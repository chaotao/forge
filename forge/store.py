"""``store`` —— **forge 自有数据**的唯一读写者（L2 §2.2 不变量 1）。

设计见 ``docs/design/03-store.md``，本模块是它的实现，只做三件事：

1. **``kind`` 表由 ``forge/schemas/`` 派生**（§6.2 / §6.3）——
   文件名 ``<kind>.schema.json`` → ``kind``；``scope`` 与**形态**由 schema 自己声明
   （``x-forge-scope`` / ``x-forge-form``）。
2. **只暴露结构语义**（``read`` / ``write`` / ``query``，§6.1）——
   不暴露文件路径与存储形态（L2 §2.5）。
3. **校验只执行、不解释**（§6.5）—— 结构层合规即可，语义归用例 / agent。

**两种形态都已实现**（§5.3）：``snapshot``（``.json``，整份原子替换）与
``log``（``.jsonl``，追加一行）。形态是 ``kind`` 的属性，**调用方不传**，
故用例的接口对形态**无感**（§6.4 的零改动承诺由此成立）。
"""
import json
import os
import tempfile
from functools import lru_cache
from typing import Any, Dict, List, Optional

from jsonschema.exceptions import best_match
from jsonschema.validators import validator_for

from .errors import StoreError
from .paths import forge_home, schemas_dir

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"

FORM_SNAPSHOT = "snapshot"
FORM_LOG = "log"

SCHEMA_SUFFIX = ".schema.json"
SCOPE_KEYWORD = "x-forge-scope"
FORM_KEYWORD = "x-forge-form"
DEFAULT_FORM = FORM_SNAPSHOT

FORMS = (FORM_SNAPSHOT, FORM_LOG)


# ---------------------------------------------------------------- kind 表
@lru_cache(maxsize=None)
def _kinds(directory: str) -> Dict[str, Dict[str, Any]]:
    """扫描 ``schemas/`` 目录，派生 ``kind`` 表（§6.2）。

    切分必须唯一：剥掉尾部 ``.schema.json`` —— 这也是 ``kind`` 名**不得含 ``.``**
    的原因（§3.4：``inbox.events.schema.json`` 切不唯一）。
    """
    table: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(directory):
        return table
    for name in sorted(os.listdir(directory)):
        if not name.endswith(SCHEMA_SUFFIX) or name.startswith("."):
            continue
        kind = name[: -len(SCHEMA_SUFFIX)]
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
        scope = schema.get(SCOPE_KEYWORD)
        if scope not in (SCOPE_GLOBAL, SCOPE_PROJECT):
            raise StoreError(
                f"{name}: 必须声明 {SCOPE_KEYWORD}，取值为 "
                f"{SCOPE_GLOBAL!r} 或 {SCOPE_PROJECT!r}"
            )
        form = schema.get(FORM_KEYWORD, DEFAULT_FORM)
        if form not in FORMS:
            raise StoreError(
                f"{name}: {FORM_KEYWORD} 只能取 {FORMS}，收到 {form!r}"
            )
        table[kind] = {
            "name": kind,
            "scope": scope,
            "form": form,
            "schema_path": path,
            "schema": schema,
        }
    return table


#: 已编译的 validator（按 ``schema_path`` 缓存）—— 见 ``_validator``。
_VALIDATORS: Dict[str, Any] = {}


def kind_names() -> List[str]:
    """已登记的全部 ``kind``（按名排序）。"""
    return sorted(_kinds(schemas_dir()))


def kind_info(kind: str) -> Dict[str, Any]:
    """单个 ``kind`` 的元信息（``name`` / ``scope`` / ``form`` / ``schema`` …）。"""
    table = _kinds(schemas_dir())
    if kind not in table:
        raise StoreError(
            f"未登记的 kind: {kind!r}（已登记：{', '.join(sorted(table)) or '(无)'}）"
        )
    return table[kind]


def _validator(kind: str) -> Any:
    """取某个 ``kind`` 的**已编译 validator**（按 ``schema_path`` 缓存）。

    ★ **为什么缓存**：``jsonschema.validate()`` 每次调用都**重建 validator 并重跑
    ``check_schema``** —— 本机实测 **1.18 ms/条**（其中 ``check_schema`` 占 **99%**），
    复用同一个实例只要 **0.0117 ms/条**（**快 100×**）。``log`` 形态**逐行**校验，
    不缓存时成本 = ``O(该 kind 的历史记录总量)``，而 ``log`` 只追加、**永不清** ⇒
    总量随时间无限涨（``workbench`` 逐项目汇总：100 个项目 ≈ 26 s，缓存后 < 1 s）。

    键取 ``schema_path`` —— 与 ``_kinds`` 的缓存**同生命周期**：``schemas/`` 目录一变
    （测试会换），路径必变 → **不会命中旧 schema 的 validator**。
    """
    info = kind_info(kind)
    key = info["schema_path"]
    validator = _VALIDATORS.get(key)
    if validator is None:
        cls = validator_for(info["schema"])
        cls.check_schema(info["schema"])  # 与 jsonschema.validate 一致：schema 自身先验一遍
        validator = cls(info["schema"])
        _VALIDATORS[key] = validator
    return validator


