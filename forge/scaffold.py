"""``scaffold`` —— 立项目与依赖管理（L2 §3.1，用例层）。

设计见 ``docs/design/05-scaffold.md``。本模块做三件事：

1. **蓝图**（§2）—— 包内 ``forge/blueprints/``，一个蓝图 = 一个目录；
2. **落位**（§3）—— ``preview``（``resolve`` + ``decide``，纯计算不写盘）/ ``apply``（执行 + 报账）；
3. **依赖管理**（增量机制 → 入口层 ``forge sync``，``05-scaffold`` §7 T5）。

**两类落位**（§1 / §4）是骨架：

- ``payload:`` —— forge **自己写**（源在包内、写者在本模块）；
- ``skill:`` / ``aiwf:`` —— **委托上游工具安装**（``skill install`` / ``aiwf wf install``），
  落点由上游自己定，蓝图里**只关联名字**（§2.2），判定与账**都在上游**（§3.4 / §3.6）。
"""
import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

import jsonschema

from .errors import BlueprintError, DelegateError, ForgeError
from .paths import PACKAGE_DIR, blueprints_dir
from . import store

#: ``op`` 词表 —— **封闭 5 个**（§3.2 / §8-15）。``skip`` / ``refuse`` 只作用于 payload 项。
OP_WRITE = "write"
OP_COPY_TREE = "copy_tree"
OP_SKIP = "skip"
OP_REFUSE = "refuse"
OP_DELEGATE = "delegate"
OPS = (OP_WRITE, OP_COPY_TREE, OP_SKIP, OP_REFUSE, OP_DELEGATE)

#: 来源前缀词表（§2.4 / §8-★7）。
PAYLOAD = "payload"
SKILL = "skill"
AIWF = "aiwf"
SOURCE_KINDS = (PAYLOAD, SKILL, AIWF)

#: 蓝图库根目录下的模型 schema（与蓝图库**同级**，§2.5）。
BLUEPRINT_SCHEMA = "blueprint.schema.json"

#: 委托落位：来源种类 → 上游 CLI、argv 模板、要清掉的环境变量（§4.2）。
#:
#: ``scrub_env`` 只清「会整体改变**落点**」的变量 —— ``AIWF_HOME`` 会把 ``.aiwf/`` 搬走。
#: ``SKILL_LIBRARY_HOME`` / ``SKILL_LIBRARY_REGISTRY`` 只影响**素材从哪读**，保留。
DELEGATES: Dict[str, Dict[str, Any]] = {
    SKILL: {
        "program": "skill",
        "argv": ("install", "{name}"),
        "scrub_env": (),
        "lands": "上游按自己的 agent 表，落进每个启用 agent 的**项目目录**",
    },
    AIWF: {
        "program": "aiwf",
        "argv": ("wf", "install", "{name}"),
        "scrub_env": ("AIWF_HOME",),
        "lands": "<项目>/.aiwf/workflows/<id>.json",
    },
}

CHANGE_SET_VERSION = 1


# ------------------------------------------------------------------ 蓝图库
def blueprint_dir(blueprint_id: str, library: Optional[str] = None) -> str:
    return os.path.join(blueprints_dir(library), blueprint_id)


def list_blueprints(library: Optional[str] = None) -> List[str]:
    """库扫描**只认子目录**（§2.3）—— 子目录里还得有 ``blueprint.json``。"""
    base = blueprints_dir(library)
    if not os.path.isdir(base):
        return []
    names = []
    for name in sorted(os.listdir(base)):
        if name.startswith("."):
            continue
        if os.path.isfile(os.path.join(base, name, "blueprint.json")):
            names.append(name)
    return names


def load_blueprint(blueprint_id: str, library: Optional[str] = None) -> Dict[str, Any]:
    directory = blueprint_dir(blueprint_id, library)
    manifest = os.path.join(directory, "blueprint.json")
    if not os.path.isfile(manifest):
        available = ", ".join(list_blueprints(library)) or "(无)"
        raise BlueprintError(
            f"蓝图 {blueprint_id!r} 不存在（库：{blueprints_dir(library)}；可用：{available}）"
        )
    with open(manifest, encoding="utf-8") as handle:
        try:
            raw = json.load(handle)
        except ValueError as exc:
            raise BlueprintError(f"{manifest} 不是合法 JSON（{exc}）")
    _validate_blueprint(raw, directory, blueprint_id)
    return raw


