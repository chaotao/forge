"""``forge new`` / ``forge sync`` 的端到端验证（``05-scaffold`` §6 / §7 T5）。"""
import json
import os

from conftest import read_log
from forge import cli, store


def _argv_lines(log):
    return [line for line in read_log(log) if line.startswith("ARGV=")]


def test_new_scaffolds_locks_and_registers(tmp_path, capsys):
    target = tmp_path / "myproj"
    assert cli.main(["new", str(target)]) == 0

    # 骨架落到项目里
    assert (target / "AGENTS.md").is_file()
    assert (target / "README.md").is_file()
    assert (target / ".gitignore").is_file()
    assert not (target / ".forge" / "projects.json").exists()

    # 落位记录只含 payload 项
    lock = store.read("lock", project_root=str(target))
    assert [entry["path"] for entry in lock["entries"]] == [
        ".gitignore",
        "AGENTS.md",
        "README.md",
    ]
    assert {entry["from"] for entry in lock["entries"]} == {"blueprint:minimal"}

    # 登记受管（global 域）
    projects = store.read("projects")
    assert len(projects["projects"]) == 1
    registered = projects["projects"][0]
    assert registered["path"] == os.path.abspath(str(target))
    assert registered["alias"] == "myproj"
    assert registered["blueprint"] == "minimal"
    assert registered["stage"] == cli.DEFAULT_STAGE

    assert "已登记受管：myproj" in capsys.readouterr().out


def test_rerun_on_managed_project_is_idempotent(tmp_path, capsys):
    """已受管 + 蓝图一致（`--blueprint` 未指定时**取登记值**）→ 幂等 no-op（§6 改判）。"""
    target = tmp_path / "myproj"
    assert cli.main(["new", str(target)]) == 0
    before = (target / "AGENTS.md").read_text(encoding="utf-8")
    capsys.readouterr()

    assert cli.main(["new", str(target)]) == 0
    assert "跳过" in capsys.readouterr().out
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == before
    assert len(store.read("projects")["projects"]) == 1


def test_new_on_managed_with_other_blueprint_points_to_sync(
    tmp_path, capsys, library
):
    """已受管 + 蓝图**不一致** → 拒绝，并指向 `sync`（换蓝图不是 `new` 的职责）。"""
    target = tmp_path / "myproj"
    assert cli.main(["new", str(target)]) == 0
    capsys.readouterr()

    assert (
        cli.main(["new", str(target), "--blueprint", "demo", "--blueprints", str(library)])
        == 1
    )
    err = capsys.readouterr().err
    assert "换蓝图用" in err and "forge sync" in err


def test_sync_requires_a_managed_project(tmp_path, capsys):
    assert cli.main(["sync", str(tmp_path / "nope")]) == 1
    assert "不在受管清单里" in capsys.readouterr().err


def test_sync_lands_new_items_and_updates_registered_blueprint(
    tmp_path, capsys, stub_path, library
):
    """`sync` 补落蓝图新增的项；受管清单的 `blueprint` 跟着更新（§7 T5）。"""
    install, log = stub_path
    install("skill")
    install("aiwf")
    target = tmp_path / "p"
    assert cli.main(["new", str(target)]) == 0  # minimal
    capsys.readouterr()

    assert (
        cli.main(
            ["sync", str(target), "--blueprint", "demo", "--blueprints", str(library)]
        )
        == 0
    )
    # 委托项**无条件**重跑上游命令（上游自己幂等，§3.4 / §3.5）
    assert _argv_lines(log) == ["ARGV=install comfy-run", "ARGV=wf install std.demo"]

    # 已落过的不覆盖（在 lock 里 → 跳过）；只有新项落下来
    lock = store.read("lock", project_root=str(target))
    assert [entry["path"] for entry in lock["entries"]] == [
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "docs",
    ]
    assert {entry["from"] for entry in lock["entries"]} == {"blueprint:minimal", "blueprint:demo"}
    assert store.read("projects")["projects"][0]["blueprint"] == "demo"


