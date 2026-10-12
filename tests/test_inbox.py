"""``inbox`` 的行为验证（``07-inbox`` §2 / §3 / §6）。

对应 L1 约定 **G**：引擎的确定性行为（id 分配 / 词表 / 合并的人为约束 / 读视图体量）
必须有自动化验证。
"""
import json
from pathlib import Path

import pytest

from forge import cli, inbox, store
from forge.errors import InboxError, StoreError


def _add(root, text="登录页的报错文案要统一", **kwargs):
    return inbox.add(root, text, **kwargs)


# ------------------------------------------------------------------ id
def test_id_is_sequential_and_formatted(tmp_path):
    """``id`` = ``max(已有) + 1``（§5）· 格式 ``INB-NNN``。"""
    root = str(tmp_path)
    assert _add(root)["id"] == "INB-001"
    assert _add(root)["id"] == "INB-002"
    assert _add(root)["id"] == "INB-003"


def test_id_is_never_reused_after_archiving(tmp_path):
    """递增**不复用**（§3.3）—— 归档不释放号（去向里的旧引用必须一直指得住，L1 D1）。"""
    root = str(tmp_path)
    _add(root)
    _add(root)
    inbox.hand(root, "INB-002", target="纳入 login 计划")
    assert _add(root)["id"] == "INB-003"


@pytest.mark.parametrize("raw", ["INB-003", "inb-3", "3", " 003 ", "inb-003"])
def test_normalize_id_accepts_common_forms(tmp_path, raw):
    """宽容收、只有一个规范形态（H2）。"""
    assert inbox.normalize_id(raw) == "INB-003"


@pytest.mark.parametrize("raw", ["", "   ", "X-1", "INB-", "abc", "INB-0"])
def test_normalize_id_refuses_junk(raw):
    with pytest.raises(InboxError):
        inbox.normalize_id(raw)


# ---------------------------------------------------------------- 捕获
def test_add_defaults(tmp_path):
    """``add`` 只必填内容；``id`` / ``created`` / ``status`` 自动给（§2.3 场景 A）。"""
    root = str(tmp_path)
    item = _add(root)
    assert item["status"] == "open"
    assert item["text"] == "登录页的报错文案要统一"
    assert len(item["created"]) == 10 and item["created"][4] == "-"
    for absent in ("tags", "source", "note", "target"):
        assert absent not in item

    doc = store.read("inbox", project_root=root)
    assert doc["version"] == 1
    assert len(doc["items"]) == 1


def test_add_optional_fields(tmp_path):
    root = str(tmp_path)
    item = _add(root, tags=["体验", "文案"], source="login", note="报错中英混排")
    assert item["tags"] == ["体验", "文案"]
    assert item["source"] == "login"
    assert item["note"] == "报错中英混排"


def test_add_requires_text(tmp_path):
    for bad in (None, "", "   "):
        with pytest.raises(InboxError):
            inbox.add(str(tmp_path), bad)


def test_tags_are_deduped_and_trimmed(tmp_path):
    item = _add(str(tmp_path), tags=[" 体验 ", "体验", "", "文案"])
    assert item["tags"] == ["体验", "文案"]


def test_missing_file_is_empty_data(tmp_path):
    """缺文件 = 空数据（§6.3）—— 首次使用即此状态，``list`` 给空、``add`` 正常创建。"""
    root = str(tmp_path)
    assert inbox.list_items(root) == []
    _add(root)
    assert (tmp_path / ".forge" / "inbox.json").is_file()


# ---------------------------------------------------------------- 合并
def test_merge_creates_product_and_archives_sources(tmp_path):
    """§2.3 场景 C：产物 = 新 ``open``（``max + 1``）；源 → ``handed`` + ``target="并入 <新id>"``。"""
    root = str(tmp_path)
    _add(root)
    _add(root)
    _add(root)  # 不参与合并的第三条
    result = inbox.merge(root, ["INB-001", "INB-002"], text="统一登录页的文案与报错")

    product = result["item"]
    assert product["id"] == "INB-004"  # max(已有) + 1
    assert product["status"] == "open"
    assert product["text"] == "统一登录页的文案与报错"
    assert result["sources"] == ["INB-001", "INB-002"]

    doc = store.read("inbox", project_root=root)
    by_id = {item["id"]: item for item in doc["items"]}
    for key in ("INB-001", "INB-002"):
        assert by_id[key]["status"] == "handed"
        assert by_id[key]["target"] == "并入 INB-004"
    assert by_id["INB-003"]["status"] == "open"  # 旁观者不受影响
    assert by_id["INB-004"]["status"] == "open"