def _blueprint_schema() -> Dict[str, Any]:
    """蓝图**模型**的 schema —— **只取包内那一份**（L1 约定 C2：模型是唯一事实源，随 forge 发布）。

    ``--blueprints`` / ``FORGE_BLUEPRINTS_DIR`` 只换**蓝图内容**，不换模型定义 ——
    用户的库里**不必**带 schema（带了也不生效），否则就是模型的第二事实源（L1 约定 D）。
    """
    path = os.path.join(PACKAGE_DIR, "blueprints", BLUEPRINT_SCHEMA)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _validate_blueprint(raw: Any, directory: str, blueprint_id: str) -> None:
    """只到**结构层**（§2.5）—— 不判断"这份蓝图适不适合这个项目"。"""
    schema = _blueprint_schema()
    try:
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path)
        raise BlueprintError(f"蓝图 {blueprint_id!r} 结构非法（{where or '.'}）：{exc.message}")

    if raw["id"] != blueprint_id:
        raise BlueprintError(
            f"蓝图 id {raw['id']!r} 与所在目录名 {blueprint_id!r} 不一致（§2.3）"
        )

    seen: Dict[str, str] = {}
    for index, item in enumerate(raw["items"]):
        source_kind, _, name = item["from"].partition(":")
        where = f"items[{index}] ({item['from']})"
        if source_kind == PAYLOAD:
            _payload_source(directory, name)
            normalized = _norm_rel(item["to"])
            if normalized in seen:
                raise BlueprintError(
                    f"{where}: `to` 与 {seen[normalized]} 重复（W2：一份产物一个写者）"
                )
            seen[normalized] = where
        elif not name:
            raise BlueprintError(f"{where}: 委托项只写名字，名字不能为空")


# ------------------------------------------------------------------- 落位
def preview(
    blueprint_id: str,
    target_root: str,
    *,
    force: bool = False,
    library: Optional[str] = None,
) -> Dict[str, Any]:
    """``resolve`` + ``decide`` —— **纯计算，不写盘**（§3.1 / §5.1）。"""
    blueprint = load_blueprint(blueprint_id, library)
    directory = blueprint_dir(blueprint_id, library)
    root = os.path.abspath(target_root)
    locked = _locked_paths(root)

    actions: List[Dict[str, Any]] = []
    for item in blueprint["items"]:
        source_kind, _, name = item["from"].partition(":")
        if source_kind != PAYLOAD:
            # 委托项：forge 不做判定、不知道落点（§3.4）。**无 `to`**（§3.2）。
            actions.append({"op": OP_DELEGATE, "from": item["from"]})
            continue

        source = _payload_source(directory, name)
        to = item["to"]
        normalized = _norm_rel(to)
        fall_op = OP_COPY_TREE if os.path.isdir(source) else OP_WRITE
        exists = os.path.exists(os.path.join(root, *normalized.split("/")))
        if normalized in locked:
            op = OP_SKIP
        elif exists and not force:
            op = OP_REFUSE
        else:
            op = fall_op
        # **模型里没有 `reason`**（§3.2）—— 人类可读的说明由入口层按 `op` 派生。
        actions.append({"op": op, "from": item["from"], "to": to})

    return {"version": CHANGE_SET_VERSION, "blueprint": blueprint["id"], "actions": actions}


def apply(
    change_set: Dict[str, Any],
    target_root: str,
    *,
    force: bool = False,
    library: Optional[str] = None,
) -> Dict[str, Any]:
    """**严格按序执行 + 失败即停**（§3.5）。已成功的项照常留在结果里 → 重跑幂等。"""
    root = os.path.abspath(target_root)
    directory = blueprint_dir(change_set["blueprint"], library)
    result: Dict[str, Any] = {
        "applied": [],
        "skipped": [],
        "refused": [],
        "delegated": [],
        "failed": None,
    }

    for action in change_set["actions"]:
        op = action["op"]
        if op == OP_SKIP:
            result["skipped"].append(action["to"])
            continue
        if op == OP_REFUSE:
            result["refused"].append(action["to"])
            continue
        try:
            if op in (OP_WRITE, OP_COPY_TREE):
                source_kind, _, name = action["from"].partition(":")
                if source_kind != PAYLOAD:
                    raise BlueprintError(f"{op!r} 只能用于 payload 项：{action['from']!r}")
                source = _payload_source(directory, name)
                destination = os.path.join(root, *_norm_rel(action["to"]).split("/"))
                if op == OP_WRITE:
                    _copy_file(source, destination)
                else:
                    _copy_tree(source, destination)
                result["applied"].append(
                    {
                        "path": action["to"],
                        "from": f"blueprint:{change_set['blueprint']}",
                    }
                )
            elif op == OP_DELEGATE:
                source_kind, _, name = action["from"].partition(":")
                delegate(source_kind, name, root, force=force)
                result["delegated"].append(action["from"])
            else:
                raise BlueprintError(f"未知 op：{op!r}（词表：{', '.join(OPS)}）")
        except ForgeError as exc:
            result["failed"] = {
                "from": action["from"],
                "to": action.get("to"),
                "error": str(exc),
            }
            break
    return result


