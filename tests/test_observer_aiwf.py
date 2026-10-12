"""``observer`` 的 ``aiwf`` 一路 —— 桩回归（``04-observer.md`` 附录 step 2）。

两条纪律（L1 约定 **G**：确定性可回归）：

- **不真调 ``aiwf``** —— 一律注入 ``runner`` 桩（§4.6 的**唯一子进程出口**）；
- 桩**喂 JSON 文本**（与真实 ``--json`` 同形），**不喂定宽列**。

覆盖：空 ``[]`` / 非法 JSON / 路径不可达 / 命令缺失 / 超时 / `--view` JSON /
非数组输出 / ``AIWF_HOME`` 清理 / CWD 传递 / 不重排 / 多 ``waiting`` 取首个。
"""
import json
import os
import subprocess

import pytest

from forge import observer


# ------------------------------------------------------------------ 桩
def make_runner(responses):
    """造一个 ``runner`` 桩：按 ``argv`` 查表返回 ``CompletedProcess``。

    ``responses`` 的值可以是**异常实例** —— 桩会直接抛出（模拟命令缺失 / 超时）。
    查表未命中即 ``AssertionError``（「不该调这条命令」也是断言）。
    """
    calls = []

    def runner(argv, *, cwd):
        calls.append((tuple(argv), cwd))
        key = tuple(argv)
        if key not in responses:
            raise AssertionError("未预期的 aiwf 调用: %r" % (argv,))
        value = responses[key]
        if isinstance(value, Exception):
            raise value
        return subprocess.CompletedProcess(
            ["aiwf", *argv], 0, stdout=value, stderr=""
        )

    runner.calls = calls
    return runner


WORKFLOWS = json.dumps(
    [{"id": "std.novel-writing", "name": "AI 长篇小说创作"}], ensure_ascii=False
)

RUN_RUNNING = {
    "started_at": "2026-10-08T16:56:19",
    "run_id": "20261008-165619-451a53",
    "workflow_id": "std.novel-writing",
    "title": "悬疑推理 · 名侦探柯南",   # ★ aiwf ``run_title()`` 解析好的实例名（runs list 里）
    "status": "running",
    "duration_ms": None,
    "node_count": 12,
    "parent_run": None,
}
RUN_DONE = {
    "started_at": "2026-10-07T10:00:00",
    "run_id": "20261007-100000-aaaaaa",
    "workflow_id": "std.novel-writing",
    "title": "20261007-100000-aaaaaa",   # ★ 旧 run：没 title ⇒ aiwf 落到 run_id（永不为空）
    "status": "completed",
    "duration_ms": 1234,
    "node_count": 3,
    "parent_run": None,
}
RUN_FAILED = {
    "started_at": "2026-10-06T09:00:00",
    "run_id": "20261006-090000-bbbbbb",
    "workflow_id": "std.other",
    "title": "另一部作品",
    "status": "failed",
    "duration_ms": 99,
    "node_count": 1,
    "parent_run": None,
}


def runs_json(*rows):
    return json.dumps(list(rows), ensure_ascii=False)


#: ``runs show --view --json`` 的真实形状（实测，``04-observer.md`` §4.3）。
VIEW_RUNNING = json.dumps(
    {
        "run_id": "20261008-165619-451a53",
        "workflow_id": "std.novel-writing",
        "status": "running",
        "phase": "awaiting_review",
        "stages": [
            {"name": "立项构思", "nodes": ["concept"], "status": "done",
             "detail": "…", "gate": True, "action": None},
            {"name": "世界观构建", "nodes": ["worldbuild"], "status": "done",
             "detail": "…", "gate": True, "action": None},
            {"name": "角色设计", "nodes": ["characters"], "status": "waiting",
             "detail": "…", "gate": True,
             "action": {"kind": "review", "run_id": "20261008-165619-451a53",
                        "workflow_id": "std.novel-writing", "id": "e77c"}},
            {"name": "故事大纲", "nodes": ["outline"], "status": "pending",
             "detail": "…", "gate": True, "action": None},
        ],
    },
    ensure_ascii=False,
)