def test_merge_accepts_loose_id_forms(tmp_path):
    root = str(tmp_path)
    _add(root)
    _add(root)
    result = inbox.merge(root, ["1", "inb-2"], text="合成一条")
    assert result["sources"] == ["INB-001", "INB-002"]


def test_merge_needs_at_least_two_sources(tmp_path):
    """§10-28：1 条不需要"合并"。"""
    root = str(tmp_path)
    _add(root)
    for bad in ([], ["INB-001"]):
        with pytest.raises(InboxError):
            inbox.merge(root, bad, text="x")
    assert store.read("inbox", project_root=root)["items"][0]["status"] == "open"


def test_merge_refuses_non_open_sources(tmp_path):
    """§10-29：已 ``handed`` / ``dropped`` 的不能再合。"""
    root = str(tmp_path)
    _add(root)
    _add(root)
    inbox.hand(root, "INB-002", target="纳入 login 计划")
    with pytest.raises(InboxError):
        inbox.merge(root, ["INB-001", "INB-002"], text="x")


def test_merge_refuses_duplicate_sources(tmp_path):
    root = str(tmp_path)
    _add(root)
    _add(root)
    with pytest.raises(InboxError):
        inbox.merge(root, ["INB-001", "INB-001"], text="x")
    with pytest.raises(InboxError):
        inbox.merge(root, ["INB-001", "1"], text="x")  # 归一化后同一条


def test_merge_refuses_unknown_source(tmp_path):
    root = str(tmp_path)
    _add(root)
    with pytest.raises(InboxError):
        inbox.merge(root, ["INB-001", "INB-009"], text="x")


def test_merge_requires_text(tmp_path):
    root = str(tmp_path)
    _add(root)
    _add(root)
    for bad in (None, "", "   "):
        with pytest.raises(InboxError):
            inbox.merge(root, ["INB-001", "INB-002"], text=bad)


def test_merge_does_not_inherit_optional_fields(tmp_path):
    """§10-31：继承任何一个源都是造"出处"的假事实。"""
    root = str(tmp_path)
    inbox.add(root, "a", tags=["体验"], source="login", note="n1")
    inbox.add(root, "b", tags=["性能"], source="pay", note="n2")
    product = inbox.merge(root, ["INB-001", "INB-002"], text="合成")["item"]
    for absent in ("tags", "source", "note"):
        assert absent not in product


def test_merge_is_atomic_one_write(tmp_path):
    """★ 产 + 源在**一次 ``write``** 内完成（§6.2 / §6.3）—— 不存在"改了一半"。

    落盘后：产物与源的状态**同时**是新的（一个字节的中间态都不留）。
    """
    root = str(tmp_path)
    _add(root)
    _add(root)
    path = Path(store.path_for("inbox", project_root=root))
    inbox.merge(root, ["INB-001", "INB-002"], text="合成")
    doc = json.loads(path.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in doc["items"]}
    assert items["INB-001"]["status"] == "handed"
    assert items["INB-002"]["status"] == "handed"
    assert items["INB-003"]["status"] == "open"


def test_merge_product_can_be_merged_again(tmp_path):
    """链式：产物也是 ``open``，与普通条目**同权**（§10-35）。"""
    root = str(tmp_path)
    _add(root)
    _add(root)
    _add(root)
    first = inbox.merge(root, ["INB-001", "INB-002"], text="第一轮")  # → INB-004
    second = inbox.merge(root, [first["item"]["id"], "INB-003"], text="第二轮")  # → INB-005
    assert first["item"]["id"] == "INB-004"
    assert second["item"]["id"] == "INB-005"

    doc = store.read("inbox", project_root=root)
    by_id = {item["id"]: item for item in doc["items"]}
    assert by_id["INB-003"]["target"] == "并入 INB-005"
    assert by_id["INB-003"]["status"] == "handed"
    assert by_id["INB-004"]["target"] == "并入 INB-005"  # 产物自己也能被再合
    assert by_id["INB-005"]["status"] == "open"


