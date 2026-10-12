"""``plan`` —— 项目内计划（一项目多份）。设计见 ``docs/design/06-plan.md``。

它是**用例层**模块：不碰文件，**只经 ``store``** 读写（L2 §4.2 边 5 / §2.2 W3）。

本模块提供三件事：

1. **模型操作**（§6.2 语义操作层）—— ``add_plan`` / ``add_task`` / ``set_target``
   加 5 个语义捷径（``claim`` / ``block`` / ``reject`` / ``done`` / ``drop``）。
   **每次写操作自动追一条 ``plan_log``** —— 轨迹是操作的**副产品**，agent 不单独记（§2.1 纪律 1）。
2. **读视图**（§2.3）—— ``list_plans`` / ``show_plan`` / ``show_task`` / ``timeline``，
   按"有没有界"给默认体量（§2.6）；另有 **汇总读面** ``overview``（§6.4）—— 一次给出
   「计数 / 分布 / 最近活动 / 在做·卡住」，供**视图型用例**（``workbench``）消费，
   免得它自己连事件、拼地址、重算语义（L2 §4.2.1：读经所有者读面）。
3. **跨记录校验** —— ``deps`` 同计划存在 + 无环 · 计划/任务唯一 · 地址合法。

> **为什么跨记录校验在本模块、而不在 ``store``**：``03-store`` §6.5 把"引用完整性"列在
> ``store`` 的检查范围内，但 ``store`` 的校验手段是**单份 JSON Schema**（§6.3），
> 而 JSON Schema 无法表达**跨记录约束**（``deps`` 指到同计划的任务、``plan`` 指向存在的计划、无环）。
> 故：**单份结构**（类型 / 必填 / 词表 / 形状）由 schema 兜底；**跨记录**的由本模块做 ——
> ``store`` 依旧"不假设数据形状"（§6.4），那条边界没破。

**判定归 agent**（L1 约定 B）：本模块**不判**"任务算不算完成""这份计划成了没"，只把 agent 的判定落盘。
"""
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import store
from .errors import PlanError

# ------------------------------------------------------------------ 常量
KIND_PLANS = "plans"
KIND_LOG = "plan_log"

PLANS_VERSION = 1

#: ``Plan.status`` 词表（§4）。
PLAN_STATUSES = ("open", "reached", "dropped")
#: ``Task.status`` 词表（§4）—— 4 态。
TASK_STATUSES = ("todo", "doing", "done", "blocked")
#: ``Event.op`` 词表 = 写动词集（§4）。
OP_ADD = "add"
OP_SET = "set"
OP_CLAIM = "claim"
OP_BLOCK = "block"
OP_REJECT = "reject"
OP_DONE = "done"
OP_DROP = "drop"
OPS = (OP_ADD, OP_SET, OP_CLAIM, OP_BLOCK, OP_REJECT, OP_DONE, OP_DROP)

#: ``Task.owner`` 取值（L0 §2 人机分工）。
OWNER_HUMAN = "human"
OWNER_AGENT = "agent"
OWNERS = (OWNER_HUMAN, OWNER_AGENT)

DEFAULT_PLAN_STATUS = "open"
DEFAULT_TASK_STATUS = "todo"

#: ``log`` 视图的默认体量（§2.4：无界的封顶）。
DEFAULT_LOG_TAIL = 20


# ------------------------------------------------------------------ 地址
def parse_address(address: str) -> "tuple[str, Optional[str]]":
    """``"login"`` → ``("login", None)``；``"login.T3"`` → ``("login", "T3")``。

    **无隐式解析**（§2.2）：``"T3"`` 只是一个**计划**名，不是任务 —— 任务**必须带计划**
    （``<plan>.<task>``），否则无法定位。计划标识与任务标识都**不含 ``.``**（schema 已约束）。
    """
    text = (address or "").strip()
    if not text:
        raise PlanError("地址不能为空")
    if "." in text:
        plan_id, task_id = text.split(".", 1)
        if not plan_id or not task_id or "." in task_id:
            raise PlanError(f"地址 {address!r} 不成立 —— 形如 <plan> 或 <plan>.<task>")
        return plan_id, task_id
    return text, None


def format_address(plan_id: str, task_id: Optional[str] = None) -> str:
    return f"{plan_id}.{task_id}" if task_id else plan_id