def test_sync_is_idempotent_for_payloads(tmp_path, capsys, stub_path, library):
    install, _log = stub_path
    install("skill")
    install("aiwf")
    target = tmp_path / "p"
    assert cli.main(["new", str(target)]) == 0
    assert (
        cli.main(["sync", str(target), "--blueprint", "demo", "--blueprints", str(library)])
        == 0
    )
    capsys.readouterr()

    assert cli.main(["sync", str(target), "--blueprints", str(library)]) == 0
    assert "跳过" in capsys.readouterr().out
    assert store.read("projects")["projects"][0]["blueprint"] == "demo"


def test_sync_dry_run_keeps_everything(tmp_path, capsys, library):
    target = tmp_path / "p"
    assert cli.main(["new", str(target)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            ["sync", str(target), "--blueprint", "demo", "--blueprints", str(library), "--dry-run"]
        )
        == 0
    )
    assert "预演（未落盘）" in capsys.readouterr().out
    assert store.read("projects")["projects"][0]["blueprint"] == "minimal"
    paths = [entry["path"] for entry in store.read("lock", project_root=str(target))["entries"]]
    assert "docs" not in paths


def test_dry_run_renders_delegate_command(tmp_path, capsys, library):
    """委托项的人类可读说明**由入口层按 `op` 派生**（§3.2 —— 模型里没有 `reason`）。"""
    target = tmp_path / "p"
    assert (
        cli.main(
            ["new", str(target), "--blueprint", "demo", "--blueprints", str(library), "--dry-run"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "skill install comfy-run" in out
    assert "aiwf wf install std.demo" in out


def test_dry_run_touches_nothing(tmp_path, capsys):
    target = tmp_path / "myproj"
    assert cli.main(["new", str(target), "--dry-run"]) == 0

    assert not target.exists()
    assert not (tmp_path / "forge-home" / "projects.json").exists()
    out = capsys.readouterr().out
    assert "预演（未落盘）" in out
    assert "AGENTS.md" in out


def test_delegated_sources_are_handed_to_upstream(tmp_path, stub_path, library):
    install, log = stub_path
    install("skill")
    install("aiwf")
    target = tmp_path / "p"

    assert (
        cli.main(
            ["new", str(target), "--blueprint", "demo", "--blueprints", str(library)]
        )
        == 0
    )
    assert _argv_lines(log) == ["ARGV=install comfy-run", "ARGV=wf install std.demo"]

    lock = store.read("lock", project_root=str(target))
    assert [entry["path"] for entry in lock["entries"]] == ["AGENTS.md", "docs"]
    assert {entry["from"] for entry in lock["entries"]} == {"blueprint:demo"}


def test_partial_failure_keeps_lock_but_does_not_register(
    tmp_path, capsys, stub_path, library
):
    """失败即停 + 未建成不登记（§6 第 5 步）。"""
    install, _log = stub_path
    install("skill", exit_code=1)
    install("aiwf")
    target = tmp_path / "p"

    assert (
        cli.main(
            ["new", str(target), "--blueprint", "demo", "--blueprints", str(library)]
        )
        == 1
    )
    assert "落位停在 skill:comfy-run" in capsys.readouterr().err

    lock = store.read("lock", project_root=str(target))
    assert [entry["path"] for entry in lock["entries"]] == ["AGENTS.md", "docs"]
    assert store.read("projects") == {}


def test_json_output(tmp_path, capsys):
    target = tmp_path / "p"
    assert cli.main(["new", str(target), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["blueprint"] == "minimal"
    assert payload["alias"] == "p"
    assert payload["failed"] is None
    assert payload["delegated"] == []


def test_alias_is_deduplicated(tmp_path):
    first = tmp_path / "a" / "proj"
    second = tmp_path / "b" / "proj"
    assert cli.main(["new", str(first)]) == 0
    assert cli.main(["new", str(second)]) == 0
    projects = store.read("projects")
    assert [item["alias"] for item in projects["projects"]] == ["proj", "proj-2"]


def test_explicit_alias(tmp_path):
    target = tmp_path / "p"
    assert cli.main(["new", str(target), "--alias", "我的项目"]) == 0
    assert store.read("projects")["projects"][0]["alias"] == "我的项目"


def test_target_that_is_a_file_is_refused(tmp_path, capsys):
    target = tmp_path / "afile"
    target.write_text("x", encoding="utf-8")
    assert cli.main(["new", str(target)]) == 1
    assert "不是目录" in capsys.readouterr().err
