"""``workbench`` —— 跨项目 / 本项目的**读视图**（零落盘）。设计见 ``docs/design/08-workbench.md``。

它是**用例层**的**视图**用例（L2 §2.1）—— 与 ``plan`` / ``inbox`` / ``scaffold`` 不同：
**不写任何数据**（零 ``kind`` / 零 schema / 零写动词，§3.1）：

1. 读 ``projects`` 清单（``store.read`` —— ``store`` 自有的 global kind）；
2. 逐项目经**所有者读面**取三个分区（L2 §4.2.1）——
   ``plan.overview()`` · ``inbox.list_items()`` · ``observer.observe()``；
3. 合成 ``ProjectStatus`` / ``Workbench``（**读时投影**），**一个字节都不落盘**。

对外两个读面（同一段渲染的两种体量）：

- ``collect()`` → ``Workbench``（全局视图 = ``forge workbench``）；
- ``collect_project(root)`` → ``ProjectStatus``（本项目视图 = ``forge status``）。

**不做**（§8）：不写盘 · 不 import ``plan`` / ``inbox`` 内部实现（只调公开读面）·
不判定「需不需要介入」（只给计数与透传）· 不翻译 / 不重排 aiwf 的词与行
（那是 ``observer`` / aiwf 的事）。
"""
import os
from typing import Any, Callable, Dict, List, Optional

from . import inbox, observer, plan, store
from .errors import StoreError

#: 动态项上限（§4.5 渲染约定 2 · §10-16）—— 超出给 ``⋯ 还有 K 条``。
DYNAMIC_LIMIT = 3

#: ``inbox`` 分区**最近加入**给几条（§4.2 · 主人 2026-10-11 追加）。
INBOX_RECENT_LIMIT = 3

#: 跨项目「最近动态」时间线给几条（§4.6 · 主人 2026-10-11 追加）—— 只留最近的几件。
RECENT_LIMIT = 6

#: 注入用 runner 签名（转发给 ``observer``）—— 供测试喂桩 aiwf 输出（L1 约定 **G**）。
Runner = Callable[..., Any]

#: 各分区的状态词表（**只为计数** —— 渲染在入口层；词表所有者分别是 plan / inbox）。
_PLAN_STATUSES = ("open", "reached", "dropped")
_INBOX_STATUSES = ("open", "handed", "dropped")


# ------------------------------------------------------------------ 读面
def collect(*, runner: Optional[Runner] = None) -> Dict[str, Any]:
    """**全局视图** —— ``Workbench``（§3.2），供 ``forge workbench``。

    读 ``projects`` 清单（**缺文件 = 空清单**，``03-store`` §5.3），逐项目取三个分区，
    按「**焦点置顶 + ``alias`` 升序**」排序（§3.3）—— 全程确定（L1 约定 **G**）。

    ★ **清单自身损坏 → ``StoreError`` 冒泡**（§6.4）：它是唯一输入，坏了就没得算。
    """
    doc = store.read("projects")
    if not isinstance(doc, dict):
        doc = {}
    focus = doc.get("focus")
    rows = [
        _status_from_registry(project, focus=focus, runner=runner)
        for project in doc.get("projects") or []
        if isinstance(project, dict)
    ]
    rows.sort(key=_sort_key)
    return {"focus": focus, "projects": rows}