def report(
    change_set: Dict[str, Any],
    result: Dict[str, Any],
    existing_lock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """把执行结果按 ``store`` 已定的模型交出（§3.6）。

    **只含 payload 项** —— 委托项的账在上游，forge 不复制（L1 约定 D）。
    已按 ``path`` 归并（重跑时"接着往下落"要靠它）+ 排序（确定性，L1 约定 G）。
    """
    existing = existing_lock or {}
    by_path: Dict[str, Dict[str, str]] = {}
    for entry in existing.get("entries", []) or []:
        if isinstance(entry, dict) and entry.get("path"):
            by_path[entry["path"]] = entry
    for entry in result.get("applied", []):
        by_path[entry["path"]] = entry
    return {
        "version": existing.get("version", 1),
        "blueprint": change_set.get("blueprint") or existing.get("blueprint") or "",
        "entries": [by_path[path] for path in sorted(by_path)],
    }


def delegate(
    source_kind: str,
    name: str,
    target_root: str,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """**委托上游安装**（§4.2）—— 不自己取件、不自己复制。

    三条约束：① **CWD 显式设为目标项目根**（两个上游都按 CWD 认项目）；
    ② 清掉会整体改道的环境变量；③ **成功判据 = 退出码**，不解析输出。
    """
    spec = DELEGATES.get(source_kind)
    if spec is None:
        raise DelegateError(
            f"未知的委托来源 {source_kind!r}（词表：{', '.join(SOURCE_KINDS)}）"
        )
    program = shutil.which(spec["program"])
    if program is None:
        raise DelegateError(
            f"找不到 `{spec['program']}` 命令 —— 委托落位要求上游 CLI 在 PATH 上"
            f"（05-scaffold §4.2 / §7 T8）"
        )
    argv = [program] + [part.format(name=name) for part in spec["argv"]]
    if force:
        argv.append("--force")
    env = os.environ.copy()
    for var in spec["scrub_env"]:
        env.pop(var, None)

    try:
        proc = subprocess.run(
            argv,
            cwd=os.path.abspath(target_root),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise DelegateError(f"无法执行 `{spec['program']}`：{exc}")
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = lines[-1] if lines else "(无输出)"
        raise DelegateError(
            f"`{spec['program']} {spec['argv'][0]} {name}` 失败"
            f"（退出码 {proc.returncode}）：{tail}"
        )
    return {"program": spec["program"], "argv": argv, "returncode": proc.returncode}


def delegate_command(source_from: str) -> str:
    """把一个委托项（``"skill:comfy-run"``）渲染成**上游命令**（``"skill install comfy-run"``）。

    给**入口层**用：``ChangeSet`` 里没有人类可读的说明（§3.2 删了 `reason`），
    提示由入口层按 `op` + 这项数据派生。这里返回的是**命令行**（数据），不是文案。
    """
    source_kind, _, name = source_from.partition(":")
    spec = DELEGATES.get(source_kind)
    if spec is None:
        return source_from
    return " ".join([spec["program"]] + [part.format(name=name) for part in spec["argv"]])


# --------------------------------------------------------------------- 内部
def _locked_paths(target_root: str) -> set:
    """``decide`` 的输入：lock 里记过的落点（§3.4）—— **只看 path，不比对内容**。"""
    lock = store.read("lock", project_root=target_root)
    paths = set()
    for entry in lock.get("entries", []) or []:
        if isinstance(entry, dict) and entry.get("path"):
            try:
                paths.add(_norm_rel(entry["path"]))
            except BlueprintError:
                continue
    return paths


def _payload_source(directory: str, relative: str) -> str:
    """``payload:`` 的寻址根是蓝图目录下的 ``payload/``（§2.4）。"""
    root = os.path.join(directory, "payload")
    parts = [part for part in str(relative).replace("\\", "/").split("/") if part]
    path = os.path.join(root, *parts)
    if not _inside(root, path):
        raise BlueprintError(f"payload 路径越出蓝图自带目录：{relative!r}")
    if not os.path.exists(path):
        raise BlueprintError(f"蓝图内不存在：payload/{relative}")
    return path


def _norm_rel(relative: str) -> str:
    """项目内相对路径的规范形 + 三条硬规则（§2.5）：不绝对、不逃逸、不以 ``.forge/`` 开头。"""
    raw = str(relative).strip()
    if not raw:
        raise BlueprintError("路径不能为空")
    if os.path.isabs(raw) or raw.replace("\\", "/").startswith("/"):
        raise BlueprintError(f"必须是项目内相对路径：{relative!r}")
    parts = [part for part in raw.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts:
        raise BlueprintError(f"路径不能为空：{relative!r}")
    if ".." in parts:
        raise BlueprintError(f"路径不得逃逸：{relative!r}")
    if parts[0] == ".forge":
        raise BlueprintError(f"不得写 .forge/（W3，归 store）：{relative!r}")
    return "/".join(parts)


def _inside(root: str, path: str) -> bool:
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return path == root or path.startswith(root + os.sep)


def _copy_file(source: str, destination: str) -> None:
    """原子替换落盘（§3.5，同 ``store`` 的 snapshot 写法）。"""
    directory = os.path.dirname(destination) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".forge-tmp-")
    try:
        with os.fdopen(handle, "wb") as out, open(source, "rb") as inp:
            shutil.copyfileobj(inp, out)
        os.replace(tmp, destination)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copy_tree(source: str, destination: str) -> None:
    """逐文件按 ``_copy_file`` 落（§3.5）—— 空目录也建出来。"""
    for current, _dirnames, filenames in os.walk(source):
        relative = os.path.relpath(current, source)
        target_dir = destination if relative == "." else os.path.join(destination, relative)
        os.makedirs(target_dir, exist_ok=True)
        for filename in sorted(filenames):
            _copy_file(os.path.join(current, filename), os.path.join(target_dir, filename))