# ------------------------------------------------------------ 加载与保存
def _plans_doc(project_root: str) -> Dict[str, Any]:
    """读 ``plans``；**缺文件 = 空数据**（§6.1）→ 给一份空计划集合。"""
    doc = store.read(KIND_PLANS, project_root=project_root)
    if not doc:
        return {"version": PLANS_VERSION, "plans": []}
    return doc


def _save_plans(project_root: str, doc: Dict[str, Any]) -> None:
    store.write(KIND_PLANS, doc, project_root=project_root)


def _append_event(project_root: str, event: Dict[str, Any]) -> None:
    store.write(KIND_LOG, event, project_root=project_root)  # log 形态 → 追加一行


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_events(project_root: str) -> List[Dict[str, Any]]:
    """读全部过程事件（``log`` 形态 → 记录列表；缺文件 = ``[]``）。"""
    events = store.read(KIND_LOG, project_root=project_root)
    return list(events or [])


# ------------------------------------------------------------ 定位与校验
def _find_plan(doc: Dict[str, Any], plan_id: str) -> Dict[str, Any]:
    for plan in doc.get("plans", []):
        if plan.get("id") == plan_id:
            return plan
    raise PlanError(f"未找到计划 {plan_id!r}")


def _find_task(plan: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    for task in plan.get("tasks", []):
        if task.get("id") == task_id:
            return task
    raise PlanError(f"计划 {plan['id']!r} 里没有任务 {task_id!r}")


def _assert_acyclic(plan: Dict[str, Any]) -> None:
    """``deps`` 无环（§5 校验）。任务数 ≤ ~10，DFS 足够。"""
    graph = {t["id"]: list(t.get("deps") or []) for t in plan.get("tasks", [])}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}

    def visit(node: str, stack: List[str]) -> None:
        if color.get(node) == GRAY:
            raise PlanError(f"计划 {plan['id']!r}: deps 成环（{' -> '.join(stack + [node])}）")
        if color.get(node) == BLACK:
            return
        color[node] = GRAY
        for nxt in graph.get(node, []):
            if nxt in graph:
                visit(nxt, stack + [node])
        color[node] = BLACK

    for node in list(graph):
        visit(node, [])