VIEW_KEY = ("runs", "show", "--view", "--json", "20261008-165619-451a53")


@pytest.fixture
def project(tmp_path):
    """一个**真实存在**的项目目录（``isdir`` 要过 —— §4.5 步 1）。"""
    path = tmp_path / "proj"
    path.mkdir()
    return path


# ------------------------------------------------------- ① 空集不是错误
def test_no_aiwf_project_is_zero_not_error(project):
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): "[]",
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section == {
        "workflows": 0,
        "runs_total": 0,
        "running": [],
        "last": None,
        "error": None,
    }
    # ★ 空集时**不该**去调 --view（逐 run 一次子进程，L1 A1）
    assert len(runner.calls) == 2


def test_workflows_and_runs_counted_independently(project):
    """``workflows>0`` 且 ``runs_total=0`` = **装了流程没跑** —— 两态只能靠它区分（§3.1）。"""
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): "[]",
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["workflows"] == 1
    assert section["runs_total"] == 0
    assert section["error"] is None


# ------------------------------------------------------- ② running 取 --view
def test_running_run_gets_view_fields(project):
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: VIEW_RUNNING,
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"] is None
    assert section["runs_total"] == 1
    run = section["running"][0]
    assert run["run_id"] == "20261008-165619-451a53"
    assert run["workflow_id"] == "std.novel-writing"
    assert run["status"] == "running"          # ★ 透传 aiwf 的词，不翻译
    assert run["started_at"] == "2026-10-08T16:56:19"
    assert run["stages_done"] == 2
    assert run["stages_total"] == 4
    assert run["current"] == "角色设计"
    assert run["wait"] == "review"             # ★ action.kind 透传
    assert run["title"] == "悬疑推理 · 名侦探柯南"   # ★ 来自 runs list（**不是** --view）


def test_title_comes_from_runs_list_not_view(project):
    """★ ``title`` **只从 ``runs list`` 取**（2026-10-11）—— ``--view`` 里也带 ``title``，
    但本模块**不取**（同一事实一处取，L1 D）。喂两个不同的值，证明取的是 runs list 那个。
    """
    view = json.dumps(
        {
            "run_id": "20261008-165619-451a53",
            "workflow_id": "std.novel-writing",
            "title": "（--view 里的名字，应被忽略）",
            "status": "running",
            "phase": "awaiting_review",
            "stages": [{"name": "角色设计", "nodes": ["characters"], "status": "waiting",
                        "detail": "…", "gate": True, "action": {"kind": "review"}}],
        },
        ensure_ascii=False,
    )
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: view,
    })

    run = observer.observe(str(project), runner=runner)["aiwf"]["running"][0]
    assert run["title"] == "悬疑推理 · 名侦探柯南"   # ← 取 runs list 的，不取 --view 的


def test_old_run_title_is_its_run_id(project):
    """★ 历史 run 没 title ⇒ aiwf 已落到 ``run_id`` ⇒ 本模块**原样透传**（主人 2026-10-11 预期）。"""
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_DONE),
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["last"]["title"] == "20261007-100000-aaaaaa"


def test_last_does_not_get_view_fields(project):
    """★ **只对 ``running`` 取 ``--view``**（§4.2）—— ``last`` 的 4 项缺省。"""
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_RUNNING, RUN_DONE),
        VIEW_KEY: VIEW_RUNNING,
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    last = section["last"]
    assert last["run_id"] == "20261007-100000-aaaaaa"
    assert last["status"] == "completed"
    assert last["stages_done"] is None
    assert last["stages_total"] is None
    assert last["current"] is None
    assert last["wait"] is None
    # ★ ``title`` 是例外：它来自 ``runs list``（**不是** ``--view``）⇒ ``last`` 也**有值**。
    assert last["title"] == "20261007-100000-aaaaaa"
    # 只调了一次 --view（就是那条 running 的）
    views = [c for c in runner.calls if c[0][:2] == ("runs", "show")]
    assert len(views) == 1


