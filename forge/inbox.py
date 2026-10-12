"""``inbox`` —— 项目里的念头：**捕获 · 整理 · 交接**。设计见 ``docs/design/07-inbox.md``。

它是**用例层**模块：不碰文件，**只经 ``store``** 读写（L2 §4.2 边 6 / §2.2 W3）。

三件事（§1）：

1. **捕获**（``add``）—— 记一条念头；**只有 ``text`` 必填**（「为何停放」那套已废）。
2. **整理 · 合并**（``merge``）—— N 条 ``open`` 攒成 1 条主题。
3. **交接**（``hand`` / ``drop``）—— ``open → handed / dropped``，**一步完成**，留一条去向。

★ **只有一份数据、只有一次 ``write``** —— 故天然**原子**（§6.3）：``plan`` 之所以有
"两次写一半成功"的问题，是因为它拆了 ``plans`` + ``plan_log`` 两个 ``kind``；``inbox``
只有一份，``merge`` 的"产 + 源"也同属这一份 → 仍是一次原子替换。

**判定归 agent**（L1 约定 B）：本模块**不判**"这条重不重要""该交给谁""**哪几条该合**" ——
它只把 agent 的判定落盘。相似度是语义判断，写不成确定性函数（§8）。
"""
import os
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import store
from .errors import InboxError

# ------------------------------------------------------------------ 常量
KIND = "inbox"

INBOX_VERSION = 1

#: ``Item.status`` 词表（§4）—— 3 态。★ **没有"进行中 / 已完成"**（它不是任务，§8）。
STATUS_OPEN = "open"
STATUS_HANDED = "handed"
STATUS_DROPPED = "dropped"
STATUSES = (STATUS_OPEN, STATUS_HANDED, STATUS_DROPPED)

DEFAULT_STATUS = STATUS_OPEN

#: 条目标识：``INB-NNN`` · 项目内唯一 · **递增不复用**（§3.3）。
ID_PREFIX = "INB"
ID_DIGITS = 3
_ID_RE = re.compile(r"^INB-(\d+)$", re.IGNORECASE)

#: 列表视图里 ``text`` 的**显示宽**上限 —— 超出一行即截断（§2.4）；
#: ``--full`` 才给全文（H4：够用即止）。
TEXT_WIDTH = 60

#: 合并至少几条源（§10-28）。
MERGE_MIN_SOURCES = 2


# ------------------------------------------------------------------ id
def _format_id(number: int) -> str:
    if number < 1:
        raise InboxError(f"id 序号必须 ≥ 1，收到 {number}")
    return f"{ID_PREFIX}-{number:0{ID_DIGITS}d}"


def normalize_id(raw: Any) -> str:
    """把用户给的 id 归一化为**规范形态** ``INB-NNN``（H2：宽容收、只有一个规范形态）。

    收 ``INB-003`` / ``inb-3`` / 裸序号 ``3`` —— 都归一为 ``INB-003``。
    """
    text = str(raw or "").strip()
    if not text:
        raise InboxError("id 不能为空")
    if text.isdigit():
        return _format_id(int(text))
    match = _ID_RE.match(text)
    if match:
        return _format_id(int(match.group(1)))
    raise InboxError(f"id {raw!r} 不成立 —— 形如 {ID_PREFIX}-001（或直接给序号 1）")


def _next_id(doc: Dict[str, Any]) -> str:
    """``max(已有) + 1``（§5）—— **唯一性与递增由用例分配**，schema 只约束格式。

    这也是 ``id`` **不得复用**的落地点（§3.3）：去向里写的旧 id 必须一直指得住（L1 D1）。
    """
    top = 0
    for item in doc.get("items", []):
        match = _ID_RE.match(str(item.get("id", "")))
        if match:
            top = max(top, int(match.group(1)))
    return _format_id(top + 1)


# ------------------------------------------------------------------ 载入
def _inbox_doc(project_root: str) -> Dict[str, Any]:
    """读 ``inbox``；**缺文件 = 空数据**（§6.3）→ 给一份空集合。"""
    doc = store.read(KIND, project_root=project_root)
    if not doc:
        return {"version": INBOX_VERSION, "items": []}
    return doc


def _save(project_root: str, doc: Dict[str, Any]) -> None:
    store.write(KIND, doc, project_root=project_root)


def _find(doc: Dict[str, Any], item_id: Any) -> Dict[str, Any]:
    key = normalize_id(item_id)
    for item in doc.get("items", []):
        if item.get("id") == key:
            return item
    raise InboxError(f"未找到条目 {key!r}")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# -------------------------------------------------------------- 字段清洗