def _criterion(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    text = str(item).strip()
    if not text:
        raise PlanError("判据不能为空")
    return {"text": text}


def _require_note(note: Optional[str], verb: str) -> str:
    if not note or not str(note).strip():
        raise PlanError(f"{verb} 必须带 --note（{verb} 不给原因就无意义，§2.4）")
    return str(note)


def _normalize_dep(plan_id: str, dep: Any) -> str:
    """把 ``--deps`` 的一项归一化为**计划内裸 id**。

    **两种形态都收**（H2：不易误用）：

    - **全地址** ``login.T1`` —— **规范形态**（§2.1 纪律 5「地址带计划」）；计划部分必须 == 当前计划；
    - **计划内裸 id** ``T1`` —— 宽容接受（`deps` 是计划内引用，计划已由所在位置给定，§3）。

    跨计划引用（计划部分与当前计划不一致）**拒** —— 本轮不做（§8 / §9 **T12**）。
    存盘统一为**计划内裸 id**（对齐 §3 `deps: 前置任务 id（同一份计划内）`，且不重复承载计划上下文）。
    """
    text = str(dep).strip()
    if not text:
        raise PlanError("deps 不能为空")
    if "." in text:
        dep_plan, dep_task = text.split(".", 1)
        if not dep_plan or not dep_task:
            raise PlanError(f"deps 地址 {text!r} 不成立 —— 形如 <plan>.<task>")
        if dep_plan != plan_id:
            raise PlanError(
                f"deps 只能指向**本计划**（{plan_id}）内的任务 —— 收到跨计划引用 {text!r}"
                f"（跨计划依赖本轮不做，§8 / §9 T12）"
            )
        return dep_task
    return text


# ------------------------------------------------------------ 写：模型操作
def add_plan(
    project_root: str,
    plan_id: str,
    *,
    title: str,
    goal: str,
    criteria: Sequence[Any],
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """新建**一份计划**（§2.4）—— 状态初值 ``open``。"""
    plan_id, task_id = parse_address(plan_id)
    if task_id:
        raise PlanError(f"add 建计划时地址只能给计划名，收到 {plan_id}.{task_id}")
    doc = _plans_doc(project_root)
    if any(plan.get("id") == plan_id for plan in doc.get("plans", [])):
        raise PlanError(f"计划 {plan_id!r} 已存在")
    plan = {
        "id": plan_id,
        "title": title,
        "goal": goal,
        "criteria": [_criterion(item) for item in criteria],
        "status": DEFAULT_PLAN_STATUS,
        "tasks": [],
    }
    doc.setdefault("plans", []).append(plan)
    doc["version"] = doc.get("version", PLANS_VERSION)
    _save_plans(project_root, doc)
    _append_event(
        project_root,
        _event(plan_id, plan_id, OP_ADD, to=DEFAULT_PLAN_STATUS, note=note),
    )
    return plan


def add_task(
    project_root: str,
    address: str,
    *,
    title: str,
    done_when: Optional[str] = None,
    owner: Optional[str] = None,
    deps: Optional[Iterable[str]] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """新建**一个任务**（§2.4）—— 地址必须是 ``<plan>.<task>``，状态初值 ``todo``。"""
    plan_id, task_id = parse_address(address)
    if not task_id:
        raise PlanError(f"建任务要给计划内地址 <plan>.<task>，收到 {address!r}")
    if owner is not None and owner not in OWNERS:
        raise PlanError(f"owner 只能取 {OWNERS}，收到 {owner!r}")
    doc = _plans_doc(project_root)
    plan = _find_plan(doc, plan_id)
    if any(task.get("id") == task_id for task in plan.get("tasks", [])):
        raise PlanError(f"计划 {plan_id!r} 里已有任务 {task_id!r}")

    dep_ids = [_normalize_dep(plan_id, dep) for dep in (deps or [])]
    known = {task["id"] for task in plan.get("tasks", [])}
    missing = [dep for dep in dep_ids if dep not in known]
    if missing:
        raise PlanError(
            f"deps 必须指向**同计划内**已存在的任务（§5）—— 未找到：{', '.join(missing)}"
        )

    task: Dict[str, Any] = {
        "id": task_id,
        "title": title,
        "status": DEFAULT_TASK_STATUS,
    }
    if dep_ids:
        task["deps"] = dep_ids
    if owner is not None:
        task["owner"] = owner
    if done_when is not None:
        task["done_when"] = done_when
    plan.setdefault("tasks", []).append(task)
    _assert_acyclic(plan)
    _save_plans(project_root, doc)
    _append_event(
        project_root,
        _event(plan_id, task_id, OP_ADD, to=DEFAULT_TASK_STATUS, note=note),
    )
    return task


def set_target(
    project_root: str,
    address: str,
    *,
    status: Optional[str] = None,
    owner: Optional[str] = None,
    note: Optional[str] = None,
    goal: Optional[str] = None,
    title: Optional[str] = None,
    op: str = OP_SET,
) -> Dict[str, Any]:
    """**兜底写动词**（§2.5）—— 其余一切改动；捷径内部也走它。

    ``op`` 记的是 **agent 实际用的动词**（捷径名 / ``set``，§2.5.1），不由 ``(from, to)`` 反推。

    落盘顺序取 **T7 的倾向**（§6.3）：**先写 ``plan_log`` 再写 ``plans``** ——
    轨迹是事实，宁可多一条孤立事件，不可丢一次状态变更。
    """
    plan_id, task_id = parse_address(address)
    doc = _plans_doc(project_root)
    plan = _find_plan(doc, plan_id)
    if task_id:
        target = _find_task(plan, task_id)
        allowed = TASK_STATUSES
        label = format_address(plan_id, task_id)
    else:
        target = plan
        allowed = PLAN_STATUSES
        label = plan_id

    if status is not None and status not in allowed:
        raise PlanError(f"{label} 的 status 只能取 {allowed}，收到 {status!r}")
    if owner is not None:
        if not task_id:
            raise PlanError("owner 只属于任务 —— 计划没有 owner 字段（§3.1）")
        if owner not in OWNERS:
            raise PlanError(f"owner 只能取 {OWNERS}，收到 {owner!r}")
    if goal is not None and task_id:
        raise PlanError("goal 只属于计划 —— 任务请用 --title（§3.1）")

    before = target.get("status")
    dirty = False

    if title is not None:
        if not str(title).strip():
            raise PlanError("title 不能为空")
        if target.get("title") != title:
            target["title"] = title
            dirty = True
    if goal is not None:
        if not str(goal).strip():
            raise PlanError("goal 不能为空")
        if target.get("goal") != goal:
            target["goal"] = goal
            dirty = True
    if owner is not None and target.get("owner") != owner:
        target["owner"] = owner
        dirty = True
    if status is not None and status != before:
        target["status"] = status
        dirty = True

    event = _event(
        plan_id,
        task_id or plan_id,
        op,
        status_from=before if status is not None else None,
        to=status if status is not None else None,
        note=note,
    )
    _append_event(project_root, event)
    if dirty:
        _save_plans(project_root, doc)

    return {
        "address": label,
        "op": op,
        "from": before,
        "to": target.get("status"),
        "changed": dirty,
        "event": event,
    }


# ------------------------------------------------------------ 写：语义捷径
def claim(project_root: str, address: str, *, note: Optional[str] = None) -> Dict[str, Any]:
    """**认领** → ``doing`` + ``owner: agent``（§2.5）。"""
    plan_id, task_id = parse_address(address)
    if not task_id:
        raise PlanError("claim 只作用于任务（<plan>.<task>）")
    return set_target(
        project_root, address, status="doing", owner=OWNER_AGENT, note=note, op=OP_CLAIM
    )


def block(project_root: str, address: str, *, note: Optional[str]) -> Dict[str, Any]:
    """**卡住** → ``blocked``；``--note`` 必填（§2.4）。"""
    plan_id, task_id = parse_address(address)
    if not task_id:
        raise PlanError("block 只作用于任务（<plan>.<task>）")
    return set_target(
        project_root, address, status="blocked", note=_require_note(note, "block"), op=OP_BLOCK
    )


def reject(project_root: str, address: str, *, note: Optional[str]) -> Dict[str, Any]:
    """**打回** → ``doing``；``--note`` **必填**（打回原因，§2.4）。"""
    plan_id, task_id = parse_address(address)
    if not task_id:
        raise PlanError("reject 只作用于任务（<plan>.<task>）")
    return set_target(
        project_root, address, status="doing", note=_require_note(note, "reject"), op=OP_REJECT
    )


def done(project_root: str, address: str, *, note: Optional[str] = None) -> Dict[str, Any]:
    """**完成** —— task → ``done``；**计划 → ``reached``**（泛化，§2.5）。"""
    _plan_id, task_id = parse_address(address)
    target_status = "done" if task_id else "reached"
    return set_target(project_root, address, status=target_status, note=note, op=OP_DONE)


def drop(project_root: str, address: str, *, note: Optional[str]) -> Dict[str, Any]:
    """**放弃一份计划** → ``dropped``；``--note`` 必填（§2.4）。"""
    plan_id, task_id = parse_address(address)
    if task_id:
        raise PlanError("drop 只作用于计划（<plan>）—— 放弃一个任务请用 set --status")
    return set_target(
        project_root, address, status="dropped", note=_require_note(note, "drop"), op=OP_DROP
    )


def _event(
    plan_id: str,
    entity_id: str,
    op: str,
    *,
    status_from: Optional[str] = None,
    to: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "ts": _now(),
        "plan": plan_id,
        "id": entity_id,
        "op": op,
    }
    if status_from is not None:
        event["from"] = status_from
    if to is not None:
        event["to"] = to
    if note:
        event["note"] = note
    return event


# ---------------------------------------------------------------- 读视图
def list_plans(project_root: str, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """**全局列表**（步 0，§2.3）—— 每份计划 ``id`` / ``title`` / ``status`` + 任务完成度。

    **不含 ``goal`` / 轨迹**（§2.6 规则 3）。
    """
    doc = _plans_doc(project_root)
    rows: List[Dict[str, Any]] = []
    for plan in doc.get("plans", []):
        if status is not None and plan.get("status") != status:
            continue
        tasks = plan.get("tasks", []) or []
        rows.append(
            {
                "id": plan["id"],
                "title": plan["title"],
                "status": plan["status"],
                "tasks_total": len(tasks),
                "tasks_done": sum(1 for task in tasks if task.get("status") == "done"),
            }
        )
    return rows


def show_plan(project_root: str, plan_id: str) -> Dict[str, Any]:
    """**单份计划**（步 1，§2.3）—— ``title`` / ``goal`` / ``criteria`` / ``status`` + 任务列表（**不带轨迹**）。"""
    doc = _plans_doc(project_root)
    plan = _find_plan(doc, plan_id)
    return {
        "id": plan["id"],
        "title": plan["title"],
        "goal": plan["goal"],
        "criteria": [dict(item) for item in plan.get("criteria", [])],
        "status": plan["status"],
        "tasks": [
            {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "owner": task.get("owner"),
                "done_when": task.get("done_when"),
                "deps": list(task.get("deps") or []),
            }
            for task in plan.get("tasks", [])
        ],
    }


def show_task(
    project_root: str, plan_id: str, task_id: str, *, tail: Optional[int] = None
) -> Dict[str, Any]:
    """**单任务交接包**（步 2，§2.3）—— 当前状态 + **完整轨迹**（``--tail N`` 可限）。"""
    doc = _plans_doc(project_root)
    plan = _find_plan(doc, plan_id)
    task = _find_task(plan, task_id)
    events = _filter_events(project_root, plan=plan_id, entity=task_id)
    if tail is not None:
        events = events[-tail:] if tail > 0 else []
    return {
        "plan": plan_id,
        "id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "owner": task.get("owner"),
        "done_when": task.get("done_when"),
        "deps": list(task.get("deps") or []),
        "events": events,
    }


def timeline(
    project_root: str,
    *,
    plan: Optional[str] = None,
    tail: Optional[int] = None,
    show_all: bool = False,
) -> List[Dict[str, Any]]:
    """**跨计划时间线**（步 9，§2.3）—— 默认**尾部 ``DEFAULT_LOG_TAIL`` 行**（§2.4：无界要封顶）。"""
    events = _filter_events(project_root, plan=plan)
    if show_all:
        return events
    limit = DEFAULT_LOG_TAIL if tail is None else tail
    return events[-limit:] if limit > 0 else []


def _filter_events(
    project_root: str, *, plan: Optional[str] = None, entity: Optional[str] = None
) -> List[Dict[str, Any]]:
    events = read_events(project_root)
    if plan is not None:
        events = [event for event in events if event.get("plan") == plan]
    if entity is not None:
        events = [event for event in events if event.get("id") == entity]
    return events


# ------------------------------------------------------------ 汇总读面
def overview(project_root: str) -> Dict[str, Any]:
    """**项目级汇总**（§6.4）—— 一次给全 ``workbench`` 要的那个 ``plan`` 面。

    **语义留在本模块**（状态词表 · 地址拼装 · 事件连接与排序）；消费方只做
    「**取 → 截断 → 渲染**」，不重算 —— 否则同一份知识两处实现（L2 §4.2.1 读经所有者读面；L1 **D1**）。

    **不截断** —— 上限是**呈现**的事（L1 **H4**），归消费方（`08-workbench` §2.4 规则 2）。
    **缺文件 = 空数据**（§6.1）—— 给零值，不报错。
    """
    plans = _plans_doc(project_root).get("plans", []) or []

    # 任务「最近一次动的时刻」= 轨迹里该 (plan, id) 的**末条** ts（日志按时间追加 → 取后者）
    seen_at: Dict[tuple, str] = {}
    latest_activity: Optional[str] = None
    for event in read_events(project_root):
        ts = event.get("ts")
        if not ts:
            continue
        latest_activity = ts
        seen_at[(event.get("plan"), event.get("id"))] = ts

    plan_status = {name: 0 for name in PLAN_STATUSES}
    tasks = {name: 0 for name in TASK_STATUSES}
    buckets: Dict[str, List[Dict[str, Any]]] = {"doing": [], "blocked": []}
    for plan in plans:
        if plan.get("status") in plan_status:
            plan_status[plan["status"]] += 1
        for task in plan.get("tasks", []) or []:
            status = task.get("status")
            if status in tasks:
                tasks[status] += 1
            if status in buckets:
                buckets[status].append(
                    {
                        "address": format_address(plan["id"], task["id"]),
                        "title": task.get("title"),
                    }
                )

    for rows in buckets.values():
        # 最近动的在前；**无事件**的排末尾；**同键**按地址升序 —— 全程确定（L1 G）
        rows.sort(key=lambda row: row["address"])
        rows.sort(
            key=lambda row: seen_at.get(parse_address(row["address"])) or "",
            reverse=True,
        )

    return {
        "plans": len(plans),
        "plan_status": plan_status,
        "tasks": {
            "total": sum(tasks.values()),
            "done": tasks["done"],
            "doing": tasks["doing"],
            "todo": tasks["todo"],
            "blocked": tasks["blocked"],
        },
        "latest_activity": latest_activity,
        "doing": buckets["doing"],
        "blocked": buckets["blocked"],
    }


def project_plan_path(project_root: str) -> str:
    """``plans.json`` 的落点 —— 仅供**报错 / 提示**用（不暴露形态，§6.1 边界）。"""
    return store.path_for(KIND_PLANS, project_root=os.path.abspath(project_root))