def test_last_is_first_finished_in_aiwf_order(project):
    """aiwf 已按 ``started_at`` 降序 ⇒ ``last`` = **首个非 running**（§7-7 不重排）。"""
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_DONE, RUN_FAILED, RUN_RUNNING),
        VIEW_KEY: VIEW_RUNNING,
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["last"]["run_id"] == "20261007-100000-aaaaaa"
    assert section["runs_total"] == 3
    assert [r["run_id"] for r in section["running"]] == [
        "20261008-165619-451a53"
    ]


def test_running_order_preserved(project):
    """★ **不重排 aiwf 给的行**（§7-7）—— 两条 running 保持原序。"""
    second = dict(RUN_RUNNING, run_id="20261008-170000-cccccc",
                  started_at="2026-10-08T17:00:00")
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): runs_json(RUN_RUNNING, second),
        VIEW_KEY: VIEW_RUNNING,
        ("runs", "show", "--view", "--json", "20261008-170000-cccccc"): VIEW_RUNNING,
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert [r["run_id"] for r in section["running"]] == [
        "20261008-165619-451a53",
        "20261008-170000-cccccc",
    ]


def test_waiting_stage_multiple_takes_first(project):
    """多个 ``waiting`` 阶段 → 取**第一个**（确定性）。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: json.dumps({
            "stages": [
                {"name": "A", "status": "waiting", "action": {"kind": "provide"}},
                {"name": "B", "status": "waiting", "action": {"kind": "review"}},
            ]
        }, ensure_ascii=False),
    })

    run = observer.observe(str(project), runner=runner)["aiwf"]["running"][0]

    assert run["current"] == "A"
    assert run["wait"] == "provide"
    assert run["stages_done"] == 0
    assert run["stages_total"] == 2


def test_running_without_waiting_stage(project):
    """running 但没有 ``waiting`` 阶段 → ``current`` / ``wait`` 为 ``None``。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: json.dumps(
            {"stages": [{"name": "A", "status": "pending", "action": None}]},
            ensure_ascii=False,
        ),
    })

    run = observer.observe(str(project), runner=runner)["aiwf"]["running"][0]

    assert run["current"] is None
    assert run["wait"] is None
    assert run["stages_total"] == 1


# ------------------------------------------------------- ③ 非法 JSON 整段降级
def _assert_degraded(section, reason_part):
    assert section["error"] is not None
    assert reason_part in section["error"]
    # ★ 数据字段一律 null（**不取 0** —— 0 是「真的没有」，会被读成谎报）
    assert section["workflows"] is None
    assert section["runs_total"] is None
    assert section["last"] is None
    assert section["running"] == []


def test_workflows_invalid_json_degrades_whole_section(project):
    runner = make_runner({("workflows", "--json"): "not json at all"})

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "无法解析")
    # ★ 且**不再往下调** —— 半份数据比没有更误导（§6）
    assert len(runner.calls) == 1


def test_runs_list_invalid_json_degrades_whole_section(project):
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): "(no runs)",   # ★ 旧的文本形态 ⇒ 不猜
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "无法解析")


def test_view_invalid_json_degrades_whole_section(project):
    """★ **三条命令任一**坏都整段降级（§6 表末行）。"""
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: "<html>oops</html>",
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "无法解析")


def test_workflows_not_array(project):
    runner = make_runner({("workflows", "--json"): '{"id": "std.demo"}'})

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "不是数组")


