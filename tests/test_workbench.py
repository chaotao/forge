"""``workbench`` 的确定性回归（``08-workbench`` 附录 step 5 · L1 约定 **G**）。

覆盖：空清单 / 排序 / 三个分区各态 / 截断（``⋯+K``）/ 坏分区隔离 / aiwf 读不到 /
未受管 / 清单坏降级 / **两个出口同形** / 文本与 ``--json`` 渲染。

上游一律**桩**（``StubRunner`` 喂 JSON），**不真调 aiwf**（``conftest`` 纪律）。
"""
import json
import os
from types import SimpleNamespace

import pytest

from forge import cli, inbox, plan, store, workbench

REGISTERED_AT = "2026-10-10T00:00:00+08:00"


# ------------------------------------------------------------------ 夹具
class StubRunner:
    """桩 aiwf：按 argv 前缀给 JSON，并记录调用（L1 约定 **G**）。"""

    def __init__(self, workflows=(), runs=(), views=None):
        self.workflows = list(workflows)
        self.runs = list(runs)
        self.views = dict(views or {})
        self.calls = []

    def __call__(self, argv, *, cwd):
        self.calls.append((list(argv), cwd))
        if argv[0] == "workflows":
            payload = self.workflows
        elif argv[:2] == ["runs", "list"]:
            payload = self.runs
        elif argv[:2] == ["runs", "show"]:
            payload = self.views.get(argv[-1], {})
        else:  # pragma: no cover - 不该发生
            payload = []
        return SimpleNamespace(stdout=json.dumps(payload, ensure_ascii=False), returncode=0)


def _project(root, alias, blueprint="minimal"):
    return {
        "path": str(root),
        "alias": alias,
        "blueprint": blueprint,
        "stage": "scaffolded",
        "registered_at": REGISTERED_AT,
    }


def _write_registry(rows, focus=None):
    doc = {"version": 1, "projects": rows}
    if focus is not None:
        doc["focus"] = focus
    store.write("projects", doc)


