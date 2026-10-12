"""``plan`` 的行为验证（``06-plan`` §2 / §3 / §6）。

对应 L1 约定 **G**：引擎的确定性行为（地址解析 / 词表 / 事件规则 / 读视图体量）必须有自动化验证。
"""
import json
from pathlib import Path

import pytest

from forge import cli, plan, store
from forge.errors import PlanError


def _block(root, plan_id="login", **kwargs):
    return plan.add_plan(
        root,
        plan_id,
        title=kwargs.pop("title", "登录模块"),
        goal=kwargs.pop("goal", "让用户能安全登录"),
        criteria=kwargs.pop("criteria", ["注册后能收到验证邮件"]),
        **kwargs,
    )


# ------------------------------------------------------------------ 地址
def test_parse_address():
    """无隐式解析（§2.2）：任务必须带计划。"""
    assert plan.parse_address("login") == ("login", None)
    assert plan.parse_address("login.T3") == ("login", "T3")
    assert plan.format_address("login") == "login"
    assert plan.format_address("login", "T3") == "login.T3"
    for bad in ("", "   ", ".T3", "login.", "a.b.c"):
        with pytest.raises(PlanError):
            plan.parse_address(bad)


# ------------------------------------------------------------ 写：建实体
def test_add_plan_writes_snapshot_and_event(tmp_path):
    root = str(tmp_path)
    _block(root)

    doc = store.read("plans", project_root=root)
    assert doc["version"] == 1
    block = doc["plans"][0]
    assert block["id"] == "login"
    assert block["status"] == "open"
    assert block["criteria"] == [{"text": "注册后能收到验证邮件"}]
    assert block["tasks"] == []

    event = plan.read_events(root)[0]
    assert (event["op"], event["plan"], event["id"], event["to"]) == ("add", "login", "login", "open")
    assert "from" not in event


def test_duplicate_plan_is_refused(tmp_path):
    root = str(tmp_path)
    _block(root)
    with pytest.raises(PlanError):
        _block(root)


def test_add_task_requires_block_and_unique_id(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="骨架")
    tasks = store.read("plans", project_root=root)["plans"][0]["tasks"]
    assert tasks[0]["status"] == "todo"

    with pytest.raises(PlanError):
        plan.add_task(root, "T2", title="没带计划")  # §2.2：计划缺失 → 直接拒
    with pytest.raises(PlanError):
        plan.add_task(root, "nope.T1", title="计划不存在")
    with pytest.raises(PlanError):
        plan.add_task(root, "login.T1", title="重名")


def test_deps_must_point_inside_the_block(tmp_path):
    """``deps`` 限同计划内（§5 校验）。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.add_task(root, "login.T2", title="b", deps=["T1"])
    with pytest.raises(PlanError):
        plan.add_task(root, "login.T3", title="c", deps=["T9"])


def test_deps_accepts_full_address_and_bare_id(tmp_path):
    """``--deps`` 收**全地址**（规范，§2.1 纪律 5）**或**计划内裸 id（宽容）—— 存盘统一为计划内裸 id（§10-★28）。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.add_task(root, "login.T2", title="b", deps=["login.T1"])  # 全地址
    plan.add_task(root, "login.T3", title="c", deps=["T1"])       # 计划内裸 id

    doc = store.read("plans", project_root=root)
    tasks = {task["id"]: task for task in doc["plans"][0]["tasks"]}
    assert tasks["T2"]["deps"] == ["T1"]
    assert tasks["T3"]["deps"] == ["T1"]


def test_deps_rejects_cross_block_unknown_and_empty(tmp_path):
    """跨计划 / 不存在 / 空 —— 都拒（§5 · §8 · §10-★28）。"""
    root = str(tmp_path)
    _block(root)
    _block(root, "pay")
    plan.add_task(root, "login.T1", title="a")
    plan.add_task(root, "pay.T1", title="p")

    for bad in ("pay.T1", "login.T9", ""):
        with pytest.raises(PlanError):
            plan.add_task(root, "login.T2", title="b", deps=[bad])


def test_cli_deps_accepts_full_address(tmp_path, monkeypatch, capsys):
    """CLI 面上 ``--deps login.T1`` 同样通过（H2：与其余地址形态一致）。"""
    _chdir(tmp_path, monkeypatch)
    cli.main(["plan", "add", "login", "--title", "t", "--goal", "g", "--criteria", "c"])
    cli.main(["plan", "add", "login.T1", "--title", "a"])
    capsys.readouterr()
    assert cli.main(["plan", "add", "login.T2", "--title", "b", "--deps", "login.T1"]) == 0
    assert cli.main(["plan", "add", "login.T3", "--title", "c", "--deps", "pay.T1"]) == 1