def reset_cache() -> None:
    """丢弃 ``kind`` 表与已编译 validator 的缓存（测试用；生产路径不需要）。"""
    _kinds.cache_clear()
    _VALIDATORS.clear()


# ------------------------------------------------------------ 落点与文件名
def _file_name(kind: str, form: str) -> str:
    return f"{kind}.jsonl" if form == FORM_LOG else f"{kind}.json"


def path_for(kind: str, project_root: Optional[str] = None) -> str:
    """某个 ``kind`` 的落盘路径（§2 / §3.4）。

    ``global`` 域 → ``<FORGE_HOME>/<kind>.json``（集中一处）；
    ``project`` 域 → ``<目标项目>/.forge/<kind>.json``（``log`` 形态为 ``.jsonl``）。
    """
    info = kind_info(kind)
    name = _file_name(kind, info["form"])
    if info["scope"] == SCOPE_GLOBAL:
        return os.path.join(forge_home(), name)
    if not project_root:
        raise StoreError(f"{kind!r} 属 project 域，必须给出 project_root")
    return os.path.join(os.path.abspath(project_root), ".forge", name)


# ------------------------------------------------------------------ 三原语
def read(kind: str, *, project_root: Optional[str] = None) -> Any:
    """读一份数据（§6.1）。**缺文件 = 空数据**，不报错（§5.3）。

    返回按形态分：``snapshot`` → 那份对象（缺 → ``{}``）；
    ``log`` → 记录列表（缺 → ``[]``）。
    """
    info = kind_info(kind)
    path = path_for(kind, project_root=project_root)
    if info["form"] == FORM_LOG:
        return _read_log(kind, path)
    return _read_snapshot(kind, path)


def write(kind: str, value: Any, *, project_root: Optional[str] = None) -> str:
    """**校验后**落盘，返回落盘路径（§6.1）。

    ``snapshot`` → 整份原子替换（临时文件 + ``rename``，§5.3）；
    ``log`` → **追加一行**（``value`` 是**一条记录**，不是整份）。
    """
    info = kind_info(kind)
    validate(kind, value)
    path = path_for(kind, project_root=project_root)
    if info["form"] == FORM_LOG:
        _append_line(path, value)
    else:
        _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def query(
    kind: str,
    field_path: str,
    value: Any,
    *,
    project_root: Optional[str] = None,
) -> List[Any]:
    """按字段取值在数据内查找（§6.1）。

    ``field_path`` 形如 ``"projects.path"`` —— **容器.字段**，容器必须是一个数组。
    对 ``log`` 形态，数据**本身**就是记录数组 → 允许省略容器：``"plan"``。
    """
    node: Any = read(kind, project_root=project_root)
    if "." in field_path:
        container_path, field = field_path.rsplit(".", 1)
        for part in container_path.split("."):
            if not isinstance(node, dict):
                return []
            node = node.get(part)
    elif isinstance(node, list):
        field = field_path
    else:
        raise StoreError(
            f"query 的 field_path 形如 'projects.path'（容器.字段），收到 {field_path!r}"
        )
    if not isinstance(node, list):
        return []
    return [
        item for item in node if isinstance(item, dict) and item.get(field) == value
    ]


# -------------------------------------------------------------------- 内部
def _read_snapshot(kind: str, path: str) -> Any:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise StoreError(f"{kind}: {path} 不是合法 JSON（{exc}）")
    validate(kind, value)
    return value


def _read_log(kind: str, path: str) -> List[Any]:
    """``log`` 形态 —— 一行一条记录（§5.3）。

    缺文件 = 空数据（``[]``）。**末行不完整 → 跳过**（追加写入的唯一坏情况）；
    其余行必须合法，否则报错（不静默吞掉数据损坏）。
    """
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    records: List[Any] = []
    last = len(lines) - 1
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            if index == last:
                break  # 末行写了一半 —— 跳过即可（§5.3）
            raise StoreError(f"{kind}: {path} 第 {index + 1} 行不是合法 JSON（{exc}）")
        validate(kind, record)
        records.append(record)
    return records


def validate(kind: str, value: Any) -> None:
    """只做**结构层**校验（§6.5）—— 不解释字段含义。

    ``log`` 形态传入的是**单条记录**（schema 描述一条）。

    ★ **与 ``jsonschema.validate(value, schema)`` 语义等价** —— 只是把 validator 的
    构建（含 ``check_schema``）**摊到一次**（见 ``_validator``）；错误的挑选仍走
    ``exceptions.best_match``，故**报错信息一字不变**。（换成 ``validator.validate``
    会改取"第一个"错误 —— 那不是等价替换。）
    """
    error = best_match(_validator(kind).iter_errors(value))
    if error is not None:
        where = "/".join(str(part) for part in error.absolute_path)
        raise StoreError(f"{kind}: 数据不合 schema（{where or '.'}）：{error.message}")


def _atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=directory, prefix=".forge-tmp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_line(path: str, record: Any) -> None:
    """追加一行 —— 不重写已有内容（§5.3）。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