# ---------------------------------------------------------------- 交接
def test_hand_and_drop(tmp_path):
    root = str(tmp_path)
    _add(root)
    _add(root)

    hand = inbox.hand(root, "INB-001", target="纳入 login 计划")
    assert (hand["from"], hand["to"], hand["target"]) == ("open", "handed", "纳入 login 计划")

    drop = inbox.drop(root, "2", target="与既有方案重复")
    assert (drop["from"], drop["to"], drop["target"]) == ("open", "dropped", "与既有方案重复")

    doc = store.read("inbox", project_root=root)
    by_id = {item["id"]: item for item in doc["items"]}
    assert by_id["INB-001"]["status"] == "handed"
    assert by_id["INB-002"]["status"] == "dropped"


@pytest.mark.parametrize("verb", ["hand", "drop"])
def test_terminate_requires_target(tmp_path, verb):
    """``--to`` 必填 —— 「这东西最后落哪了」是**已发生的事实**（§2.4）。"""
    root = str(tmp_path)
    _add(root)
    for bad in (None, "", "   "):
        with pytest.raises(InboxError):
            getattr(inbox, verb)(root, "INB-001", target=bad)
    assert store.read("inbox", project_root=root)["items"][0]["status"] == "open"


@pytest.mark.parametrize("verb", ["hand", "drop"])
def test_terminate_is_one_shot(tmp_path, verb):
    """**一次交接即终结**（§3.1）—— 已归档的条目不能再交接。"""
    root = str(tmp_path)
    _add(root)
    inbox.hand(root, "INB-001", target="归某计划")
    with pytest.raises(InboxError):
        getattr(inbox, verb)(root, "INB-001", target="再一次")


def test_terminate_unknown_id(tmp_path):
    with pytest.raises(InboxError):
        inbox.hand(str(tmp_path), "INB-001", target="x")


# ---------------------------------------------------------------- 兜底
def test_set_updates_fields_without_touching_status(tmp_path):
    """``set`` **不动状态**（§2.4 / §9 T7）—— 状态只经 merge / hand / drop。"""
    root = str(tmp_path)
    _add(root)
    result = inbox.set_fields(root, "INB-001", text="改过的表述", tags=["体验"])
    assert result["changed"] == ["text", "tags"]
    assert result["item"]["status"] == "open"
    assert result["item"]["text"] == "改过的表述"


def test_set_replaces_optional_fields_given_once(tmp_path):
    """★ 给出即**替换**（H2：一个语义）—— 不是追加。"""
    root = str(tmp_path)
    inbox.add(root, "a", tags=["体验", "文案"], note="旧备注")
    inbox.set_fields(root, "INB-001", tags=["性能"], note="")
    item = inbox.show_item(root, "INB-001")
    assert item["tags"] == ["性能"]
    assert "note" not in item  # 空串 = 清空


def test_set_requires_something(tmp_path):
    root = str(tmp_path)
    _add(root)
    with pytest.raises(InboxError):
        inbox.set_fields(root, "INB-001")
    assert store.read("inbox", project_root=root)["items"][0]["status"] == "open"


def test_set_cannot_touch_status(tmp_path):
    """``set`` 的表面没有 status —— 想改状态得走语义动作（§2.4）。"""
    root = str(tmp_path)
    _add(root)
    with pytest.raises(TypeError):
        inbox.set_fields(root, "INB-001", status="handed")  # type: ignore[call-arg]


# ---------------------------------------------------------------- 读视图
def test_list_hides_archived_by_default(tmp_path):
    root = str(tmp_path)
    _add(root)
    _add(root)
    inbox.hand(root, "INB-002", target="纳入某计划")
    assert [row["id"] for row in inbox.list_items(root)] == ["INB-001"]
    assert [row["id"] for row in inbox.list_items(root, show_all=True)] == [
        "INB-001",
        "INB-002",
    ]