def _view(**overrides):
    """一个现成的 ``ProjectStatus``（渲染层测试用）。"""
    base = {
        "alias": "demo",
        "path": "F:\\x",
        "blueprint": "minimal",
        "registered_at": REGISTERED_AT,
        "focus": False,
        "plan": {
            "plans": 1,
            "plan_status": {"open": 1, "reached": 0, "dropped": 0},
            "tasks": {"total": 3, "done": 1, "doing": 1, "todo": 1, "blocked": 0},
            "latest_activity": "2026-10-10T01:00:00+08:00",
            "doing": [{"address": "core.T1", "title": "甲"}],
            "blocked": [],
            "truncated": {"doing": 0, "blocked": 0},
            "error": None,
        },
        "inbox": {
            "total": 2,
            "status": {"open": 2, "handed": 0, "dropped": 0},
            "latest_created": "2026-10-10",
            "oldest_open": "2026-10-09",
            "error": None,
        },
        "aiwf": {
            "workflows": 1,
            "runs_total": 1,
            "running": [
                {
                    "run_id": "r",
                    "workflow_id": "std.x",
                    "title": "实例甲",
                    "status": "running",
                    "started_at": "2026-10-10T00:00:00+08:00",
                    "stages_done": 2,
                    "stages_total": 8,
                    "current": "角色设计",
                    "wait": "review",
                }
            ],
            "last": None,
            "error": None,
        },
        "error": None,
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ collect
def test_empty_registry():
    assert workbench.collect() == {"focus": None, "projects": []}


def test_sort_focus_first_then_alias(tmp_path):
    for name in ("b", "a", "c"):
        (tmp_path / name).mkdir()
    _write_registry(
        [_project(tmp_path / "b", "b"), _project(tmp_path / "a", "a"), _project(tmp_path / "c", "c")],
        focus="c",
    )
    view = workbench.collect(runner=StubRunner())
    assert [p["alias"] for p in view["projects"]] == ["c", "a", "b"]
    assert view["projects"][0]["focus"] is True
    assert view["focus"] == "c"


def test_missing_registry_is_empty_not_error():
    assert workbench.collect(runner=StubRunner())["projects"] == []


# ------------------------------------------------------------------ plan 分区
def test_plan_section_counts_and_truncate(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    plan.add_plan(str(root), "core", title="核心", goal="g", criteria=["c"])
    for index in range(5):
        plan.add_task(str(root), f"core.T{index}", title=f"任务{index}")
        plan.claim(str(root), f"core.T{index}")

    section = workbench.collect_project(str(root), runner=StubRunner())["plan"]
    assert section["plans"] == 1
    assert section["plan_status"] == {"open": 1, "reached": 0, "dropped": 0}
    assert section["tasks"]["doing"] == 5
    assert len(section["doing"]) == workbench.DYNAMIC_LIMIT
    assert section["truncated"]["doing"] == 5 - workbench.DYNAMIC_LIMIT
    assert section["latest_activity"] is not None
    assert section["error"] is None


def test_plan_section_zero_plans(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    section = workbench.collect_project(str(root), runner=StubRunner())["plan"]
    assert section["plans"] == 0
    assert section["latest_activity"] is None


# ------------------------------------------------------------------ inbox 分区
def test_inbox_section_distribution_and_dates(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    inbox.add(str(root), "甲")
    inbox.add(str(root), "乙")
    section = workbench.collect_project(str(root), runner=StubRunner())["inbox"]
    assert section["total"] == 2
    assert section["status"] == {"open": 2, "handed": 0, "dropped": 0}
    assert section["latest_created"] is not None
    assert section["oldest_open"] is not None
    assert section["error"] is None


def test_inbox_oldest_open_absent_when_no_open(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    item = inbox.add(str(root), "要走")
    inbox.hand(str(root), item["id"], target="别处")
    section = workbench.collect_project(str(root), runner=StubRunner())["inbox"]
    assert section["total"] == 1
    assert section["status"] == {"open": 0, "handed": 1, "dropped": 0}
    assert section["oldest_open"] is None
    assert section["latest_created"] is not None


# ------------------------------------------------------------------ aiwf 分区
def test_aiwf_section_running_wait(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    runner = StubRunner(
        workflows=[{"id": "std.x", "name": "X"}],
        runs=[
            {
                "run_id": "r1",
                "workflow_id": "std.x",
                "status": "running",
                "started_at": "2026-10-10T00:00:00+08:00",
                "duration_ms": 1,
                "node_count": 2,
                "parent_run": None,
            }
        ],
        views={
            "r1": {
                "run_id": "r1",
                "workflow_id": "std.x",
                "status": "running",
                "phase": "executing",
                "stages": [
                    {"name": "S0", "status": "done"},
                    {"name": "S1", "status": "waiting", "action": {"kind": "review"}},
                    {"name": "S2", "status": "pending"},
                ],
            }
        },
    )
    section = workbench.collect_project(str(root), runner=runner)["aiwf"]
    assert section["workflows"] == 1
    assert section["runs_total"] == 1
    assert len(section["running"]) == 1
    assert section["running"][0]["wait"] == "review"
    assert section["running"][0]["current"] == "S1"
    assert section["running"][0]["stages_done"] == 1
    assert section["running"][0]["stages_total"] == 3
    assert section["error"] is None


def test_aiwf_missing_binary_degrades(tmp_path):
    root = tmp_path / "p"
    root.mkdir()

    def missing(argv, *, cwd):
        raise FileNotFoundError(2, "no aiwf")

    view = workbench.collect_project(str(root), runner=missing)
    assert view["aiwf"]["error"] == "aiwf 不在 PATH"
    assert view["aiwf"]["workflows"] is None
    assert view["plan"]["error"] is None  # ★ 其余分区照常
    assert view["inbox"]["error"] is None


# ------------------------------------------------------------------ 容错隔离
def test_bad_plan_section_isolated(tmp_path):
    root = tmp_path / "p"
    (root / ".forge").mkdir(parents=True)
    (root / ".forge" / "plans.json").write_text("{ broken", encoding="utf-8")
    view = workbench.collect_project(str(root), runner=StubRunner())
    assert view["plan"]["error"] is not None
    assert view["plan"]["plans"] is None  # ★ 不取 0（L1 H2）
    assert view["inbox"]["error"] is None
    assert view["aiwf"]["error"] is None
    assert view["error"] is None


def test_bad_project_does_not_break_others(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "bad"
    (bad / ".forge").mkdir(parents=True)
    (bad / ".forge" / "inbox.json").write_text("nope", encoding="utf-8")
    _write_registry([_project(good, "good"), _project(bad, "bad")])

    view = workbench.collect(runner=StubRunner())
    by_alias = {p["alias"]: p for p in view["projects"]}
    assert len(view["projects"]) == 2
    assert by_alias["bad"]["inbox"]["error"] is not None
    assert by_alias["good"]["inbox"]["error"] is None


# ------------------------------------------------------------------ 本项目视图
def test_unmanaged_project(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    _write_registry([])
    view = workbench.collect_project(str(root), runner=StubRunner())
    assert view["alias"] is None
    assert view["path"] == os.path.abspath(str(root))
    assert view["plan"]["plans"] == 0  # ★ 未受管照常可用


def test_broken_registry_degrades_status(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    path = store.path_for("projects")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{ broken")
    view = workbench.collect_project(str(root), runner=StubRunner())
    assert view["alias"] is None  # ★ 降级未受管，不失败
    assert view["plan"]["error"] is None


def test_status_is_identical_slice_of_workbench(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    plan.add_plan(str(root), "core", title="核心", goal="g", criteria=["c"])
    inbox.add(str(root), "念头")
    _write_registry([_project(root, "demo")])
    runner = StubRunner(workflows=[{"id": "w", "name": "W"}])

    whole = workbench.collect(runner=runner)
    piece = workbench.collect_project(str(root), runner=runner)
    # ★ 本项目视图 = 全局里那一段（同一份数据，L1 D1）
    assert piece == whole["projects"][0]


# ------------------------------------------------------------------ 渲染
def test_emit_status_text(capsys):
    cli._emit_project(_view(), with_identity=False)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "F:\\x"  # 去身份行
    assert lines[1].startswith("  plan   ")
    assert "1 份计划（open 1 · reached 0 · dropped 0）" in lines[1]
    assert "· 上次变动 2026-10-10T01:00:00+08:00" in lines[1]
    assert lines[2] == "         在做 1  core.T1 甲"
    assert lines[3].startswith("  inbox  ")
    assert "2 条（open 2 · handed 0 · dropped 0）" in lines[3]
    assert "最近 2026-10-10 · 最老未处理 2026-10-09" in lines[3]
    assert lines[4].startswith("  aiwf   ")
    assert lines[5] == "         ⏳ 实例甲 在等你：review（角色设计 · 2/8）"


def test_emit_workbench_text(capsys):
    cli._emit_workbench({"focus": None, "projects": [_view(), _view(alias="zzz")]})
    out = capsys.readouterr().out
    assert "2 个项目 · 焦点 无" in out
    assert "demo · minimal · F:\\x" in out  # 全局段带身份行
    assert "zzz · minimal · F:\\x" in out


def test_emit_workbench_empty(capsys):
    cli._emit_workbench({"focus": None, "projects": []})
    assert "还没有受管项目" in capsys.readouterr().out


def test_emit_unmanaged_marker(capsys):
    cli._emit_project(_view(alias=None), with_identity=False)
    assert "F:\\x（未受管）" in capsys.readouterr().out


def test_emit_read_failure(capsys):
    broken = {
        "plans": None,
        "plan_status": None,
        "tasks": None,
        "latest_activity": None,
        "doing": [],
        "blocked": [],
        "truncated": {"doing": 0, "blocked": 0},
        "error": "坏数据",
    }
    cli._emit_project(_view(plan=broken), with_identity=False)
    assert "读取失败：坏数据" in capsys.readouterr().out


def test_emit_truncation_hint(capsys):
    section = _view()["plan"]
    section = dict(section)
    section["doing"] = [
        {"address": f"core.T{i}", "title": f"任务{i}"} for i in range(3)
    ]
    section["truncated"] = {"doing": 4, "blocked": 0}
    cli._emit_project(_view(plan=section), with_identity=False)
    out = capsys.readouterr().out
    assert "在做 7" in out
    assert "⋯ 还有 4 条 → forge plan show" in out


def test_emit_untouched_aiwf(capsys):
    cli._emit_project(_view(aiwf={"workflows": 0, "runs_total": 0, "running": [], "last": None, "error": None}),
                      with_identity=False)
    assert "流程 0 · 运行 0 —— 未接流程" in capsys.readouterr().out


# ------------------------------------------------------------------ 命令层
def test_cmd_status_json(monkeypatch, capsys):
    view = _view()
    monkeypatch.setattr(workbench, "collect_project", lambda root, **kw: view)
    rc = cli.main(["status", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == view


def test_cmd_workbench_json(monkeypatch, capsys):
    view = {"focus": None, "projects": [_view()]}
    monkeypatch.setattr(workbench, "collect", lambda **kw: view)
    rc = cli.main(["workbench", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == view


def test_cmd_status_uses_cwd(monkeypatch, tmp_path, capsys):
    captured = {}

    def fake(root, **kw):
        captured["root"] = root
        return _view()

    monkeypatch.setattr(workbench, "collect_project", fake)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status"]) == 0
    assert captured["root"] == os.getcwd()


# ------------------------------------------------------- inbox 最近加入（v12）
def test_inbox_recent_three_most_recent(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    for index in range(5):
        inbox.add(str(root), f"念头{index}")
    recent = workbench.collect_project(str(root), runner=StubRunner())["inbox"]["recent"]
    assert len(recent) == workbench.INBOX_RECENT_LIMIT
    assert [row["id"] for row in recent] == ["INB-005", "INB-004", "INB-003"]
    assert recent[0]["text"] == "念头4"


def test_inbox_recent_only_gives_short_text(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    inbox.add(str(root), "x" * 200)
    row = workbench.collect_project(str(root), runner=StubRunner())["inbox"]["recent"][0]
    assert row["text"].endswith("…")


# ------------------------------------------------------- 最近动态时间线（v12）
def test_activity_entries_cover_three_sources(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    plan.add_plan(str(root), "core", title="核心", goal="g", criteria=["c"])
    plan.add_task(str(root), "core.T1", title="甲")
    plan.claim(str(root), "core.T1")
    inbox.add(str(root), "想法")
    runner = StubRunner(
        workflows=[{"id": "std.x", "name": "X"}],
        runs=[{
            "run_id": "r1", "workflow_id": "std.x", "title": "实例甲",
            "status": "running", "started_at": "2026-10-10T00:00:00+08:00",
        }],
        views={"r1": {
            "run_id": "r1", "workflow_id": "std.x",
            "stages": [{"name": "S0", "status": "waiting", "action": {"kind": "review"}}],
        }},
    )
    view = workbench.collect_project(str(root), runner=runner)
    kinds = {entry["kind"] for entry in view["recent"]}
    assert kinds == {"plan", "inbox", "aiwf"}
    aiwf = next(e for e in view["recent"] if e["kind"] == "aiwf")
    assert aiwf["title"] == "实例甲"
    assert aiwf["wait"] == "review"
    # ★ 降序：``aiwf``（2026-10-10）排在 ``inbox`` / ``plan`` 的日期之前
    ts = [entry.get("ts") or "" for entry in view["recent"]]
    assert ts == sorted(ts, reverse=True)


def test_merge_recent_sorts_desc_and_caps():
    projects = [
        {"recent": [{"ts": "2026-01-01", "kind": "plan", "project": "a"}]},
        {"recent": [
            {"ts": "2026-03-01", "kind": "plan", "project": "b"},
            {"ts": "2026-02-01", "kind": "inbox", "project": "b"},
        ]},
    ]
    merged = workbench.merge_recent(projects)
    assert [e["ts"] for e in merged] == ["2026-03-01", "2026-02-01", "2026-01-01"]


def test_merge_recent_respects_limit():
    projects = [{"recent": [
        {"ts": f"2026-01-{day:02d}", "kind": "plan", "project": "a"} for day in range(1, 11)
    ]}]
    assert len(workbench.merge_recent(projects)) == workbench.RECENT_LIMIT


# ------------------------------------------------------- aiwf 实例标题（v12）
def test_aiwf_title_comes_from_runs_list(tmp_path):
    """★ ``title`` 来自 ``runs list``（2026-10-11）—— aiwf 已解析好，本层**原样透传**（L1 D）。"""
    root = tmp_path / "p"
    root.mkdir()
    runner = StubRunner(
        workflows=[{"id": "std.x", "name": "X"}],
        runs=[{
            "run_id": "r1", "workflow_id": "std.x", "title": "D01 · 第1集",
            "status": "running", "started_at": "2026-10-10T00:00:00+08:00",
        }],
        views={"r1": {"run_id": "r1", "workflow_id": "std.x",
                      "stages": [{"name": "S0", "status": "pending"}]}},
    )
    section = workbench.collect_project(str(root), runner=runner)["aiwf"]
    assert section["running"][0]["title"] == "D01 · 第1集"


def test_finished_run_also_carries_title(tmp_path):
    """★ ``last``（已结束的 run）**也有** ``title`` —— 它来自 ``runs list``，不是 ``--view``。"""
    root = tmp_path / "p"
    root.mkdir()
    runner = StubRunner(
        workflows=[{"id": "std.x", "name": "X"}],
        runs=[{
            "run_id": "r1", "workflow_id": "std.x", "title": "20261007-aaaaaa",
            "status": "completed", "started_at": "2026-10-07T00:00:00+08:00",
        }],
    )
    section = workbench.collect_project(str(root), runner=runner)["aiwf"]
    assert section["last"]["title"] == "20261007-aaaaaa"


def test_aiwf_title_absent_is_none(tmp_path):
    """``runs list`` 没给 ``title``（形状不符）⇒ ``None``，本层不猜（L1 H2）。"""
    root = tmp_path / "p"
    root.mkdir()
    runner = StubRunner(
        workflows=[{"id": "std.x", "name": "X"}],
        runs=[{
            "run_id": "r1", "workflow_id": "std.x", "status": "running",
            "started_at": "2026-10-10T00:00:00+08:00",
        }],
        views={"r1": {"run_id": "r1", "workflow_id": "std.x", "stages": []}},
    )
    section = workbench.collect_project(str(root), runner=runner)["aiwf"]
    assert section["running"][0]["title"] is None


# ------------------------------------------------------- 渲染：时间线（v12）
def test_emit_aiwf_uses_title(capsys):
    aiwf = _view()["aiwf"]
    aiwf = dict(aiwf)
    aiwf["running"] = [dict(aiwf["running"][0], title="D01 · 第1集")]
    cli._emit_project(_view(aiwf=aiwf), with_identity=False)
    assert "⏳ D01 · 第1集 在等你：review（角色设计 · 2/8）" in capsys.readouterr().out


def test_emit_recent_block(capsys):
    entries = [
        {"ts": "2026-10-10T14:35:35", "kind": "aiwf", "project": "drama",
         "state": "running", "title": "D01 · 第1集", "current": "S1 编剧",
         "stages_done": 1, "stages_total": 8, "wait": "review"},
        {"ts": "2026-10-09", "kind": "inbox", "project": "mc", "id": "INB-015",
         "text": "把动力轴做成通用旋转输出"},
    ]
    cli._emit_recent(entries)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "最近动态"
    assert "2026-10-10 14:35  drama" in out
    assert "⏳ D01 · 第1集 在等你：review（S1 编剧 · 1/8）" in out
    assert "记了念头 INB-015" in out


def test_emit_status_includes_recent(capsys):
    entries = [{"ts": "2026-10-10T00:00:00", "kind": "aiwf", "project": "demo",
                "state": "running", "title": "T", "current": "S", "wait": None,
                "stages_done": 0, "stages_total": 1}]
    cli._emit_project(_view(recent=entries), with_identity=False, with_recent=True)
    lines = capsys.readouterr().out.splitlines()
    assert lines[1] == "最近动态"
    assert "T 运行中（S · 0/1）" in lines[2]
    assert lines[3].startswith("  plan   ")


def test_emit_workbench_merges_recent(capsys):
    cli._emit_workbench({
        "focus": None,
        "projects": [
            _view(recent=[{"ts": "2026-10-08", "kind": "plan", "project": "demo",
                           "doing": [], "blocked": [], "truncated": {}}]),
            _view(alias="zzz", recent=[{"ts": "2026-10-09", "kind": "plan",
                                        "project": "zzz", "doing": [], "blocked": [],
                                        "truncated": {}}]),
        ],
    })
    out = capsys.readouterr().out
    assert "最近动态" in out
    assert out.index("2026-10-09") < out.index("2026-10-08")  # ★ 时间降序
    assert "计划有变动" in out

