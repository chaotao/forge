"""``scaffold`` 的行为验证（``05-scaffold`` §2 / §3 / §4）。

锚点：**两类落位**（payload 自己写 · 委托上游）与**判定只作用于 payload 项**。
"""
import json
import os

import pytest

from conftest import read_log
from forge import scaffold, store
from forge.errors import BlueprintError, DelegateError


def _fresh_target(tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    return target


def _write_lock(target, change_set, result):
    store.write(
        "lock", scaffold.report(change_set, result), project_root=str(target)
    )


# ------------------------------------------------------------------ 蓝图库
def test_library_scan_only_accepts_subdirectories(library):
    """库扫描**只认子目录**，同级文件天然被忽略（§2.3）。"""
    (library / "README.md").write_text("x", encoding="utf-8")
    (library / "not-a-blueprint").mkdir()
    assert scaffold.list_blueprints(str(library)) == ["demo"]


def test_missing_blueprint_lists_what_is_available(library):
    with pytest.raises(BlueprintError) as excinfo:
        scaffold.load_blueprint("nope", str(library))
    assert "demo" in str(excinfo.value)


def test_blueprint_id_must_match_directory(library):
    manifest = json.loads((library / "demo" / "blueprint.json").read_text(encoding="utf-8"))
    manifest["id"] = "other"
    (library / "demo" / "blueprint.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(BlueprintError):
        scaffold.load_blueprint("demo", str(library))


def test_payload_must_exist_in_the_blueprint(library):
    manifest = json.loads((library / "demo" / "blueprint.json").read_text(encoding="utf-8"))
    manifest["items"][0]["from"] = "payload:missing.md"
    (library / "demo" / "blueprint.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(BlueprintError):
        scaffold.load_blueprint("demo", str(library))


def test_delegated_item_must_not_declare_to(library):
    """委托项**只关联名字**（§2.2 / §8-★26）。"""
    manifest = json.loads((library / "demo" / "blueprint.json").read_text(encoding="utf-8"))
    manifest["items"].append({"from": "skill:extra", "to": "here"})
    (library / "demo" / "blueprint.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(BlueprintError):
        scaffold.load_blueprint("demo", str(library))


@pytest.mark.parametrize("bad_to", ["/abs/path", "../escape", ".forge/lock.json"])
def test_payload_to_rules(library, bad_to):
    """不逃逸 · 不以 ``.forge/`` 开头（W3）（§2.5）。"""
    manifest = json.loads((library / "demo" / "blueprint.json").read_text(encoding="utf-8"))
    manifest["items"][0]["to"] = bad_to
    (library / "demo" / "blueprint.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(BlueprintError):
        scaffold.load_blueprint("demo", str(library))


def test_duplicate_to_is_refused(library):
    """同一份产物一个写者（W2）（§2.5）。"""
    manifest = json.loads((library / "demo" / "blueprint.json").read_text(encoding="utf-8"))
    manifest["items"][1]["to"] = "AGENTS.md"
    (library / "demo" / "blueprint.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(BlueprintError):
        scaffold.load_blueprint("demo", str(library))


# -------------------------------------------------------------------- 预演
def test_preview_is_ordered_typed_and_pure(library, tmp_path):
    target = tmp_path / "proj"
    change_set = scaffold.preview("demo", str(target), library=str(library))

    assert [action["op"] for action in change_set["actions"]] == [
        scaffold.OP_WRITE,
        scaffold.OP_COPY_TREE,
        scaffold.OP_DELEGATE,
        scaffold.OP_DELEGATE,
    ]
    assert change_set["blueprint"] == "demo"
    assert change_set["actions"][0]["to"] == "AGENTS.md"
    # 委托项只关联名字 —— 落点由上游定，`to` 不存在（§2.2）
    assert "to" not in change_set["actions"][2]
    assert change_set["actions"][2]["from"] == "skill:comfy-run"
    assert change_set["actions"][3]["from"] == "aiwf:std.demo"
    # **模型里没有 `reason`**（§3.2）—— 人类可读的说明由入口层按 `op` 派生
    assert all("reason" not in action for action in change_set["actions"])
    # 纯计算：目标目录一个字节都不该有
    assert not target.exists()


def test_existing_without_lock_is_refused(library, tmp_path):
    target = _fresh_target(tmp_path)
    (target / "AGENTS.md").write_text("mine\n", encoding="utf-8")

    change_set = scaffold.preview("demo", str(target), library=str(library))
    assert change_set["actions"][0]["op"] == scaffold.OP_REFUSE
    assert change_set["actions"][1]["op"] == scaffold.OP_COPY_TREE  # docs 不存在


def test_force_turns_refuse_into_write(library, tmp_path):
    target = _fresh_target(tmp_path)
    (target / "AGENTS.md").write_text("mine\n", encoding="utf-8")

    change_set = scaffold.preview(
        "demo", str(target), force=True, library=str(library)
    )
    assert change_set["actions"][0]["op"] == scaffold.OP_WRITE


def test_locked_path_is_skipped(library, tmp_path):
    target = _fresh_target(tmp_path)
    (target / "AGENTS.md").write_text("hi\n", encoding="utf-8")
    store.write(
        "lock",
        {
            "version": 1,
            "blueprint": "demo",
            "entries": [{"path": "AGENTS.md", "from": "blueprint:demo"}],
        },
        project_root=str(target),
    )

    change_set = scaffold.preview("demo", str(target), library=str(library))
    assert change_set["actions"][0]["op"] == scaffold.OP_SKIP


# -------------------------------------------------------------------- 落位
def test_apply_writes_payload_and_reports_only_payload(library, tmp_path, stub_path):
    install, _log = stub_path
    install("skill")
    install("aiwf")
    target = _fresh_target(tmp_path)

    change_set = scaffold.preview("demo", str(target), library=str(library))
    result = scaffold.apply(change_set, str(target), library=str(library))

    assert result["failed"] is None
    assert (target / "AGENTS.md").read_text(encoding="utf-8") == "hi\n"
    assert (target / "docs" / "a.txt").read_text(encoding="utf-8") == "a\n"
    assert (target / "docs" / "sub" / "b.txt").read_text(encoding="utf-8") == "b\n"
    assert result["delegated"] == ["skill:comfy-run", "aiwf:std.demo"]

    lock = scaffold.report(change_set, result)
    assert [entry["path"] for entry in lock["entries"]] == ["AGENTS.md", "docs"]
    assert {entry["from"] for entry in lock["entries"]} == {"blueprint:demo"}
    assert lock["blueprint"] == "demo"


def test_delegate_gets_cwd_name_force_and_scrubbed_env(
    library, tmp_path, stub_path, monkeypatch
):
    """三条约束（§4.2）：CWD = 目标项目根 · 只传名字 · 清 ``AIWF_HOME``。"""
    install, log = stub_path
    install("skill")
    install("aiwf")
    monkeypatch.setenv("AIWF_HOME", "/somewhere/else")
    target = _fresh_target(tmp_path)

    change_set = scaffold.preview("demo", str(target), library=str(library))
    scaffold.apply(change_set, str(target), force=True, library=str(library))

    lines = read_log(log)
    expected_cwd = f"CWD={os.path.abspath(str(target))}"
    assert lines[0] == expected_cwd
    assert lines[1] == "ARGV=install comfy-run --force"  # 只传名字 + --force 透传
    assert lines[2] == "AIWF_HOME=[/somewhere/else]"  # skill 项**不清**（只影响素材从哪读）
    assert lines[3] == expected_cwd
    assert lines[4] == "ARGV=wf install std.demo --force"
    assert lines[5] == "AIWF_HOME=[]"  # aiwf 项**清掉**（它会整体搬走 .aiwf/）


def test_delegate_failure_is_fail_fast(library, tmp_path, stub_path):
    """失败即停（§3.5）：已成功的项照常报账，后面的项不再执行。"""
    install, log = stub_path
    install("skill", exit_code=1)
    install("aiwf")
    target = _fresh_target(tmp_path)

    change_set = scaffold.preview("demo", str(target), library=str(library))
    result = scaffold.apply(change_set, str(target), library=str(library))

    assert result["failed"]["from"] == "skill:comfy-run"
    assert result["applied"] != []  # payload 已落，照常报账
    assert result["delegated"] == []
    assert not any("wf install" in line for line in read_log(log))


def test_missing_upstream_cli_reports_clearly(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(DelegateError) as excinfo:
        scaffold.delegate("skill", "anything", str(tmp_path))
    assert "skill" in str(excinfo.value)


def test_unknown_delegate_source_is_refused(tmp_path):
    with pytest.raises(DelegateError):
        scaffold.delegate("maven", "anything", str(tmp_path))


def test_apply_is_idempotent(library, tmp_path, stub_path):
    install, log = stub_path
    install("skill")
    install("aiwf")
    target = _fresh_target(tmp_path)

    first = scaffold.preview("demo", str(target), library=str(library))
    result = scaffold.apply(first, str(target), library=str(library))
    _write_lock(target, first, result)

    second = scaffold.preview("demo", str(target), library=str(library))
    assert [action["op"] for action in second["actions"]] == [
        scaffold.OP_SKIP,
        scaffold.OP_SKIP,
        scaffold.OP_DELEGATE,
        scaffold.OP_DELEGATE,
    ]
    result2 = scaffold.apply(second, str(target), library=str(library))
    assert result2["applied"] == []
    assert result2["skipped"] == ["AGENTS.md", "docs"]


def test_report_merges_with_existing_lock(library, tmp_path):
    """中途失败后重跑要"接着往下落" —— 账按 ``path`` 归并（§3.5 / §3.6）。"""
    change_set = {"version": 1, "blueprint": "demo", "actions": []}
    existing = {
        "version": 1,
        "blueprint": "demo",
        "entries": [{"path": "AGENTS.md", "from": "blueprint:demo"}],
    }
    result = {"applied": [{"path": "docs", "from": "blueprint:demo"}]}

    merged = scaffold.report(change_set, result, existing)
    assert [entry["path"] for entry in merged["entries"]] == ["AGENTS.md", "docs"]