def merge_recent(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把各项目的 ``recent`` 合成**一条全局时间线**（§4.6）—— 全局视图置顶用。

    ★ **只在读时合、不写进 ``Workbench``**：``ProjectStatus`` 已各带一份 ``recent``，
    再在顶层存一份就是**同一份数据两处落**（L1 约定 **D** / **H4**）——
    入口层拿这份合成结果渲染即可。
    """
    entries = [entry for row in projects for entry in (row.get("recent") or [])]
    entries.sort(key=_recent_sort_key, reverse=True)
    return entries[:RECENT_LIMIT]


def collect_project(project_root: str, *, runner: Optional[Runner] = None) -> Dict[str, Any]:
    """**本项目视图** —— 一个 ``ProjectStatus``（§3.2 注），供 ``forge status``。

    项目根由调用方给（命令层取 **CWD**，与 ``plan`` / ``inbox`` 同款）。
    在清单里找得到 → 带上身份并算焦点；找不到 → **未受管**（``alias`` 等为 ``None``），
    三个分区**照读**（**未受管照常可用**，§10-**b**）。

    ★ 清单**读不到**（损坏）时**降级为未受管**而非让命令失败 —— 本项目视图的核心是三个
    分区，身份只是附加（L1 约定 **A1** + §10-**b**「照常可用」）。
    """
    root = os.path.abspath(project_root)
    try:
        doc = store.read("projects")
    except StoreError:
        doc = {}
    if not isinstance(doc, dict):
        doc = {}
    focus = doc.get("focus")
    match: Optional[Dict[str, Any]] = None
    for project in doc.get("projects") or []:
        if isinstance(project, dict) and _same_path(project.get("path"), root):
            match = project
            break
    if match is None:
        return _partition_status(
            root, alias=None, blueprint=None, registered_at=None, focus=False, runner=runner
        )
    return _partition_status(
        root,
        alias=match.get("alias"),
        blueprint=match.get("blueprint"),
        registered_at=match.get("registered_at"),
        focus=match.get("alias") == focus,
        runner=runner,
    )


# ------------------------------------------------------------------ 组装
def _status_from_registry(
    project: Dict[str, Any], *, focus: Optional[str], runner: Optional[Runner]
) -> Dict[str, Any]:
    """清单里的一条 ``Project`` → ``ProjectStatus``（身份来自清单，三个分区现读）。"""
    return _partition_status(
        project.get("path"),
        alias=project.get("alias"),
        blueprint=project.get("blueprint"),
        registered_at=project.get("registered_at"),
        focus=project.get("alias") == focus,
        runner=runner,
    )


def _partition_status(
    path: Optional[str],
    *,
    alias: Optional[str],
    blueprint: Optional[str],
    registered_at: Optional[str],
    focus: bool,
    runner: Optional[Runner],
) -> Dict[str, Any]:
    """``ProjectStatus``（§3.2）—— 身份 + 三个分区 + 顶层 ``error``。

    顶层 ``error`` 当前**恒 ``None``**：三个分区各自容错（坏在哪显示在哪，§6.4）。
    """
    root = os.path.abspath(path) if path else path
    status = {
        "alias": alias,
        "path": root,
        "blueprint": blueprint,
        "registered_at": registered_at,
        "focus": focus,
        "plan": _plan_section(root, runner),
        "inbox": _inbox_section(root, runner),
        "aiwf": _aiwf_section(root, runner),
        "error": None,
    }
    # ★ 本项目「最近动态」（§4.6）—— 挂在 ``ProjectStatus`` 上：全局视图的各项目也各带一份，
    #   全局时间线由 ``merge_recent()`` 读时合成（不另存，L1 D / H4）。
    return _with_recent(status)


def _plan_section(root: Optional[str], runner: Optional[Runner]) -> Dict[str, Any]:
    """``plan`` 分区（§4.1）—— 调**所有者读面** ``plan.overview()``，本模块只**截断**。

    ★ 计数 / 分布 / ``latest_activity`` / ``doing`` ``blocked``（连顺序）**原样来自所有者**；
    本模块**不重算语义、不排序**（L2 §4.2.1 · L1 约定 **D1**），只截断到 ``DYNAMIC_LIMIT``
    并算余量（L1 约定 **H4**）。
    """
    try:
        data = plan.overview(root)
    except StoreError as exc:
        # 坏数据**显式显示**，不静默吞（§6.4）—— ★ 计数给 ``None`` 不取 ``0``
        # （``0`` 会被读成「真的没有」，L1 约定 **H2**）。
        return {
            "plans": None,
            "plan_status": None,
            "tasks": None,
            "latest_activity": None,
            "doing": [],
            "blocked": [],
            "truncated": {"doing": 0, "blocked": 0},
            "error": str(exc),
        }

    doing = data.get("doing") or []
    blocked = data.get("blocked") or []
    return {
        "plans": data["plans"],
        "plan_status": data["plan_status"],
        "tasks": data["tasks"],
        "latest_activity": data.get("latest_activity"),
        "doing": doing[:DYNAMIC_LIMIT],
        "blocked": blocked[:DYNAMIC_LIMIT],
        "truncated": {
            "doing": max(0, len(doing) - DYNAMIC_LIMIT),
            "blocked": max(0, len(blocked) - DYNAMIC_LIMIT),
        },
        "error": None,
    }


def _inbox_section(root: Optional[str], runner: Optional[Runner]) -> Dict[str, Any]:
    """``inbox`` 分区（§4.2）—— 调**所有者读面** ``inbox.list_items(show_all=True)``。

    计数与 ``min`` / ``max``（``latest_created`` / ``oldest_open``）是**平凡组装**，不涉语义
    （§6.2）；``oldest_open`` **只在有 ``open`` 时才有值**（全消化完 → 缺省，**不是错误**，A1）。
    """
    try:
        rows = inbox.list_items(root, show_all=True)
    except StoreError as exc:
        return {
            "total": None,
            "status": None,
            "latest_created": None,
            "oldest_open": None,
            "recent": [],
            "error": str(exc),
        }

    status = {name: 0 for name in _INBOX_STATUSES}
    created: List[str] = []
    open_created: List[str] = []
    for row in rows:
        state = row.get("status")
        if state in status:
            status[state] += 1
        ts = row.get("created")
        if ts:
            created.append(ts)
            if state == inbox.STATUS_OPEN:
                open_created.append(ts)
    # ★ **最近加入**（主人 2026-10-11 追加）：按 ``created`` 降序、同日在后按 ``id`` 降序
    #   （id 递增不复用 ⇒ 同日唯一确定）。
    recent = sorted(
        rows, key=lambda row: (row.get("created") or "", row.get("id") or ""), reverse=True
    )[:INBOX_RECENT_LIMIT]
    return {
        "total": len(rows),
        "status": status,
        "latest_created": max(created) if created else None,
        "oldest_open": min(open_created) if open_created else None,
        "recent": [dict(row) for row in recent],
        "error": None,
    }


def _aiwf_section(root: Optional[str], runner: Optional[Runner]) -> Dict[str, Any]:
    """``aiwf`` 分区（§4.3）—— 经 **``observer``**（L2 §2.2 不变量 2 · §4.2 边 8）。

    ★ **硬线**：``workbench`` 不自己读 ``.aiwf/``，只取 ``observer`` 的投影；
    ``observer`` 从不抛（失败进 ``aiwf.error``，§6.4）。
    """
    if runner is None:
        observation = observer.observe(root)
    else:
        observation = observer.observe(root, runner=runner)
    return observation["aiwf"]


# ------------------------------------------------------------------ 内部
# ------------------------------------------------------------------ 最近动态
def _with_recent(status: Dict[str, Any]) -> Dict[str, Any]:
    """给一个 ``ProjectStatus`` 挂上**本项目**的「最近动态」（§4.6），按时间降序截断。"""
    entries = _activity_entries(status)
    entries.sort(key=_recent_sort_key, reverse=True)
    status["recent"] = entries[:RECENT_LIMIT]
    return status


def _recent_sort_key(entry: Dict[str, Any]):
    """时间线排序键 —— ``ts`` 降序；无 ``ts``（``""``）排末尾；同 ``ts`` 按 kind / 项目（L1 G）。"""
    return (entry.get("ts") or "", entry.get("kind") or "", entry.get("project") or "")


def _activity_entries(status: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一个项目的**带时间戳的事实** → 时间线条目（§4.6）。

    ★ **只挑已经有时间戳的**：``plan.latest_activity`` · ``inbox`` 最近一条 ``created`` ·
    ``aiwf`` 运行中与「最近一轮」的 ``started_at``。**不臆造时间、不重排语义**（L1 A1 / D1）：
    分区里没有逐项时间戳的部分（如 ``doing`` 的每一条）就**不硬塞**进来。

    ``inbox`` **每项目只取最近 1 条**进时间线 —— 分区里已经给了最近 3 条（§4.2），
    时间线再铺 3 条会把别的项目挤掉（H4）。
    """
    alias = status.get("alias")
    entries: List[Dict[str, Any]] = []

    plan_sec = status.get("plan") or {}
    if not plan_sec.get("error") and plan_sec.get("latest_activity"):
        entries.append(
            {
                "ts": plan_sec["latest_activity"],
                "kind": "plan",
                "project": alias,
                "doing": plan_sec.get("doing") or [],
                "blocked": plan_sec.get("blocked") or [],
                "truncated": plan_sec.get("truncated") or {},
            }
        )

    inbox_sec = status.get("inbox") or {}
    if not inbox_sec.get("error"):
        for row in (inbox_sec.get("recent") or [])[:1]:
            entries.append(
                {
                    "ts": row.get("created"),
                    "kind": "inbox",
                    "project": alias,
                    "id": row.get("id"),
                    "text": row.get("text"),
                }
            )

    aiwf_sec = status.get("aiwf") or {}
    if not aiwf_sec.get("error"):
        for run in aiwf_sec.get("running") or []:
            entries.append(
                {
                    "ts": run.get("started_at"),
                    "kind": "aiwf",
                    "project": alias,
                    "state": "running",
                    "title": run.get("title"),
                    "workflow_id": run.get("workflow_id"),
                    "run_id": run.get("run_id"),
                    "current": run.get("current"),
                    "stages_done": run.get("stages_done"),
                    "stages_total": run.get("stages_total"),
                    "wait": run.get("wait"),
                }
            )
        last = aiwf_sec.get("last")
        if last:
            entries.append(
                {
                    "ts": last.get("started_at"),
                    "kind": "aiwf",
                    "project": alias,
                    "state": "finished",
                    "title": last.get("title"),
                    "workflow_id": last.get("workflow_id"),
                    "run_id": last.get("run_id"),
                    "status": last.get("status"),
                }
            )
    return entries


def _sort_key(row: Dict[str, Any]):
    """排序键：**焦点置顶**（``not focus`` → ``True`` 排后），其余按 ``alias`` 升序（§3.3）。"""
    return (not row.get("focus"), row.get("alias") or "")


def _same_path(left: Optional[str], right: str) -> bool:
    """两个路径是否指向同一处 —— 规范化大小写与分隔符（Windows 盘符大小写 / 反斜杠差异）。"""
    if not left:
        return False
    return os.path.normcase(os.path.normpath(os.path.abspath(left))) == os.path.normcase(
        os.path.normpath(os.path.abspath(right))
    )