# ------------------------------------------------------------ 写：捷径
def test_shortcut_event_ops_and_from_to(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T3", title="实现登录页")

    plan.claim(root, "login.T3")
    plan.reject(root, "login.T3", note="密码强度校验缺失，未过")
    plan.done(root, "login.T3", note="补了中文文案，通过")

    events = plan.read_events(root)
    assert [event["op"] for event in events] == ["add", "add", "claim", "reject", "done"]

    claim_event = events[2]
    assert (claim_event["from"], claim_event["to"]) == ("todo", "doing")
    reject_event = events[3]
    # 打回与认领的 (from, to) 相同 —— 靠 op 区分（§2.5.1）
    assert (reject_event["from"], reject_event["to"]) == ("doing", "doing")
    assert reject_event["note"] == "密码强度校验缺失，未过"

    assert store.read("plans", project_root=root)["plans"][0]["tasks"][0]["status"] == "done"


def test_claim_sets_owner(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.claim(root, "login.T1")
    assert store.read("plans", project_root=root)["plans"][0]["tasks"][0]["owner"] == "agent"


def test_shortcuts_that_need_a_task_refuse_a_block(tmp_path):
    root = str(tmp_path)
    _block(root)
    for verb in ("claim", "block", "reject"):
        with pytest.raises(PlanError):
            getattr(plan, verb)(root, "login", note="x")


@pytest.mark.parametrize("verb", ["block", "reject"])
def test_note_is_mandatory(tmp_path, verb):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    for note in (None, "   "):
        with pytest.raises(PlanError):
            getattr(plan, verb)(root, "login.T1", note=note)
    assert store.read("plans", project_root=root)["plans"][0]["tasks"][0]["status"] == "todo"


def test_single_task_shortcuts(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.block(root, "login.T1", note="缺设计稿")
    assert store.read("plans", project_root=root)["plans"][0]["tasks"][0]["status"] == "blocked"


def test_done_generalizes_to_block(tmp_path):
    """「这实体成了」只有一种表达（§2.5）：task → ``done``，计划 → ``reached``。"""
    root = str(tmp_path)
    _block(root)
    result = plan.done(root, "login")
    assert result["to"] == "reached"
    assert store.read("plans", project_root=root)["plans"][0]["status"] == "reached"


def test_drop_only_applies_to_blocks_and_needs_note(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    with pytest.raises(PlanError):
        plan.drop(root, "login", note=None)
    with pytest.raises(PlanError):
        plan.drop(root, "login.T1", note="x")

    plan.drop(root, "login", note="战略调整")
    assert store.read("plans", project_root=root)["plans"][0]["status"] == "dropped"
    assert plan.read_events(root)[-1]["op"] == "drop"


# ------------------------------------------------------------ 写：兜底 set
def test_set_note_only_touches_log(tmp_path):
    """§6.2：仅当 ``status`` / ``owner`` 变时才改 ``plans.json``。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plans_path = Path(store.path_for("plans", project_root=root))
    before = plans_path.read_bytes()

    result = plan.set_target(root, "login.T1", note="换个思路")

    assert result["changed"] is False
    assert plans_path.read_bytes() == before  # plans.json 没被重写
    event = plan.read_events(root)[-1]
    assert event["op"] == "set"
    assert "from" not in event and "to" not in event
    assert event["note"] == "换个思路"


def test_set_status_records_op_set(tmp_path):
    """``op`` 双轨（§2.5.1）：走 ``set`` 就记 ``set``，不由 (from,to) 反推。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.set_target(root, "login.T1", status="doing")
    event = plan.read_events(root)[-1]
    assert event["op"] == "set"
    assert (event["from"], event["to"]) == ("todo", "doing")


def test_vocabularies_are_enforced(tmp_path):
    """三张封闭词表（§4）—— 按实体级别校验。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    with pytest.raises(PlanError):
        plan.set_target(root, "login", status="done")  # 计划的词表里没有 done
    with pytest.raises(PlanError):
        plan.set_target(root, "login.T1", status="reached")  # 任务的词表里没有 reached


def test_fields_belong_to_their_level(tmp_path):
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    with pytest.raises(PlanError):
        plan.set_target(root, "login.T1", goal="x")  # goal 只属于计划
    with pytest.raises(PlanError):
        plan.set_target(root, "login", owner="agent")  # owner 只属于任务
    with pytest.raises(PlanError):
        plan.set_target(root, "login.T1", owner="robot")


def test_missing_targets_raise(tmp_path):
    root = str(tmp_path)
    with pytest.raises(PlanError):
        plan.show_plan(root, "nope")
    _block(root)
    with pytest.raises(PlanError):
        plan.set_target(root, "login.T9", status="doing")
    with pytest.raises(PlanError):
        plan.show_task(root, "login", "T9")


# ---------------------------------------------------------------- 读视图
def test_list_view_has_no_goal(tmp_path):
    """列表视图：``id`` / ``title`` / ``status`` / 完成度 —— **不含 goal**（§2.6 规则 3）。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.add_task(root, "login.T2", title="b")
    plan.done(root, "login.T1")

    rows = plan.list_plans(root)
    assert rows == [
        {
            "id": "login",
            "title": "登录模块",
            "status": "open",
            "tasks_total": 2,
            "tasks_done": 1,
        }
    ]


def test_list_status_filter(tmp_path):
    root = str(tmp_path)
    _block(root, "login")
    _block(root, "demo")
    plan.drop(root, "demo", note="不做")
    assert [row["id"] for row in plan.list_plans(root)] == ["login", "demo"]
    assert [row["id"] for row in plan.list_plans(root, status="dropped")] == ["demo"]


def test_show_plan_carries_goal_and_criteria(tmp_path):
    root = str(tmp_path)
    _block(root, criteria=["判据一", "判据二"])
    plan.add_task(root, "login.T1", title="骨架", done_when="空场景能跑")
    block = plan.show_plan(root, "login")
    assert block["goal"] == "让用户能安全登录"
    assert [criterion["text"] for criterion in block["criteria"]] == ["判据一", "判据二"]
    assert block["tasks"][0]["done_when"] == "空场景能跑"


def test_show_task_is_a_handoff_packet(tmp_path):
    """``show <plan>.<task>`` 一次给全：状态 + 完整轨迹（§2.3 步 2）。"""
    root = str(tmp_path)
    _block(root)
    plan.add_task(root, "login.T1", title="a")
    plan.claim(root, "login.T1")
    plan.done(root, "login.T1", note="跑通")

    packet = plan.show_task(root, "login", "T1")
    assert packet["status"] == "done"
    assert [event["op"] for event in packet["events"]] == ["add", "claim", "done"]
    assert packet["events"][-1]["note"] == "跑通"


def test_timeline_default_is_bounded(tmp_path):
    """``log`` 跨计划、无界 → 默认封顶 ``--tail 20``（§2.4）。"""
    root = str(tmp_path)
    _block(root)
    for index in range(25):
        plan.add_task(root, f"login.T{index}", title=f"t{index}")

    assert len(plan.read_events(root)) == 26  # 1 建计划 + 25 建任务
    assert len(plan.timeline(root)) == plan.DEFAULT_LOG_TAIL
    assert len(plan.timeline(root, show_all=True)) == 26
    assert len(plan.timeline(root, tail=5)) == 5
    assert len(plan.timeline(root, plan="login")) == 20


def test_overview_counts_and_buckets(tmp_path):
    """汇总面（§6.4）：计数 / 分布 / 在做·卡住 —— 语义在 `plan` 侧，消费方只渲染。"""
    root = str(tmp_path)
    _block(root, "login")
    _block(root, "demo")
    plan.add_task(root, "login.T1", title="登录骨架")
    plan.add_task(root, "login.T2", title="找回密码")
    plan.add_task(root, "demo.T1", title="样板")
    plan.claim(root, "login.T1")
    plan.block(root, "login.T2", note="等接口")
    plan.done(root, "demo.T1")
    plan.drop(root, "demo", note="不做了")

    view = plan.overview(root)
    assert view["plans"] == 2
    assert view["plan_status"] == {"open": 1, "reached": 0, "dropped": 1}
    assert view["tasks"] == {"total": 3, "done": 1, "doing": 1, "todo": 0, "blocked": 1}
    assert view["doing"] == [{"address": "login.T1", "title": "登录骨架"}]
    assert view["blocked"] == [{"address": "login.T2", "title": "找回密码"}]
    assert view["latest_activity"] == plan.read_events(root)[-1]["ts"]


def test_overview_without_data_is_empty(tmp_path):
    """缺文件 = 空数据（§6.1）—— 汇总面照给零值，不报错。"""
    assert plan.overview(str(tmp_path)) == {
        "plans": 0,
        "plan_status": {"open": 0, "reached": 0, "dropped": 0},
        "tasks": {"total": 0, "done": 0, "doing": 0, "todo": 0, "blocked": 0},
        "latest_activity": None,
        "doing": [],
        "blocked": [],
    }


def test_overview_orders_by_recent_activity(tmp_path, monkeypatch):
    """`doing` / `blocked` **已排序**：该 task 轨迹末条事件的 `ts` 降序（§4.1 末）。"""
    root = str(tmp_path)
    ticks = iter(range(60))
    monkeypatch.setattr(plan, "_now", lambda: f"2026-10-10T00:00:{next(ticks):02d}+08:00")

    _block(root)
    for index in (1, 2, 3, 4):
        plan.add_task(root, f"login.T{index}", title=f"t{index}")
    for index in (1, 2, 3, 4):
        plan.claim(root, f"login.T{index}")
    plan.set_target(root, "login.T2", note="再看一眼")  # T2 最后动过

    assert [row["address"] for row in plan.overview(root)["doing"]] == [
        "login.T2",
        "login.T4",
        "login.T3",
        "login.T1",
    ]


def test_overview_ties_fall_back_to_address(tmp_path, monkeypatch):
    """同 `ts` → 按地址升序；**没进过 `plan_log` 的 task 排末尾** —— 全程确定（L1 G）。"""
    root = str(tmp_path)
    monkeypatch.setattr(plan, "_now", lambda: "2026-10-10T00:00:00+08:00")
    _block(root)
    for name in ("T2", "T1"):
        plan.add_task(root, f"login.{name}", title=name)
        plan.claim(root, f"login.{name}")

    # 手写一份「有 doing 但无事件」的数据 —— 正常路径下每个 task 都有 add 事件
    store.write(
        "plans",
        {
            "version": 1,
            "plans": [
                {
                    "id": "login",
                    "title": "登录模块",
                    "goal": "让用户能安全登录",
                    "criteria": [{"text": "能登录"}],
                    "status": "open",
                    "tasks": [
                        {"id": "T1", "title": "T1", "status": "doing"},
                        {"id": "T2", "title": "T2", "status": "doing"},
                        {"id": "T9", "title": "T9", "status": "doing"},
                    ],
                }
            ],
        },
        project_root=root,
    )
    assert [row["address"] for row in plan.overview(root)["doing"]] == [
        "login.T1",
        "login.T2",
        "login.T9",
    ]


# ---------------------------------------------------------------- CLI 层
def _chdir(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def test_cli_plan_flow(tmp_path, monkeypatch, capsys):
    root = _chdir(tmp_path, monkeypatch)

    assert (
        cli.main(
            ["plan", "add", "login", "--title", "登录模块", "--goal", "让用户能安全登录", "--criteria", "能登录"]
        )
        == 0
    )
    assert cli.main(["plan", "add", "login.T1", "--title", "骨架"]) == 0
    capsys.readouterr()

    assert cli.main(["plan", "claim", "login.T1"]) == 0
    assert cli.main(["plan", "done", "login.T1", "--note", "跑通"]) == 0
    assert "done login.T1" in capsys.readouterr().out

    # 项目根 = CWD（§2.4）
    assert (root / ".forge" / "plans.json").is_file()
    assert (root / ".forge" / "plan_log.jsonl").is_file()

    assert cli.main(["plan", "show"]) == 0
    out = capsys.readouterr().out
    assert "登录模块" in out and "1/1" in out
    assert "让用户能安全登录" not in out  # 列表不带 goal

    assert cli.main(["plan", "show", "login.T1"]) == 0
    assert "跑通" in capsys.readouterr().out


def test_cli_plan_json(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["plan", "add", "login", "--title", "t", "--goal", "g", "--criteria", "c"])
    capsys.readouterr()
    assert cli.main(["plan", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["id"] == "login"


def test_cli_plan_add_requires_goal_and_criteria(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    assert cli.main(["plan", "add", "login", "--title", "t"]) == 1
    assert "--goal" in capsys.readouterr().err
    assert cli.main(["plan", "add", "login", "--title", "t", "--goal", "g"]) == 1
    assert "criteria" in capsys.readouterr().err


def test_cli_reject_requires_note(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["plan", "add", "login", "--title", "t", "--goal", "g", "--criteria", "c"])
    cli.main(["plan", "add", "login.T1", "--title", "a"])
    capsys.readouterr()
    assert cli.main(["plan", "reject", "login.T1"]) == 1
    assert "--note" in capsys.readouterr().err


def test_cli_set_requires_something(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["plan", "add", "login", "--title", "t", "--goal", "g", "--criteria", "c"])
    capsys.readouterr()
    assert cli.main(["plan", "set", "login"]) == 1
    assert "至少给一项" in capsys.readouterr().err


def test_cli_log_view(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["plan", "add", "login", "--title", "t", "--goal", "g", "--criteria", "c"])
    cli.main(["plan", "add", "login.T1", "--title", "a"])
    capsys.readouterr()
    assert cli.main(["plan", "log"]) == 0
    out = capsys.readouterr().out
    assert "时间线" in out and "login.T1" in out
