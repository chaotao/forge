"""``store`` 的行为验证（``03-store`` §3 / §5 / §6）。"""
import json
import os
from pathlib import Path

import pytest

from forge import store
from forge.errors import StoreError

PROJECTS = {
    "version": 1,
    "projects": [
        {
            "path": "D:/proj/alpha",
            "alias": "alpha",
            "blueprint": "minimal",
            "stage": "scaffolded",
            "registered_at": "2026-10-08T12:00:00+08:00",
        }
    ],
}
LOCK = {
    "version": 1,
    "blueprint": "minimal",
    "entries": [{"path": "AGENTS.md", "from": "blueprint:minimal"}],
}


def test_kind_table_is_derived_from_schemas():
    """``kind`` 表由 ``schemas/`` 目录派生，``scope`` / ``形态`` 由 schema 声明（§6.2）。"""
    assert store.kind_names() == ["inbox", "lock", "plan_log", "plans", "projects"]

    projects = store.kind_info("projects")
    assert projects["scope"] == store.SCOPE_GLOBAL
    assert projects["form"] == store.FORM_SNAPSHOT

    lock = store.kind_info("lock")
    assert lock["scope"] == store.SCOPE_PROJECT
    assert lock["form"] == store.FORM_SNAPSHOT

    # 06-plan 的两个 kind：计划集合（snapshot）+ 过程事件（log）
    assert store.kind_info("plans")["form"] == store.FORM_SNAPSHOT
    assert store.kind_info("plan_log")["form"] == store.FORM_LOG

    # 07-inbox：项目域的念头账（snapshot）—— 新模块接入 = 加一份 schema，store 零改动（§6.4）
    inbox_kind = store.kind_info("inbox")
    assert inbox_kind["scope"] == store.SCOPE_PROJECT
    assert inbox_kind["form"] == store.FORM_SNAPSHOT


def test_unknown_kind_is_refused():
    with pytest.raises(StoreError):
        store.kind_info("nope")


def test_paths_follow_the_two_domains(tmp_path):
    """global 集中一处 · project 落 ``<目标项目>/.forge/``（§2）。"""
    assert store.path_for("projects") == os.path.join(
        os.environ["FORGE_HOME"], "projects.json"
    )
    assert store.path_for("lock", project_root=tmp_path) == os.path.join(
        str(tmp_path), ".forge", "lock.json"
    )


def test_project_kind_requires_project_root():
    with pytest.raises(StoreError):
        store.path_for("lock")


def test_missing_file_reads_as_empty(tmp_path):
    """缺文件 = 空数据，不报错（§5.3）。"""
    assert store.read("projects") == {}
    assert store.read("lock", project_root=tmp_path) == {}


def test_write_read_roundtrip(tmp_path):
    path = store.write("lock", LOCK, project_root=tmp_path)
    assert os.path.isfile(path)
    assert store.read("lock", project_root=tmp_path) == LOCK


def test_write_is_validated_and_refuses_bad_data(tmp_path):
    bad = {"version": 1, "entries": []}  # 缺 blueprint
    with pytest.raises(StoreError):
        store.write("lock", bad, project_root=tmp_path)
    assert not os.path.isfile(os.path.join(str(tmp_path), ".forge", "lock.json"))


def test_write_leaves_no_temp_file(tmp_path):
    """原子替换的写法不留残渣（§5.3）。"""
    directory = tmp_path / ".forge"
    store.write("lock", LOCK, project_root=tmp_path)
    assert os.listdir(directory) == ["lock.json"]


def test_query_finds_by_field(tmp_path):
    store.write("projects", PROJECTS)
    assert store.query("projects", "projects.path", "D:/proj/alpha")[0]["alias"] == "alpha"
    assert store.query("projects", "projects.alias", "nope") == []


def test_query_without_container_is_refused():
    with pytest.raises(StoreError):
        store.query("projects", "alias", "alpha")


def test_read_rejects_malformed_file(tmp_path):
    path = tmp_path / ".forge" / "lock.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError):
        store.read("lock", project_root=tmp_path)