def test_list_row_carries_no_note_or_source_or_target(tmp_path):
    """一行摘要只给 ``id`` / ``text`` / ``status`` / ``created``（+ ``tags``）—— H4。"""
    root = str(tmp_path)
    inbox.add(root, "a", tags=["体验"], source="login", note="详情")
    row = inbox.list_items(root)[0]
    assert set(row) == {"id", "text", "status", "created", "tags"}


def test_list_full_gives_full_text_but_still_no_note(tmp_path):
    """``--full``：``text`` 全文，仍不给 ``note``（§10-34）。"""
    root = str(tmp_path)
    long_text = "登录页的报错文案要统一" * 12
    inbox.add(root, long_text, note="详情")
    assert inbox.list_items(root)[0]["text"].endswith("…")
    full = inbox.list_items(root, full=True)[0]
    assert full["text"] == long_text
    assert "note" not in full


def test_list_all_never_gives_full_text(tmp_path):
    """★ 归档**永远只给标题行**（H4：归档无界增长）—— 连 ``--full`` 也压不住。"""
    root = str(tmp_path)
    inbox.add(root, "短句")
    long_text = "很长的表述" * 30
    inbox.add(root, long_text)
    inbox.hand(root, "INB-002", target="某计划")

    rows = inbox.list_items(root, full=True, show_all=True)
    archived = next(row for row in rows if row["id"] == "INB-002")
    assert archived["text"].endswith("…")
    assert archived["status"] == "handed"


def test_list_tag_filter_without_grouping(tmp_path):
    """``--tag`` 是**过滤**不是分组（§10-37/38：引擎不做聚类）。"""
    root = str(tmp_path)
    inbox.add(root, "a", tags=["体验"])
    inbox.add(root, "b", tags=["性能"])
    inbox.add(root, "c")
    assert [row["id"] for row in inbox.list_items(root, tag="体验")] == ["INB-001"]
    assert inbox.list_items(root, tag="不存在") == []


def test_list_text_truncates_by_display_width(tmp_path):
    """截断按**显示宽**（中日韩算 2）—— 60 列内该装下的中文要装得下。"""
    root = str(tmp_path)
    inbox.add(root, "登" * 40)
    assert inbox.list_items(root)[0]["text"] == "登" * 30 + "…"  # 30 字 = 60 列


def test_show_item_gives_everything(tmp_path):
    root = str(tmp_path)
    inbox.add(root, "a", tags=["体验"], source="login", note="详情")
    item = inbox.show_item(root, "INB-001")
    assert item == {
        "id": "INB-001",
        "text": "a",
        "status": "open",
        "created": item["created"],
        "tags": ["体验"],
        "source": "login",
        "note": "详情",
    }


def test_show_unknown_id(tmp_path):
    with pytest.raises(InboxError):
        inbox.show_item(str(tmp_path), "INB-042")


# ---------------------------------------------------------------- schema
def test_schema_rejects_bad_documents(tmp_path):
    """结构层校验（§5）—— ``store`` 只执行、不解释（§6.5）。"""
    root = str(tmp_path)
    bad_docs = [
        {"version": 1, "items": [{"id": "INB-1", "text": "x", "status": "open", "created": "2026-10-09"}]},
        {"version": 1, "items": [{"id": "INB-001", "text": "", "status": "open", "created": "2026-10-09"}]},
        {"version": 1, "items": [{"id": "INB-001", "text": "x", "status": "doing", "created": "2026-10-09"}]},
        {"version": 1, "items": [{"id": "INB-001", "text": "x", "status": "open", "created": "2026/10/09"}]},
        # handed 缺 target
        {"version": 1, "items": [{"id": "INB-001", "text": "x", "status": "handed", "created": "2026-10-09"}]},
        # open 不该有 target
        {"version": 1, "items": [{"id": "INB-001", "text": "x", "status": "open", "created": "2026-10-09", "target": "某处"}]},
        # 未知字段
        {"version": 1, "items": [{"id": "INB-001", "text": "x", "status": "open", "created": "2026-10-09", "mergedInto": "INB-002"}]},
    ]
    for doc in bad_docs:
        with pytest.raises(StoreError):
            store.write("inbox", doc, project_root=root)