def test_runs_list_not_array(project):
    runner = make_runner({
        ("workflows", "--json"): WORKFLOWS,
        ("runs", "list", "--json"): '{"runs": []}',
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "不是数组")


def test_empty_output_is_an_error(project):
    runner = make_runner({("workflows", "--json"): "   "})

    section = observer.observe(str(project), runner=runner)["aiwf"]

    _assert_degraded(section, "无输出")


# ------------------------------------------------------- ④ 三分失败判别器
def test_unreachable_path_does_not_call_aiwf(tmp_path):
    """★ 步 1：路径不可达 ⇒ **不调 aiwf**（§4.5）—— 有确定性信号，不靠它报错。"""
    runner = make_runner({})

    section = observer.observe(str(tmp_path / "nope"), runner=runner)["aiwf"]

    assert runner.calls == []
    assert section["error"] == observer.ERROR_PATH_UNREACHABLE
    assert section["workflows"] is None
    assert section["running"] == []


def test_path_is_a_file_not_a_dir(tmp_path):
    """不是目录（是个文件）也算不可达 —— ``isdir`` 判据。"""
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    runner = make_runner({})

    section = observer.observe(str(f), runner=runner)["aiwf"]

    assert runner.calls == []
    assert section["error"] == observer.ERROR_PATH_UNREACHABLE


def test_aiwf_missing_from_path(project):
    runner = make_runner({
        ("workflows", "--json"): FileNotFoundError(2, "No such file or directory"),
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"] == observer.ERROR_AIWF_MISSING
    assert section["workflows"] is None


def test_timeout(project):
    runner = make_runner({
        ("workflows", "--json"): subprocess.TimeoutExpired("aiwf", 30),
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"] == observer.ERROR_TIMEOUT


def test_other_oserror(project):
    runner = make_runner({("workflows", "--json"): OSError(13, "Permission denied")})

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"].startswith("aiwf 调用失败")
    assert section["workflows"] is None


# ------------------------------------------------------- ⑤ 契约与调用细节
def test_observation_container_shape(project):
    """``Observation`` = 按数据源分字段的容器（§5）—— 本轮只有 ``aiwf``。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): "[]",
    })

    observation = observer.observe(str(project), runner=runner)

    assert set(observation) == {"aiwf"}
    assert set(observation["aiwf"]) == {
        "workflows", "runs_total", "running", "last", "error"
    }


def test_cwd_is_absolute_project_root(project):
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): "[]",
    })

    observer.observe(str(project), runner=runner)

    for _argv, cwd in runner.calls:
        assert cwd == os.path.abspath(str(project))


def test_relative_path_is_absolutised(project, monkeypatch):
    """传相对路径也归一成绝对 —— ``cwd=`` 要的是绝对路径。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): "[]",
    })
    monkeypatch.chdir(project)

    observer.observe(".", runner=runner)

    assert runner.calls[0][1] == os.path.abspath(str(project))


def test_default_runner_clears_aiwf_home_and_sets_cwd(monkeypatch, project):
    """★ 模块头 ⑤：``AIWF_HOME`` 非空时 aiwf **静默读错目录** ⇒ 采集必须清掉它。

    ★ 模块头 ⑥：同时要**强制子进程 UTF-8**、并**抓 bytes**（不 ``text=True``）。
    """
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=b"[]", stderr=b"")

    monkeypatch.setattr(observer.subprocess, "run", fake_run)
    monkeypatch.setenv("AIWF_HOME", str(project / "elsewhere"))

    observer._run_aiwf(["workflows", "--json"], cwd=str(project))

    assert seen["argv"] == ["aiwf", "workflows", "--json"]
    assert seen["kwargs"]["cwd"] == str(project)
    assert "AIWF_HOME" not in seen["kwargs"]["env"]
    # ★ 模块头 ⑥：子进程强制 UTF-8；且父侧不 text 解码（抓 bytes 自己解）
    assert seen["kwargs"]["env"]["PYTHONIOENCODING"] == "utf-8"
    assert "text" not in seen["kwargs"]
    assert "encoding" not in seen["kwargs"]
    assert seen["kwargs"]["check"] is False
    assert seen["kwargs"]["timeout"] == observer.DEFAULT_TIMEOUT


def test_no_aiwf_home_is_fine(monkeypatch, project):
    """环境本来就没 ``AIWF_HOME`` 时也不该炸（清一个不存在的键）。"""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setattr(observer.subprocess, "run", fake_run)
    monkeypatch.delenv("AIWF_HOME", raising=False)

    observer._run_aiwf(["workflows", "--json"], cwd=str(project))

    assert "AIWF_HOME" not in seen["kwargs"]["env"]


def test_malformed_rows_do_not_crash(project):
    """外部数据**不校验**（§7-10），但**不崩** —— 形状不符给 ``None``。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): json.dumps(["垃圾", 42, None]),
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"] is None
    assert section["runs_total"] == 3
    assert section["running"] == []
    assert section["last"]["run_id"] is None


def test_view_without_stages_key(project):
    """``--view`` 形状不符（无 ``stages``）⇒ 派生字段为零值，不炸。"""
    runner = make_runner({
        ("workflows", "--json"): "[]",
        ("runs", "list", "--json"): runs_json(RUN_RUNNING),
        VIEW_KEY: json.dumps({"run_id": "x"}, ensure_ascii=False),
    })

    run = observer.observe(str(project), runner=runner)["aiwf"]["running"][0]

    assert run["stages_done"] == 0
    assert run["stages_total"] == 0
    assert run["current"] is None
    assert run["wait"] is None


# ------------------------------------------------------- ⑥ 管道编码（模块头 ⑥）
GBK_SAMPLE = json.dumps(
    [{"id": "std.novel-writing", "name": "AI 长篇小说创作"}], ensure_ascii=False
)


def test_decode_gbk_bytes():
    """★ 模块头 ⑥：``cp936`` 输出（Windows 无 ``PYTHONIOENCODING`` 时）也要能解。"""
    assert observer._decode(GBK_SAMPLE.encode("gbk")) == GBK_SAMPLE


def test_decode_utf8_bytes():
    assert observer._decode(GBK_SAMPLE.encode("utf-8")) == GBK_SAMPLE


def test_decode_str_and_none_passthrough():
    assert observer._decode("[]") == "[]"
    assert observer._decode(None) == ""
    assert observer._decode(b"") == ""


def test_decode_never_raises_on_garbage():
    """两种编码都解不了也不抛 —— 交给 ``json.loads`` 报「无法解析」（L1 G）。"""
    assert isinstance(observer._decode(b"\xff\xfe\x00\x01"), str)


def test_gbk_stdout_from_stub_runner(project):
    """桩喂 **GBK bytes**（不是文本）—— 真机 cp936 场景，须解出正确中文。"""
    runner = make_runner({
        ("workflows", "--json"): GBK_SAMPLE.encode("gbk"),
        ("runs", "list", "--json"): "[]",
    })

    section = observer.observe(str(project), runner=runner)["aiwf"]

    assert section["error"] is None
    assert section["workflows"] == 1


def test_default_runner_handles_gbk_bytes(monkeypatch, project):
    """端到端：**真实 runner**（走 ``_run_aiwf``）收到 GBK bytes 也能解。

    模拟主人终端（``forge`` 的 subprocess 捕获到 aiwf 的 cp936 输出）；
    这正是不用 ``text=True`` 的原因 —— 见模块头 ⑥。
    """
    def fake_run(argv, **kwargs):
        body = GBK_SAMPLE if argv[1] == "workflows" else "[]"
        return subprocess.CompletedProcess(argv, 0,
                                           stdout=body.encode("gbk"),
                                           stderr=b"")

    monkeypatch.setattr(observer.subprocess, "run", fake_run)

    section = observer.observe(str(project), runner=observer._run_aiwf)["aiwf"]

    assert section["error"] is None
    assert section["workflows"] == 1
    assert section["runs_total"] == 0