# ------------------------------------------------------------- log 形态
def _install_journal(tmp_path, monkeypatch):
    """造一个 ``log`` 形态的 kind（一条记录 = ``{"n": int}``），验证形态机制本身。"""
    schema_dir = tmp_path / "schemas"
    schema_dir.mkdir()
    (schema_dir / "journal.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "x-forge-scope": "project",
                "x-forge-form": "log",
                "type": "object",
                "required": ["n"],
                "additionalProperties": False,
                "properties": {"n": {"type": "integer"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(store, "schemas_dir", lambda: str(schema_dir))
    store.reset_cache()


def test_log_kind_derives_form_and_jsonl_path(tmp_path, monkeypatch):
    _install_journal(tmp_path, monkeypatch)
    assert store.kind_info("journal")["form"] == store.FORM_LOG
    assert store.path_for("journal", project_root=tmp_path).endswith("journal.jsonl")


def test_log_missing_file_reads_as_empty(tmp_path, monkeypatch):
    _install_journal(tmp_path, monkeypatch)
    assert store.read("journal", project_root=tmp_path) == []


def test_log_appends_one_line_per_write(tmp_path, monkeypatch):
    _install_journal(tmp_path, monkeypatch)
    store.write("journal", {"n": 1}, project_root=tmp_path)
    store.write("journal", {"n": 2}, project_root=tmp_path)
    assert store.read("journal", project_root=tmp_path) == [{"n": 1}, {"n": 2}]
    path = Path(store.path_for("journal", project_root=tmp_path))
    assert [line for line in path.read_text(encoding="utf-8").splitlines() if line] == [
        '{"n": 1}',
        '{"n": 2}',
    ]


def test_log_write_is_validated(tmp_path, monkeypatch):
    _install_journal(tmp_path, monkeypatch)
    with pytest.raises(StoreError):
        store.write("journal", {"n": "x"}, project_root=tmp_path)
    assert store.read("journal", project_root=tmp_path) == []


def test_log_skips_incomplete_last_line(tmp_path, monkeypatch):
    """追加写入的唯一坏情况：末行写了一半 → 跳过（§5.3）。"""
    _install_journal(tmp_path, monkeypatch)
    store.write("journal", {"n": 1}, project_root=tmp_path)
    path = Path(store.path_for("journal", project_root=tmp_path))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 2')
    assert store.read("journal", project_root=tmp_path) == [{"n": 1}]


def test_log_middle_bad_line_is_refused(tmp_path, monkeypatch):
    """中间行损坏**不静默**（只有末行可跳过）。"""
    _install_journal(tmp_path, monkeypatch)
    store.write("journal", {"n": 1}, project_root=tmp_path)
    store.write("journal", {"n": 2}, project_root=tmp_path)
    path = Path(store.path_for("journal", project_root=tmp_path))
    path.write_text('{"n": 1}\nnot json\n{"n": 2}\n', encoding="utf-8")
    with pytest.raises(StoreError):
        store.read("journal", project_root=tmp_path)


def test_log_query_without_container(tmp_path, monkeypatch):
    """``log`` 的数据本身就是记录数组 → ``query`` 允许省略容器（§6.1）。"""
    _install_journal(tmp_path, monkeypatch)
    store.write("journal", {"n": 1}, project_root=tmp_path)
    store.write("journal", {"n": 2}, project_root=tmp_path)
    assert store.query("journal", "n", 2, project_root=tmp_path) == [{"n": 2}]


# --------------------------------------------------------- validator 缓存
def test_validator_is_cached_per_schema(tmp_path, monkeypatch):
    """★ validator 只构建一次（性能护栏）—— 见 ``forge/store.py::_validator``。

    构建含 ``check_schema``，实测占单次校验耗时的 **99%**；``log`` 形态逐行校验，
    不缓存则成本 = ``O(历史记录总量)``。
    """
    _install_journal(tmp_path, monkeypatch)
    first = store._validator("journal")
    assert store._validator("journal") is first
    store.reset_cache()  # 换 ``schemas/`` 目录必须能让缓存失效
    assert store._validator("journal") is not first


def test_validate_error_matches_jsonschema(tmp_path, monkeypatch):
    """★ 语义等价：报错**一字不变** —— 错误挑选仍走 ``best_match``（§6.5）。"""
    import jsonschema

    _install_journal(tmp_path, monkeypatch)
    schema = store.kind_info("journal")["schema"]
    bad = {"n": "x", "extra": 1}  # 同时触发 type + additionalProperties 两个错

    with pytest.raises(jsonschema.ValidationError) as direct:
        jsonschema.validate(bad, schema)
    with pytest.raises(StoreError) as wrapped:
        store.validate("journal", bad)

    assert str(wrapped.value).endswith(str(direct.value.message))