def test_schema_accepts_valid_documents(tmp_path):
    root = str(tmp_path)
    store.write(
        "inbox",
        {
            "version": 1,
            "items": [
                {"id": "INB-001", "text": "a", "status": "open", "created": "2026-10-09"},
                {
                    "id": "INB-1000",
                    "text": "b",
                    "status": "handed",
                    "created": "2026-10-09",
                    "target": "并入 INB-001",
                    "tags": ["x"],
                },
            ],
        },
        project_root=root,
    )
    assert len(store.read("inbox", project_root=root)["items"]) == 2


def test_module_does_not_depend_on_plan():
    """两个用例**写路径零依赖**（§7.1 / L1 E1）—— ``inbox`` 不 import ``plan``。"""
    source = Path(inbox.__file__).read_text(encoding="utf-8")
    assert "import plan" not in source
    assert "plans.json" not in source


# ---------------------------------------------------------------- CLI 层
def _chdir(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.chdir(root)
    return root


def test_cli_inbox_flow(tmp_path, monkeypatch, capsys):
    root = _chdir(tmp_path, monkeypatch)

    assert cli.main(["inbox", "add", "登录页的报错文案要统一"]) == 0
    assert "INB-001" in capsys.readouterr().out
    assert cli.main(["inbox", "add", "登录失败没有提示", "--tag", "体验", "--source", "login"]) == 0
    assert cli.main(["inbox", "add", "登录按钮在窄屏被裁切"]) == 0
    capsys.readouterr()

    assert (root / ".forge" / "inbox.json").is_file()

    assert cli.main(["inbox", "merge", "INB-001", "INB-002", "--text", "统一登录页的文案与报错"]) == 0
    out = capsys.readouterr().out
    assert "INB-001 + INB-002 → INB-004" in out
    assert "2 条源已归档" in out

    assert cli.main(["inbox", "hand", "INB-004", "--to", "纳入 login 计划"]) == 0
    out = capsys.readouterr().out
    assert "未处理 → 已交接" in out and "纳入 login 计划" in out

    assert cli.main(["inbox", "list"]) == 0
    out = capsys.readouterr().out
    assert "INB-003" in out and "窄屏" in out
    assert "未处理" in out
    assert "INB-004" not in out  # 产物已交接 → 不在活页

    assert cli.main(["inbox", "list", "--all"]) == 0
    out = capsys.readouterr().out
    assert "已交接" in out and "INB-001" in out and "INB-004" in out

    assert cli.main(["inbox", "show", "INB-002"]) == 0
    out = capsys.readouterr().out
    assert "source" in out and "login" in out and "并入 INB-004" in out


def test_cli_inbox_json(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["inbox", "add", "一条念头", "--tag", "体验"])
    capsys.readouterr()

    assert cli.main(["inbox", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == "INB-001" and rows[0]["tags"] == ["体验"]

    assert cli.main(["inbox", "show", "INB-001", "--json"]) == 0
    item = json.loads(capsys.readouterr().out)
    assert item["status"] == "open"


def test_cli_inbox_errors(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    cli.main(["inbox", "add", "一条"])
    capsys.readouterr()

    with pytest.raises(SystemExit):  # 缺 --to → argparse 直接拦下（exit 2）
        cli.main(["inbox", "hand", "INB-001"])
    assert cli.main(["inbox", "merge", "INB-001", "--text", "x"]) == 1  # 源 < 2
    assert "至少要 2 条源" in capsys.readouterr().err
    assert cli.main(["inbox", "set", "INB-001"]) == 1           # 至少给一项
    assert "至少给一项" in capsys.readouterr().err
    assert cli.main(["inbox", "show", "INB-009"]) == 1
    assert "未找到条目" in capsys.readouterr().err


def test_cli_inbox_list_empty(tmp_path, monkeypatch, capsys):
    _chdir(tmp_path, monkeypatch)
    assert cli.main(["inbox", "list"]) == 0
    assert "空" in capsys.readouterr().out