def _require_text(text: Any, verb: str = "add") -> str:
    value = str(text or "").strip()
    if not value:
        raise InboxError(f"{verb} 必须给内容（一句话：是什么念头，§3.1）")
    return value


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_tags(tags: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    for tag in tags or []:
        text = str(tag).strip()
        if text and text not in out:
            out.append(text)
    return out


def _assign_optional(
    item: Dict[str, Any],
    *,
    tags: Optional[Sequence[Any]] = None,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> List[str]:
    """把可选字段写进条目 —— ★ **给出即替换**（H2：一个语义，不留歧义）。

    ``tags=[]`` / ``source=""`` 表示**清空**该字段（重新落盘时字段直接不写）。
    """
    touched: List[str] = []
    if tags is not None:
        cleaned = _clean_tags(tags)
        if cleaned:
            item["tags"] = cleaned
        else:
            item.pop("tags", None)
        touched.append("tags")
    if source is not None:
        value = _clean(source)
        if value:
            item["source"] = value
        else:
            item.pop("source", None)
        touched.append("source")
    if note is not None:
        value = _clean(note)
        if value:
            item["note"] = value
        else:
            item.pop("note", None)
        touched.append("note")
    return touched


# ------------------------------------------------------------ 写：捕获
def add(
    project_root: str,
    text: str,
    *,
    tags: Optional[Sequence[Any]] = None,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """**捕获一条念头**（§2.3 场景 A）—— ``id`` / ``created`` 自动给，其余可选。"""
    doc = _inbox_doc(project_root)
    item: Dict[str, Any] = {
        "id": _next_id(doc),
        "text": _require_text(text),
        "status": DEFAULT_STATUS,
        "created": _now(),
    }
    _assign_optional(item, tags=tags, source=source, note=note)
    doc.setdefault("items", []).append(item)
    _save(project_root, doc)
    return item


# ------------------------------------------------------------ 写：整理
def merge(
    project_root: str,
    item_ids: Sequence[Any],
    *,
    text: str,
    tags: Optional[Sequence[Any]] = None,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """**整理 · 合并**（§2.3 场景 C）—— N 条 ``open`` 攒成 1 条主题。

    - 源 **≥ 2 条且必须全 ``open``**（§10-28 / §10-29）；
    - 产物 = 一条**新 ``open`` 条目**，``text`` = agent 给的整合表述（§10-27）；
    - 源 → ``handed`` + ``target="并入 <新 id>"``（§10-32：复用既有状态，**零模型改动**）；
    - ``tags`` / ``source`` / ``note`` **不自动继承**（§10-31）——
      继承任何一个源都是造"出处"的假事实；
    - ★ **产 + 源在同一次 ``write`` 内完成 → 原子**（§6.2 / §6.3）。
    """
    value = _require_text(text, "merge")

    raw_ids = [str(item).strip() for item in item_ids or []]
    if len(raw_ids) < MERGE_MIN_SOURCES:
        raise InboxError(
            f"merge 至少要 {MERGE_MIN_SOURCES} 条源（1 条不需要合并，§10-28）"
        )

    doc = _inbox_doc(project_root)
    sources: List[Dict[str, Any]] = []
    keys: List[str] = []
    for raw in raw_ids:
        key = normalize_id(raw)
        if key in keys:
            raise InboxError(f"源条目 {key} 重复")
        item = _find(doc, key)
        if item.get("status") != STATUS_OPEN:
            raise InboxError(
                f"{key} 已经是 {item.get('status')} —— 只有 open 的条目能被合并（§10-29）"
            )
        keys.append(key)
        sources.append(item)

    product: Dict[str, Any] = {
        "id": _next_id(doc),
        "text": value,
        "status": DEFAULT_STATUS,
        "created": _now(),
    }
    _assign_optional(product, tags=tags, source=source, note=note)

    for item in sources:
        item["status"] = STATUS_HANDED
        item["target"] = f"并入 {product['id']}"

    doc.setdefault("items", []).append(product)
    _save(project_root, doc)  # ★ 一次 write：产物 + 源一起落，不存在"改了一半"
    return {"item": product, "sources": keys}


# ------------------------------------------------------------ 写：兜底
def set_fields(
    project_root: str,
    item_id: Any,
    *,
    text: Optional[str] = None,
    tags: Optional[Sequence[Any]] = None,
    source: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """**兜底写动词**（§2.4 ``set``）—— 改笔误 / 补标签 / 改出处；★ **不动状态**。

    ``status`` 只经 ``merge`` / ``hand`` / ``drop`` 变（§9 **T7**）—— 这是本模块与
    ``plan`` 的一处刻意不同：念头的"去向"是**语义动作**，不是随手改的字段。
    """
    if all(value is None for value in (text, tags, source, note)):
        raise InboxError("set 至少给一项：--text / --tag / --source / --note")

    doc = _inbox_doc(project_root)
    item = _find(doc, item_id)
    touched: List[str] = []

    if text is not None:
        value = _require_text(text, "set --text")
        if item.get("text") != value:
            item["text"] = value
        touched.append("text")
    touched += _assign_optional(item, tags=tags, source=source, note=note)

    _save(project_root, doc)
    return {"id": item["id"], "changed": touched, "item": dict(item)}


# ------------------------------------------------------------ 写：交接
def _terminate(
    project_root: str, item_id: Any, status: str, target: Optional[str], verb: str
) -> Dict[str, Any]:
    """交接与丢弃走**同一条路**（§2.4）—— 只差终态；**一次交接即终结**（§3.1）。"""
    where = _clean(target)
    if not where:
        raise InboxError(f"{verb} 必须给 --to（这东西最后落哪了 —— 是事实，§2.4）")

    doc = _inbox_doc(project_root)
    item = _find(doc, item_id)
    if item.get("status") != STATUS_OPEN:
        raise InboxError(
            f"{item['id']} 已经是 {item.get('status')} —— 交接是一次性的（§3.1）"
        )

    before = item["status"]
    item["status"] = status
    item["target"] = where
    _save(project_root, doc)
    return {
        "id": item["id"],
        "op": verb,
        "from": before,
        "to": status,
        "target": where,
        "item": dict(item),
    }


def hand(project_root: str, item_id: Any, *, target: str) -> Dict[str, Any]:
    """**交接出去** → ``handed``（§2.4）—— ``--to`` 必填。"""
    return _terminate(project_root, item_id, STATUS_HANDED, target, "hand")


def drop(project_root: str, item_id: Any, *, target: str) -> Dict[str, Any]:
    """**丢弃** → ``dropped``（§2.4）—— ``--to`` 必填。"""
    return _terminate(project_root, item_id, STATUS_DROPPED, target, "drop")


# ---------------------------------------------------------------- 读视图
def _shorten(text: str, width: int = TEXT_WIDTH) -> str:
    """按**显示宽**截断 —— 中日韩字符算 2 列，按字符数截会"一行装不下"。"""
    total = 0
    for index, char in enumerate(text):
        size = 2 if unicodedata.east_asian_width(char) in "WF" else 1
        if total + size > width:
            return text[:index] + "…"
        total += size
    return text


def _row(item: Dict[str, Any], *, full: bool) -> Dict[str, Any]:
    """一行 —— ``id`` / ``text`` / ``status`` / ``created``（有 ``tags`` 才给 ``tags``）。

    ★ ``source`` / ``note`` / ``target`` **不给** —— 那三样归 ``show``（H4：够用即止）。
    """
    row: Dict[str, Any] = {
        "id": item["id"],
        "text": item["text"] if full else _shorten(item["text"]),
        "status": item.get("status", DEFAULT_STATUS),
        "created": item.get("created", ""),
    }
    if item.get("tags"):
        row["tags"] = list(item["tags"])
    return row


def list_items(
    project_root: str,
    *,
    full: bool = False,
    show_all: bool = False,
    tag: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """**列表视图**（§2.4 / §2.5）—— 默认只给**未处理**条目。

    - ``full``：``text`` 给**全文**（**整理专用**，§10-34）—— 仍**不给 ``note``**；
    - ``show_all``：连**已归档**一起给（★ 归档**永远只给标题行**，即使加了 ``--full``，H4）；
    - ``tag``：按标签过滤（**不是分组** —— 引擎不做聚类，§10-37/38）。
    """
    doc = _inbox_doc(project_root)
    rows: List[Dict[str, Any]] = []
    for item in doc.get("items", []):
        status = item.get("status", DEFAULT_STATUS)
        if status != STATUS_OPEN and not show_all:
            continue
        if tag is not None and tag not in (item.get("tags") or []):
            continue
        # 归档无界增长 → 不给正文（§2.5）
        rows.append(_row(item, full=full and status == STATUS_OPEN))
    return rows


def show_item(project_root: str, item_id: Any) -> Dict[str, Any]:
    """**单条全文**（§2.3 场景 B 步 1）—— 内容 + 标签 / 出处 / 备注 + 状态 + 去向。"""
    doc = _inbox_doc(project_root)
    return dict(_find(doc, item_id))


def project_inbox_path(project_root: str) -> str:
    """``inbox.json`` 的落点 —— 仅供**报错 / 提示**用（不暴露形态，§6.1 边界）。"""
    return store.path_for(KIND, project_root=os.path.abspath(project_root))
