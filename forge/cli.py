"""入口层 —— ``forge`` CLI（L2 §1：人是唯一入口，数据访问层不直接面向人）。

六个命令族：

- ``forge new <路径>`` —— 蓝图落位（``scaffold``）+ 登记受管（``store``）；
- ``forge sync <路径>`` —— 把**已受管**项目对齐到蓝图（依赖管理的入口，``05-scaffold`` §7 T5）；
- ``forge plan …`` —— 计划 / 任务 / 过程记录（``plan`` 用例，``06-plan`` §2.4）；
- ``forge inbox …`` —— 项目里的念头：捕获 / 整理 / 交接（``inbox`` 用例，``07-inbox`` §2.4）；
- ``forge status`` —— **本项目**一眼状态（``workbench`` 用例，项目根 = CWD）；
- ``forge workbench`` —— **跨项目**总览（``workbench`` 用例，不依赖 CWD）。

后四个**项目根取当前工作目录**（``workbench`` 例外 —— 它看的是全局清单）。

**渲染在本层**（L1 约定 B：文案不出模块）；``--dry-run`` 是 ``preview`` 原语的命令表面，
落盘前可停手看一眼。读视图给**结构化文本** + ``--json`` 两种输出（H2：四个视图一致）。
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import __version__, inbox, plan, scaffold, store, workbench
from .errors import ForgeError

DEFAULT_BLUEPRINT = "minimal"

#: ``Project.stage`` 的取值。**词表未定**（``03-store`` §7 S3）→ 现由入口层给定值。
DEFAULT_STAGE = "scaffolded"

_OP_LABEL = {
    scaffold.OP_WRITE: "写入",
    scaffold.OP_COPY_TREE: "写入",
    scaffold.OP_SKIP: "跳过",
    scaffold.OP_REFUSE: "拒绝",
    scaffold.OP_DELEGATE: "委托",
}

#: 人类可读的说明**由入口层按 `op` 派生**（``05-scaffold`` §3.2 —— 模型里没有 `reason`）。
_HINT = {
    scaffold.OP_SKIP: "目标已存在且在 lock 里",
    scaffold.OP_REFUSE: "目标已存在且不在 lock 里",
}

_TALLY_ORDER = (
    scaffold.OP_WRITE,
    scaffold.OP_COPY_TREE,
    scaffold.OP_DELEGATE,
    scaffold.OP_SKIP,
    scaffold.OP_REFUSE,
)

#: ``plan`` 捷径 —— 命令名 → ``plan`` 模块里的函数名（§2.5）。
_PLAN_SHORTCUTS = ("claim", "block", "reject", "done", "drop")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge", description="forge —— 面向 AI 协作项目的一站式工具"
    )
    parser.add_argument("--version", action="version", version=f"forge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="立一个新项目：蓝图落位 + 登记受管")
    new.add_argument("path", help="目标项目路径")
    new.add_argument("--alias", default=None, help="受管清单里的短名，缺省取目录名")
    _add_scaffold_options(new, blueprint_help="蓝图 id（缺省 minimal）")
    new.set_defaults(func=cmd_new)

    sync = sub.add_parser(
        "sync", help="把已受管的项目对齐到蓝图（只增不删；换蓝图也走这里）"
    )
    sync.add_argument("path", help="**已受管**的项目路径")
    _add_scaffold_options(sync, blueprint_help="蓝图 id（缺省取受管清单里登记的那个）")
    sync.set_defaults(func=cmd_sync)

    _build_plan_parser(sub)
    _build_inbox_parser(sub)
    _build_views_parser(sub)
    return parser


def _add_scaffold_options(parser: argparse.ArgumentParser, *, blueprint_help: str) -> None:
    """``new`` 与 ``sync`` 共用的落位参数 —— 两者走**同一条链路**（§6 / §7 T5）。"""
    parser.add_argument("--blueprint", default=None, help=blueprint_help)
    parser.add_argument(
        "--blueprints", default=None, help="蓝图库目录 —— 替换语义，不叠加（§2.3）"
    )
    parser.add_argument("--force", action="store_true", help="把 refuse 改判为落；对委托项透传给上游")
    parser.add_argument("--dry-run", action="store_true", help="只算不落盘（预演）")
    parser.add_argument("--json", action="store_true", dest="as_json", help="以 JSON 输出")


def _build_plan_parser(sub) -> None:
    """``forge plan …`` —— 写 7 动词 / 捷径 5 个 + 读 4 视图（``06-plan`` §2.4）。"""
    root = sub.add_parser("plan", help="项目内的计划 / 任务 / 过程记录（项目根 = CWD）")
    verbs = root.add_subparsers(dest="plan_command", required=True)

    add = verbs.add_parser("add", help="新建一份计划（<plan>）或一个任务（<plan>.<task>）")
    add.add_argument("address", help="<plan> 或 <plan>.<task>")
    add.add_argument("--title", required=True, help="显示短名（计划 / 任务都要）")
    add.add_argument("--goal", default=None, help="目标陈述（**建计划必给**）")
    add.add_argument("--criteria", action="append", default=[], help="判据（可重复；**建计划至少一条**）")
    add.add_argument("--done-when", dest="done_when", default=None, help="任务的验收条件")
    add.add_argument("--owner", default=None, choices=list(plan.OWNERS), help="human | agent")
    add.add_argument("--deps", action="append", default=[], help="前置任务：<plan>.<task> 或计划内 id（同计划内；可重复）")
    add.add_argument("--note", default=None, help="附一句话进轨迹")
    add.add_argument("--json", action="store_true", dest="as_json")
    add.set_defaults(func=cmd_plan)

    setter = verbs.add_parser("set", help="兜底 —— 其余一切改动（至少给一项）")
    setter.add_argument("address")
    setter.add_argument("--status", default=None, help="open|reached|dropped（计划）· todo|doing|done|blocked（任务）")
    setter.add_argument("--owner", default=None, choices=list(plan.OWNERS))
    setter.add_argument("--note", default=None)
    setter.add_argument("--goal", default=None, help="只对计划")
    setter.add_argument("--title", default=None)
    setter.add_argument("--json", action="store_true", dest="as_json")
    setter.set_defaults(func=cmd_plan)

    for name, help_text in (
        ("claim", "认领（→ doing + owner: agent）"),
        ("block", "卡住（→ blocked）—— --note 必填"),
        ("reject", "打回（→ doing）—— --note 必填"),
        ("done", "完成 —— task → done；计划 → reached"),
        ("drop", "放弃一份计划（→ dropped）—— --note 必填"),
    ):
        shortcut = verbs.add_parser(name, help=help_text)
        shortcut.add_argument("address", help="<plan> 或 <plan>.<task>")
        shortcut.add_argument("--note", default=None)
        shortcut.add_argument("--json", action="store_true", dest="as_json")
        shortcut.set_defaults(func=cmd_plan)

    show = verbs.add_parser("show", help="看：全局列表 / 单份计划 / 单任务（交接包）")
    show.add_argument("address", nargs="?", default=None, help="省略 → 列表")
    show.add_argument("--status", default=None, help="列表过滤：open|reached|dropped")
    show.add_argument("--tail", type=int, default=None, help="单任务轨迹只取尾部 N 条")
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(func=cmd_plan)

    log = verbs.add_parser("log", help="跨计划时间线（默认尾部 20 条）")
    log.add_argument("--plan", default=None, help="只看某一份计划")
    log.add_argument("--tail", type=int, default=None)
    log.add_argument("--all", action="store_true", dest="show_all", help="全量（慎用：无界）")
    log.add_argument("--json", action="store_true", dest="as_json")
    log.set_defaults(func=cmd_plan)


def _build_inbox_parser(sub) -> None:
    """``forge inbox …`` —— 写 5 动词 + 读 2 视图（``07-inbox`` §2.4）。

    ``--full`` / ``--all`` 是 ``list`` 的**变体**，不是一个独立视图（H2：表面一致）。
    """
    root = sub.add_parser("inbox", help="项目里的念头：捕获 / 整理 / 交接（项目根 = CWD）")
    verbs = root.add_subparsers(dest="inbox_command", required=True)

    add = verbs.add_parser("add", help="记一条念头（只有内容必填）")
    add.add_argument("text", help="一句话：是什么念头")
    add.add_argument("--tag", action="append", default=None, help="自由标签（可重复）")
    add.add_argument("--source", default=None, help="出处：哪个计划 / 哪次对话（纯文本，不校验）")
    add.add_argument("--note", default=None, help="补充说明（只有 show 才看到）")
    add.add_argument("--json", action="store_true", dest="as_json")
    add.set_defaults(func=cmd_inbox)

    merge = verbs.add_parser("merge", help="整理：把 N 条 open 攒成 1 条主题（源 ≥ 2）")
    merge.add_argument("ids", nargs="+", help="源条目（≥ 2，且必须都是 open）")
    merge.add_argument("--text", required=True, help="整合表述（agent 给 —— 引擎不能替它拼原文）")
    merge.add_argument("--tag", action="append", default=None)
    merge.add_argument("--source", default=None)
    merge.add_argument("--note", default=None)
    merge.add_argument("--json", action="store_true", dest="as_json")
    merge.set_defaults(func=cmd_inbox)

    setter = verbs.add_parser("set", help="兜底 —— 改笔误 / 补标签 / 改出处（不动状态）")
    setter.add_argument("id")
    setter.add_argument("--text", default=None)
    setter.add_argument("--tag", action="append", default=None)
    setter.add_argument("--source", default=None)
    setter.add_argument("--note", default=None)
    setter.add_argument("--json", action="store_true", dest="as_json")
    setter.set_defaults(func=cmd_inbox)

    for name, help_text in (
        ("hand", "交接出去（→ 已交接）—— --to 必填"),
        ("drop", "丢弃（→ 丢弃）—— --to 必填"),
    ):
        shortcut = verbs.add_parser(name, help=help_text)
        shortcut.add_argument("id")
        shortcut.add_argument(
            "--to", dest="target", required=True, help="去向：交给谁 / 纳入哪 / 为何丢弃"
        )
        shortcut.add_argument("--json", action="store_true", dest="as_json")
        shortcut.set_defaults(func=cmd_inbox)

    listing = verbs.add_parser("list", help="看还悬着什么（默认只给未处理）")
    listing.add_argument(
        "--full", action="store_true", help="text 给全文（整理专用；仍不给 note）"
    )
    listing.add_argument(
        "--all", action="store_true", dest="show_all", help="连已归档一起给（只给标题行）"
    )
    listing.add_argument("--tag", default=None, help="按标签过滤")
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(func=cmd_inbox)

    show = verbs.add_parser("show", help="看一条的全文（含出处 / 备注 / 去向）")
    show.add_argument("id")
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(func=cmd_inbox)


def _build_views_parser(sub) -> None:
    """两个**读视图**出口（``08-workbench`` §2.3 / §10-★13）—— 共用同一段渲染。

    - ``forge status`` —— **本项目**一眼状态（项目根 = CWD，同 ``plan`` / ``inbox``）；
    - ``forge workbench`` —— **跨项目**总览（**不依赖** CWD，从任意目录跑）。
    """
    status = sub.add_parser(
        "status", help="本项目一眼状态：计划 / 念头 / aiwf 三个分区（项目根 = CWD）"
    )
    status.add_argument("--json", action="store_true", dest="as_json")
    status.set_defaults(func=cmd_status)

    board = sub.add_parser(
        "workbench", help="跨项目总览：手上几个项目分别到哪了（不依赖 CWD）"
    )
    board.add_argument("--json", action="store_true", dest="as_json")
    board.set_defaults(func=cmd_workbench)


# ------------------------------------------------------------------- new
def cmd_new(args: argparse.Namespace) -> int:
    """``forge new`` —— 立一个新项目（§6）。

    **已受管路径按蓝图比对**（v1.0 改判）：蓝图一致（``--blueprint`` 未指定时**取登记值**）
    → **幂等 no-op**（一个字节都不写）；不一致 → 拒绝并指向 ``forge sync`` ——
    换蓝图**不是** ``new`` 的职责（§6 / §7 T5）。
    """
    target = _target(args.path)
    managed = store.query("projects", "projects.path", target)
    if managed:
        registered = managed[0]
        stored = registered.get("blueprint") or DEFAULT_BLUEPRINT
        if args.blueprint and args.blueprint != stored:
            raise ForgeError(
                f"{target} 已在受管清单里（alias={registered.get('alias')} · 蓝图 {stored}）"
                f" —— 换蓝图用 `forge sync {args.path} --blueprint {args.blueprint}`"
            )
        return _relocate(
            target, stored, args, alias=registered.get("alias"), register=False
        )
    return _relocate(
        target, args.blueprint or DEFAULT_BLUEPRINT, args, alias=None, register=True
    )


def cmd_sync(args: argparse.Namespace) -> int:
    """``forge sync`` —— 把**已受管**的项目对齐到蓝图（§7 T5 的依赖管理入口）。

    与 ``new`` 走**同一条** ``preview`` / ``apply`` / 报账链路，故幂等；差别只有三处：
    ① 目标必须**已受管**（否则提示用 ``new``）；② 蓝图缺省**取登记值**；
    ③ 换蓝图由**显式** ``--blueprint`` 触发。**只增不删**（对齐不破坏，§3.4）。
    """
    target = _target(args.path)
    managed = store.query("projects", "projects.path", target)
    if not managed:
        raise ForgeError(
            f"{target} 不在受管清单里 —— sync 只做增量；先 `forge new {args.path}`"
        )
    if not os.path.isdir(target):
        raise ForgeError(f"受管项目的目录不存在：{target} —— 要人介入")

    registered = managed[0]
    stored = registered.get("blueprint") or DEFAULT_BLUEPRINT
    blueprint = args.blueprint or stored
    code = _relocate(
        target, blueprint, args, alias=registered.get("alias"), register=False
    )
    if code == 0 and not args.dry_run and blueprint != stored:
        _update_registered(target, blueprint)
    return code


def _target(raw: str) -> str:
    target = os.path.abspath(os.path.expanduser(raw))
    if os.path.exists(target) and not os.path.isdir(target):
        raise ForgeError(f"目标已存在且不是目录：{target}")
    return target


def _relocate(
    target: str,
    blueprint: str,
    args: argparse.Namespace,
    *,
    alias: Optional[str],
    register: bool,
) -> int:
    """``new`` 与 ``sync`` 共用的落位链路（§6 / §7 T5）。"""
    change_set = scaffold.preview(
        blueprint, target, force=args.force, library=args.blueprints
    )
    if args.dry_run:
        _emit_preview(args, change_set, target)
        return 0

    result = scaffold.apply(
        change_set, target, force=args.force, library=args.blueprints
    )

    # 报账：**只含 payload 项**（§3.6）。失败时已成功的项照常落账 → 重跑能接着往下落。
    if result["applied"]:
        existing = store.read("lock", project_root=target)
        store.write(
            "lock",
            scaffold.report(change_set, result, existing),
            project_root=target,
        )

    failed = result["failed"]
    if not failed and register:
        alias = _register(target, args, change_set)

    if args.as_json:
        payload = dict(result)
        payload.update(
            {"target": target, "blueprint": change_set["blueprint"], "alias": alias}
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _emit_result(result, target, change_set["blueprint"])
        if failed:
            print(f"forge: 落位停在 {failed['from']} —— {failed['error']}", file=sys.stderr)
        elif register:
            print(f"已登记受管：{alias}")

    return 1 if failed else 0


def _update_registered(target: str, blueprint: str) -> None:
    """换蓝图后同步受管清单里的 ``blueprint``。

    ``Project.blueprint`` 是**单值** —— 多蓝图并存（``add``）属 §7 **T2**，本轮不动模型；
    完整来源**没丢**：每个 ``Entry.from`` 各自记着 ``blueprint:<id>``（§3.6）。
    """
    projects = store.read("projects")
    items: List[Dict[str, Any]] = []
    for item in projects.get("projects", []) or []:
        if item.get("path") == target:
            item = dict(item)
            item["blueprint"] = blueprint
        items.append(item)
    value: Dict[str, Any] = {"version": projects.get("version", 1), "projects": items}
    if projects.get("focus"):
        value["focus"] = projects["focus"]
    store.write("projects", value)


def _register(target: str, args: argparse.Namespace, change_set: Dict[str, Any]) -> str:
    """落位成功之后才登记受管（§6 第 4/5 步）—— 未建成的项目不进清单。"""
    projects = store.read("projects")
    items: List[Dict[str, Any]] = list(projects.get("projects", []) or [])
    alias = args.alias or _derive_alias(
        os.path.basename(target.rstrip("\\/")), {item.get("alias") for item in items}
    )
    if any(item.get("alias") == alias for item in items):
        raise ForgeError(f"alias {alias!r} 已被占用，请用 --alias 另给一个")
    items.append(
        {
            "path": target,
            "alias": alias,
            "blueprint": change_set.get("blueprint") or args.blueprint,
            "stage": DEFAULT_STAGE,
            "registered_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    )
    value: Dict[str, Any] = {"version": projects.get("version", 1), "projects": items}
    if projects.get("focus"):
        value["focus"] = projects["focus"]
    store.write("projects", value)
    return alias


def _derive_alias(base: str, taken: set) -> str:
    """``alias`` 生成规则**未定**（``03-store`` §7 S2）→ 临时取值：目录名，重名加序号。"""
    stem = re.sub(r"[\s/\\:]+", "-", base).strip("-") or "project"
    alias, index = stem, 2
    while alias in taken:
        alias = f"{stem}-{index}"
        index += 1
    return alias


# ------------------------------------------------------------------ plan
def cmd_plan(args: argparse.Namespace) -> int:
    root = os.getcwd()
    verb = args.plan_command
    if verb == "add":
        return _plan_add(args, root)
    if verb == "set":
        return _plan_set(args, root)
    if verb in _PLAN_SHORTCUTS:
        return _plan_shortcut(args, root, verb)
    if verb == "show":
        return _plan_show(args, root)
    if verb == "log":
        return _plan_log(args, root)
    raise ForgeError(f"未知的 plan 子命令：{verb}")


def _plan_add(args: argparse.Namespace, root: str) -> int:
    _plan_id, task_id = plan.parse_address(args.address)
    if task_id:
        if args.goal is not None or args.criteria:
            raise ForgeError("任务不支持 --goal / --criteria（它们属于计划）—— 用 --done-when")
        result = plan.add_task(
            root,
            args.address,
            title=args.title,
            done_when=args.done_when,
            owner=args.owner,
            deps=args.deps,
            note=args.note,
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"已加任务 {args.address} · {result['title']} · {result['status']}")
        return 0

    if not args.goal:
        raise ForgeError("建计划必须给 --goal（目标陈述，§3.1）")
    if not args.criteria:
        raise ForgeError("建计划至少要一条 --criteria（判据，§10-13）")
    result = plan.add_plan(
        root, args.address, title=args.title, goal=args.goal, criteria=args.criteria, note=args.note
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"已加计划 {result['id']} · {result['title']} · {result['status']}")
    return 0


def _plan_set(args: argparse.Namespace, root: str) -> int:
    if all(
        value is None
        for value in (args.status, args.owner, args.note, args.goal, args.title)
    ):
        raise ForgeError("set 至少给一项：--status / --owner / --note / --goal / --title")
    result = plan.set_target(
        root,
        args.address,
        status=args.status,
        owner=args.owner,
        note=args.note,
        goal=args.goal,
        title=args.title,
    )
    _emit_change(result, args.as_json)
    return 0


def _plan_shortcut(args: argparse.Namespace, root: str, verb: str) -> int:
    if verb == "claim":
        result = plan.claim(root, args.address, note=args.note)
    elif verb == "block":
        result = plan.block(root, args.address, note=args.note)
    elif verb == "reject":
        result = plan.reject(root, args.address, note=args.note)
    elif verb == "done":
        result = plan.done(root, args.address, note=args.note)
    else:  # drop
        result = plan.drop(root, args.address, note=args.note)
    _emit_change(result, args.as_json)
    return 0


def _plan_show(args: argparse.Namespace, root: str) -> int:
    if not args.address:
        rows = plan.list_plans(root, status=args.status)
        if args.as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _emit_plan_list(rows, args.status)
        return 0

    plan_id, task_id = plan.parse_address(args.address)
    if task_id:
        payload = plan.show_task(root, plan_id, task_id, tail=args.tail)
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            _emit_task(payload)
        return 0

    payload = plan.show_plan(root, plan_id)
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _emit_plan_block(payload)
    return 0


def _plan_log(args: argparse.Namespace, root: str) -> int:
    events = plan.timeline(
        root, plan=args.plan, tail=args.tail, show_all=args.show_all
    )
    if args.as_json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
    else:
        _emit_timeline(events, args.plan, args.show_all)
    return 0


# --------------------------------------------------------- plan 渲染
def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(text))


def _ljust(text: str, width: int) -> str:
    return str(text) + " " * max(0, width - _display_width(text))


def _emit_change(result: Dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    head = f"{result['op']} {result['address']}"
    event = result.get("event") or {}
    if result.get("from") is not None and result.get("to") is not None:
        head += f"  {result['from']} → {result['to']}"
    elif result.get("to") is not None:
        head += f"  → {result['to']}"
    else:
        head += "  （轨迹已追记）"
    if event.get("note"):
        head += f"  · {event['note']}"
    print(head)


def _emit_plan_list(rows: List[Dict[str, Any]], status: Optional[str]) -> None:
    suffix = f"（--status {status}）" if status else ""
    if not rows:
        print(f"还没有计划{suffix} —— 建一份计划：forge plan add <计划> --title … --goal … --criteria …")
        return
    id_w = max(_display_width(row["id"]) for row in rows)
    title_w = max(_display_width(row["title"]) for row in rows)
    print(f"计划 {len(rows)} 个{suffix}")
    for row in rows:
        progress = f"{row['tasks_done']}/{row['tasks_total']}"
        print(
            f"  {_ljust(row['id'], id_w)}  {_ljust(row['title'], title_w)}  "
            f"{_ljust(row['status'], 8)} {progress}"
        )


def _emit_plan_block(block: Dict[str, Any]) -> None:
    print(f"{block['id']} · {block['title']} · {block['status']}")
    print(f"  goal  {block['goal']}")
    if block["criteria"]:
        print("  判据")
        for criterion in block["criteria"]:
            tail = f"  （{criterion['check']}）" if criterion.get("check") else ""
            print(f"    - {criterion['text']}{tail}")
    tasks = block["tasks"]
    if not tasks:
        print("  任务 0 个 —— 加任务：forge plan add <计划>.<任务> --title …")
        return
    print(f"  任务 {len(tasks)} 个")
    id_w = max(_display_width(task["id"]) for task in tasks)
    title_w = max(_display_width(task["title"]) for task in tasks)
    for task in tasks:
        extras = []
        if task.get("owner"):
            extras.append(f"owner={task['owner']}")
        if task.get("done_when"):
            extras.append(task["done_when"])
        tail = f"  · {'  · '.join(extras)}" if extras else ""
        print(
            f"    {_ljust(task['status'], 8)} {_ljust(task['id'], id_w)}  "
            f"{_ljust(task['title'], title_w)}{tail}"
        )


def _emit_task(packet: Dict[str, Any]) -> None:
    address = plan.format_address(packet["plan"], packet["id"])
    head = f"{address} · {packet['title']} · {packet['status']}"
    if packet.get("owner"):
        head += f" · owner={packet['owner']}"
    print(head)
    if packet.get("done_when"):
        print(f"  done_when  {packet['done_when']}")
    if packet.get("deps"):
        print(f"  deps  {', '.join(packet['deps'])}")
    events = packet["events"]
    print(f"  轨迹 {len(events)} 条")
    for event in events:
        print("    " + _render_event(event))


def _emit_timeline(
    events: List[Dict[str, Any]], only_plan: Optional[str], show_all: bool
) -> None:
    scope = f"（--plan {only_plan}）" if only_plan else ""
    if not events:
        print(f"还没有过程记录{scope}")
        return
    amount = "全量" if show_all else f"尾部 {len(events)} 条"
    print(f"时间线{scope} · {amount}")
    addresses = []
    for event in events:
        if event.get("id") == event.get("plan"):
            addresses.append(str(event.get("plan", "")))  # 计划事件：不必重复写成 login.login
        else:
            addresses.append(f"{event.get('plan', '')}.{event.get('id', '')}")
    addr_w = max(_display_width(address) for address in addresses)
    op_w = max(_display_width(event.get("op", "")) for event in events)
    for address, event in zip(addresses, events):
        print(
            f"  {event.get('ts', '')}  {_ljust(address, addr_w)}  "
            f"{_ljust(event.get('op', ''), op_w)}  {_render_event(event, with_ts=False)}"
        )


def _render_event(event: Dict[str, Any], *, with_ts: bool = True) -> str:
    parts = []
    if with_ts and event.get("ts"):
        parts.append(event["ts"])
    if event.get("from") is not None and event.get("to") is not None:
        parts.append(f"{event['from']} → {event['to']}")
    elif event.get("to") is not None:
        parts.append(f"→ {event['to']}")
    elif event.get("from") is not None:
        parts.append(f"{event['from']} →")
    if event.get("note"):
        parts.append(event["note"])
    return "  ".join(parts) if parts else "—"


# ----------------------------------------------------------------- inbox
#: 状态的**渲染层**中文化（``07-inbox`` §4：落盘英文 token，渲染出中文）。
_STATUS_LABEL = {
    inbox.STATUS_OPEN: "未处理",
    inbox.STATUS_HANDED: "已交接",
    inbox.STATUS_DROPPED: "丢弃",
}


def cmd_inbox(args: argparse.Namespace) -> int:
    """``forge inbox …`` —— 项目根取 CWD（与 ``plan`` 一致，§2.4）。"""
    root = os.getcwd()
    verb = args.inbox_command

    if verb == "add":
        item = inbox.add(
            root, args.text, tags=args.tag, source=args.source, note=args.note
        )
        if args.as_json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            print(f"已记 {item['id']} · {item['text']}")
        return 0

    if verb == "merge":
        result = inbox.merge(
            root,
            args.ids,
            text=args.text,
            tags=args.tag,
            source=args.source,
            note=args.note,
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            product = result["item"]
            print(
                f"已合并 {' + '.join(result['sources'])} → "
                f"{product['id']} · {product['text']}"
            )
            print(f'  {len(result["sources"])} 条源已归档（target="并入 {product["id"]}"）')
        return 0

    if verb == "set":
        result = inbox.set_fields(
            root,
            args.id,
            text=args.text,
            tags=args.tag,
            source=args.source,
            note=args.note,
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"set {result['id']} · 改了 {'、'.join(result['changed'])}")
        return 0

    if verb in ("hand", "drop"):
        result = (inbox.hand if verb == "hand" else inbox.drop)(
            root, args.id, target=args.target
        )
        if args.as_json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"{verb} {result['id']}  {_status_label(result['from'])} → "
                f"{_status_label(result['to'])}  · {result['target']}"
            )
        return 0

    if verb == "list":
        rows = inbox.list_items(
            root, full=args.full, show_all=args.show_all, tag=args.tag
        )
        if args.as_json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _emit_inbox_list(rows, args)
        return 0

    if verb == "show":
        item = inbox.show_item(root, args.id)
        if args.as_json:
            print(json.dumps(item, ensure_ascii=False, indent=2))
        else:
            _emit_inbox_item(item)
        return 0

    raise ForgeError(f"未知的 inbox 子命令：{verb}")


def _status_label(status: Optional[str]) -> str:
    return _STATUS_LABEL.get(status, status or "—")


def _emit_inbox_list(rows: List[Dict[str, Any]], args: argparse.Namespace) -> None:
    scope = []
    if args.full:
        scope.append("--full")
    if args.show_all:
        scope.append("--all")
    if args.tag:
        scope.append(f"--tag {args.tag}")
    suffix = f"（{' '.join(scope)}）" if scope else ""
    if not rows:
        print(f'inbox 空{suffix} —— 记一条：forge inbox add "一句话"')
        return
    print(f"念头 {len(rows)} 条{suffix}")
    id_w = max(_display_width(row["id"]) for row in rows)
    status_w = max(_display_width(_status_label(row["status"])) for row in rows)
    for row in rows:
        tags = f"  #{'  #'.join(row['tags'])}" if row.get("tags") else ""
        print(
            f"  {_ljust(row['id'], id_w)}  "
            f"{_ljust(_status_label(row['status']), status_w)}  "
            f"{row['text']}{tags}  · {row['created']}"
        )


def _emit_inbox_item(item: Dict[str, Any]) -> None:
    print(
        f"{item['id']} · {_status_label(item.get('status'))} · {item.get('created', '')}"
    )
    print(f"  {item['text']}")
    if item.get("tags"):
        print(f"  tags    {' '.join('#' + tag for tag in item['tags'])}")
    if item.get("source"):
        print(f"  source  {item['source']}")
    if item.get("note"):
        print(f"  note    {item['note']}")
    if item.get("target"):
        print(f"  target  {item['target']}")


# ------------------------------------------------------- scaffold 渲染
def _action_hint(action: Dict[str, Any]) -> str:
    """人类可读的说明**由入口层按 `op` 派生**（§3.2 —— 模型里没有 `reason`）。"""
    return _HINT.get(action["op"], "")


def _render_action(action: Dict[str, Any]) -> str:
    """预览里的一行 —— 委托项直接展示**上游命令**（`from` 只是标识，命令里已含名字）。"""
    op = action["op"]
    label = _OP_LABEL.get(op, op)
    if op == scaffold.OP_DELEGATE:
        return f"  {label:<4} {scaffold.delegate_command(action['from'])}"
    shown = action.get("to") or action["from"]
    hint = _action_hint(action)
    suffix = f"  （{hint}）" if hint else ""
    return f"  {label:<4} {shown}{suffix}"


def _emit_preview(args: argparse.Namespace, change_set: Dict[str, Any], target: str) -> None:
    if args.as_json:
        print(json.dumps(change_set, ensure_ascii=False, indent=2))
        return
    print(f"预演（未落盘）· 蓝图 {change_set['blueprint']} · 目标 {target}")
    for action in change_set["actions"]:
        print(_render_action(action))
    tally: Dict[str, int] = {}
    for action in change_set["actions"]:
        tally[action["op"]] = tally.get(action["op"], 0) + 1
    print(_tally(tally))


def _emit_result(result: Dict[str, Any], target: str, blueprint: str) -> None:
    print(f"已落位 · 蓝图 {blueprint} · 目标 {target}")
    for entry in result["applied"]:
        print(f"  {_OP_LABEL[scaffold.OP_WRITE]:<4} {entry['path']}")
    for entry in result["delegated"]:
        print(f"  {_OP_LABEL[scaffold.OP_DELEGATE]:<4} {scaffold.delegate_command(entry)}")
    for path in result["skipped"]:
        print(f"  {_OP_LABEL[scaffold.OP_SKIP]:<4} {path}")
    for path in result["refused"]:
        print(f"  {_OP_LABEL[scaffold.OP_REFUSE]:<4} {path}  （已存在且不在 lock 里）")
    tally = {
        scaffold.OP_WRITE: len(result["applied"]),
        scaffold.OP_DELEGATE: len(result["delegated"]),
        scaffold.OP_SKIP: len(result["skipped"]),
        scaffold.OP_REFUSE: len(result["refused"]),
    }
    print(_tally(tally))


def _tally(counts: Dict[str, int]) -> str:
    parts = [f"{_OP_LABEL[op]} {counts[op]}" for op in _TALLY_ORDER if counts.get(op)]
    return " · ".join(parts) if parts else "（无变更）"


# ------------------------------------------------------------- workbench
#: 分区标签的显示宽度（`plan` 4 / `inbox` 5 / `aiwf` 4）—— 取最大对齐（§4.5）。
_SECTION_WIDTH = 5
#: 分区**续行**的缩进 = 内容起点（2 空格 + 标签列 + 2 空格）。
_SECTION_INDENT = "  " + " " * _SECTION_WIDTH + "  "


def cmd_status(args: argparse.Namespace) -> int:
    """``forge status`` —— **本项目**视图（项目根 = CWD，同 ``plan`` / ``inbox``）。"""
    view = workbench.collect_project(os.getcwd())
    if args.as_json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        _emit_project(view, with_identity=False, with_recent=True)
    return 0


def cmd_workbench(args: argparse.Namespace) -> int:
    """``forge workbench`` —— **全局**总览（不依赖 CWD，从任意目录跑）。"""
    view = workbench.collect()
    if args.as_json:
        print(json.dumps(view, ensure_ascii=False, indent=2))
    else:
        _emit_workbench(view)
    return 0


def _emit_workbench(view: Dict[str, Any]) -> None:
    projects = view["projects"]
    if not projects:
        print("还没有受管项目 —— 立一个：forge new <路径>")
        return
    print(f"{len(projects)} 个项目 · 焦点 {view.get('focus') or '无'}")
    # ★ 「最近动态」置顶（主人 2026-10-11：按时间顺序看最近在进行中的）——
    #   各项目自带 ``recent``，这里**读时合成**一条全局时间线（不重复存，L1 D / H4）。
    _emit_recent(workbench.merge_recent(projects))
    for status in projects:
        print()
        _emit_project(status, with_identity=True)


def _emit_project(status: Dict[str, Any], *, with_identity: bool, with_recent: bool = False) -> None:
    """**一段** —— 两个出口**共用**（L1 约定 **D1**：一处渲染）。

    ``with_identity=True``（全局）：首行 ``alias · blueprint · path``；
    ``False``（本项目，§10-**a**）：**去掉身份行**，只留路径（未受管时标注）。

    ``with_recent=True``（**只有本项目视图**）：在身份行下先给一段「最近动态」。
    全局视图的最近动态**置顶合成**（``_emit_workbench``），逐项目**不再重复**。
    """
    if with_identity:
        print(
            " · ".join(
                str(part or "?")
                for part in (
                    status.get("alias"),
                    status.get("blueprint"),
                    status.get("path"),
                )
            )
        )
    else:
        path = status.get("path") or "?"
        print(path if status.get("alias") else f"{path}（未受管）")

    # ``forge status`` 的「最近动态」（本项目）—— 全局视图把它提到顶部。
    if with_recent and status.get("recent"):
        _emit_recent(status["recent"])
    print(_section_line("plan", _plan_line(status["plan"])))
    for line in _plan_dynamic_lines(status["plan"]):
        print(line)
    print(_section_line("inbox", _inbox_line(status["inbox"])))
    for line in _inbox_recent_lines(status["inbox"]):
        print(line)
    for line in _aiwf_lines(status["aiwf"]):
        print(line)


# ------------------------------------------------------------- 最近动态
#: 「最近动态」里时间戳列的显示宽度（``YYYY-MM-DD HH:MM``）。
_RECENT_TS_WIDTH = 16
#: 时间线里实例标题 / 文案的**显示宽**上限 —— 超过即截断（H4）。
_RECENT_TEXT_WIDTH = 44
#: 时间线里 ``plan`` / ``inbox`` 正文的**显示宽**上限。
_RECENT_BODY_WIDTH = 56


def _emit_recent(entries: List[Dict[str, Any]]) -> None:
    """「最近动态」—— 跨项目把带时间戳的事实**按时间降序**排一段（§4.6 · 主人 2026-10-11）。"""
    if not entries:
        return
    proj_w = max(_display_width(entry.get("project") or "?") for entry in entries)
    print("最近动态")
    for entry in entries:
        print(
            f"  {_ljust(_short_ts(entry.get('ts')), _RECENT_TS_WIDTH)}  "
            f"{_ljust(entry.get('project') or '?', proj_w)}  {_recent_body(entry)}"
        )


def _short_ts(ts: Optional[str]) -> str:
    """把时间戳压到 ``YYYY-MM-DD HH:MM`` —— 只有日期（``inbox``）就照给。"""
    if not ts:
        return "—"
    text = str(ts)
    return text[:16].replace("T", " ") if "T" in text else text


def _clip(text: str, width: int) -> str:
    """按**显示宽**截断（中日韩算 2 列）—— 与 ``_ljust`` 同一套口径。"""
    if _display_width(text) <= width:
        return str(text)
    out, total = "", 0
    for char in str(text):
        size = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if total + size > width - 1:
            break
        out += char
        total += size
    return out + "…"


def _recent_body(entry: Dict[str, Any]) -> str:
    """时间线一行的正文 —— 按 ``kind`` 派生（文案在本层，L1 约定 B）。"""
    kind = entry.get("kind")
    if kind == "plan":
        return _clip(_plan_activity_text(entry), _RECENT_BODY_WIDTH)
    if kind == "inbox":
        text = _clip(str(entry.get("text") or ""), _RECENT_TEXT_WIDTH)
        return f"记了念头 {entry.get('id')} {text}"
    # ★ 实例名直接用 aiwf 的 ``title``（``runs list`` 已解析好、**永不为空**；旧 run = ``run_id``）。
    name = _clip(
        str(entry.get("title") or entry.get("run_id") or "?"),
        _RECENT_TEXT_WIDTH,
    )
    if entry.get("state") == "finished":
        return f"{name} 已结束（{entry.get('status')}）"
    stage = entry.get("current") or "?"
    ratio = _ratio(entry.get("stages_done"), entry.get("stages_total"))
    if entry.get("wait"):
        return f"⏳ {name} 在等你：{entry['wait']}（{stage} · {ratio}）"
    return f"{name} 运行中（{stage} · {ratio}）"


def _plan_activity_text(entry: Dict[str, Any]) -> str:
    """``plan`` 的动态行 —— 优先「在做」，其次「卡住」，都没有就是「有变动」。"""
    doing = entry.get("doing") or []
    extra = (entry.get("truncated") or {}).get("doing") or 0
    if doing:
        items = "、".join(f"{row['address']} {row['title']}" for row in doing)
        text = f"在做 {len(doing) + extra} 项：{items}"
        return text + " …" if extra else text
    blocked = entry.get("blocked") or []
    if blocked:
        items = "、".join(f"{row['address']} {row['title']}" for row in blocked)
        return f"卡住 {len(blocked)} 项：{items}"
    return "计划有变动"


def _section_line(label: str, text: str) -> str:
    return f"  {_ljust(label, _SECTION_WIDTH)}  {text}"


def _plan_line(section: Dict[str, Any]) -> str:
    if section.get("error"):
        return f"读取失败：{section['error']}"
    plans = section["plans"]
    if plans == 0:
        return "0 份计划 —— 未记账"
    dist = section["plan_status"]
    tasks = section["tasks"]
    line = (
        f"{plans} 份计划（open {dist['open']} · reached {dist['reached']}"
        f" · dropped {dist['dropped']}）"
        f"· 任务 {tasks['total']}（done {tasks['done']} · doing {tasks['doing']}"
        f" · todo {tasks['todo']} · blocked {tasks['blocked']}）"
    )
    if section.get("latest_activity"):
        line += f"· 上次变动 {section['latest_activity']}"
    return line


def _plan_dynamic_lines(section: Dict[str, Any]) -> List[str]:
    """``在做`` / ``卡住`` 行（**仅非空时出现**，§4.5 约定 1）—— 截断提示只给 forge 自家命令。"""
    if section.get("error"):
        return []
    lines: List[str] = []
    truncated = section.get("truncated") or {}
    for key, label in (("doing", "在做"), ("blocked", "卡住")):
        rows = section.get(key) or []
        if not rows:
            continue
        total = len(rows) + truncated.get(key, 0)
        items = " · ".join(f"{row['address']} {row['title']}" for row in rows)
        line = f"{_SECTION_INDENT}{label} {total}  {items}"
        if truncated.get(key):
            line += f"  ⋯ 还有 {truncated[key]} 条 → forge plan show"
        lines.append(line)
    return lines


def _inbox_line(section: Dict[str, Any]) -> str:
    if section.get("error"):
        return f"读取失败：{section['error']}"
    total = section["total"]
    if total == 0:
        return "0 条"
    dist = section["status"]
    line = (
        f"{total} 条（open {dist['open']} · handed {dist['handed']}"
        f" · dropped {dist['dropped']}）"
    )
    tail = []
    if section.get("latest_created"):
        tail.append(f"最近 {section['latest_created']}")
    if section.get("oldest_open"):
        tail.append(f"最老未处理 {section['oldest_open']}")
    if tail:
        line += "· " + " · ".join(tail)
    return line


def _inbox_recent_lines(section: Dict[str, Any]) -> List[str]:
    """``inbox`` 分区**最近加入的 3 条**（主人 2026-10-11）—— 一行一条，附状态（未处理之外才标）。"""
    if section.get("error"):
        return []
    rows = section.get("recent") or []
    if not rows:
        return []
    lines = [f"{_SECTION_INDENT}最近加入"]
    for row in rows:
        state = row.get("status")
        mark = "" if state == inbox.STATUS_OPEN else f"（{_status_label(state)}）"
        lines.append(
            f"{_SECTION_INDENT}  {_clip(str(row.get('id')), 8)}  "
            f"{_clip(str(row.get('text') or ''), _RECENT_TEXT_WIDTH)}{mark}"
        )
    return lines


def _aiwf_lines(section: Dict[str, Any]) -> List[str]:
    """``aiwf`` 首行 + 每个运行中 run 一行 —— ★ **不透传就翻译、不重排、不拼命令**（§4.3.1）。"""
    if section.get("error"):
        return [_section_line("aiwf", f"读取失败：{section['error']}")]
    workflows = section["workflows"]
    runs_total = section["runs_total"]
    running = section.get("running") or []
    if workflows == 0 and runs_total == 0:
        head = "流程 0 · 运行 0 —— 未接流程"
    else:
        head = f"流程 {workflows} · 运行 {runs_total} · 运行中 {len(running)}"
    lines = [_section_line("aiwf", head)]
    for run in running:
        # ★ 实例名直接用 aiwf 的 ``title``（``runs list`` 已解析好、**永不为空**；旧 run = ``run_id``）。
        name = _clip(
            str(run.get("title") or run.get("run_id") or "?"),
            _RECENT_TEXT_WIDTH,
        )
        stage = run.get("current") or "?"
        ratio = _ratio(run.get("stages_done"), run.get("stages_total"))
        if run.get("wait"):
            lines.append(
                f"{_SECTION_INDENT}⏳ {name} 在等你：{run['wait']}（{stage} · {ratio}）"
            )
        else:
            lines.append(f"{_SECTION_INDENT}{name} 运行中（{stage} · {ratio}）")
    return lines


def _ratio(done: Optional[int], total: Optional[int]) -> str:
    return f"{'?' if done is None else done}/{'?' if total is None else total}"


# ------------------------------------------------------------------ 入口
def _configure_stdio() -> None:
    """★ 把 CLI 自己的 ``stdout`` / ``stderr`` 钉成 **UTF-8**（见 ``04-observer.md`` §4.1 口径 6）。

    **症状**（实测）：Windows 控制台默认 **cp936**，而本视图渲染含**非 GBK 字形**
    （``⏳`` / ``⋯``）⇒ ``print`` 直接 ``UnicodeEncodeError``，**整条命令崩**。
    这与 ``observer`` 给子进程强制 ``PYTHONIOENCODING=utf-8`` 是**同一件事的两端**：
    输入侧我们自己解，输出侧我们自己写死。

    - ``errors="replace"`` 兜底 ⇒ **绝不因一个字形崩**（L1 约定 **G**）；
    - Windows 且输出到**控制台**时，顺带把控制台**输出代码页**设为 65001 ——
      否则 conhost 会把 UTF-8 字节按 cp936 渲染成乱码。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    if os.name == "nt":
        try:
            import ctypes

            if sys.stdout is not None and sys.stdout.isatty():
                ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except (OSError, AttributeError):
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ForgeError as exc:
        print(f"forge: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"forge: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
