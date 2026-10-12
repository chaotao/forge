#!/usr/bin/env python3
"""inbox 端到端仿真 —— 走一遍**真实项目里的念头流程**：捕获 → 整理 · 合并 → 交接。

跑法（任意目录均可，建议在仓库根）：

    python sim/inbox-demo/drive.py

全程落在 ``sim/inbox-demo/_run/`` 内，**绝不碰真的 ``~/.forge``**：

    0. 探测 ``forge``
    1. 清空 ``_run/``、建隔离 ``FORGE_HOME``；``forge new`` 起一个真项目
       （``minimal`` 蓝图 —— 纯 payload、无委托，所以这一步不依赖 skill / aiwf）
    2. 捕获念头                            —— ``inbox add``（含 tag / source / note）
    3. 读视图                              —— ``list`` / ``list --full`` / ``show``
    4. 整理 · 合并                         —— ``merge``（源归档 + 产物 open）· 含**链式**
    5. 交接                                —— ``hand`` / ``drop``
    6. 再看视图                            —— 活页只剩「够格的主题」· ``--all`` 含归档
    7. 错路                                —— 源不够 / 合已归档 / 缺 ``--to`` / 不存在
    8. ``--json`` 一致性 · 归档只给标题行
    9. 产物核验                            —— ``inbox.json``：id 不复用 · 源 target 指向产物 · **open 无 target**
   10. 产物一览 · 结论

退出码 ``0`` = 全部通过；``1`` = 有断言失败。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ★ 输出编码：Windows 控制台默认 cp936，本脚本含非 GBK 字形（`▸` / `⏳` …）
#   ⇒ 不钉 UTF-8 会 UnicodeEncodeError 中断（与 `forge` 入口 `cli._configure_stdio` 同理）。
sys.path.insert(0, str(HERE.parent))
from _stdio import configure_stdout  # noqa: E402

configure_stdout()
RUN = HERE / "_run"
FORGE_HOME = RUN / "home"
DEMO = RUN / "demo"

#: ``minimal`` 的 items 全是 ``payload:``（纯自落、零委托）—— 仿真因此不依赖外部工具。
BLUEPRINT = "minimal"

#: 捕获的念头 —— 顺序即 id 顺序（``INB-001`` …）。
#: 第 3 条**故意很长**，用来验证「归档永远只给标题行」（§2.5）。
CAPTURES = (
    ("登录页的报错文案要统一", ["体验", "文案"], "login", "现在中英混排"),
    ("登录失败没有任何提示", ["体验"], "login", None),
    (
        "登录页在三类极端窄屏（320 / 360 / 375）下都存在按钮裁切、"
        "文案折行错位、错误提示被键盘遮挡的问题，需要统一处理",
        [],
        None,
        None,
    ),
    ("支付回调偶发重复入账", ["性能"], "pay", None),
    ("支付日志缺 request id，出问题只能靠猜", ["性能"], "pay", None),
    ("README 里的安装步骤过期了", [], None, None),
)

LONG_ID = "INB-003"

#: 合并脚本：``(源 id…, 新表述)`` —— 第三个是**链式**（源里有上一轮的产物）。
MERGES = (
    (("INB-001", "INB-002"), "统一登录页的报错与提示文案"),
    (("INB-007", "INB-003"), "改善登录页的窄屏与文案体验"),
    (("INB-004", "INB-005"), "补齐支付链路的可观测性"),
)

#: 6 条原始 + 3 条产物。
EXPECTED_ITEMS = len(CAPTURES) + len(MERGES)

#: 由 ``main`` 探测后填入。
FORGE = ""

_checks: "list[tuple[str, bool, str]]" = []


# ------------------------------------------------------------------ 输出
def say(text: str = "") -> None:
    print(text, flush=True)


def head(text: str) -> None:
    say()
    say(f"── {text} " + "─" * max(0, 62 - len(text)))


def indent(text: str, prefix: str = "    ") -> str:
    body = (text or "").strip("\n")
    return "\n".join(prefix + line for line in body.splitlines()) if body else ""


def check(label: str, ok: bool, detail: str = "") -> bool:
    say(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  —— {detail}" if detail else ""))
    _checks.append((label, ok, detail))
    return ok


# ------------------------------------------------------------------ 执行
def forge_run(args: "list[str]", *, cwd: "Path | None" = None) -> subprocess.CompletedProcess:
    """跑 ``forge``，**FORGE_HOME 固定指向隔离目录**（这是仿真最重要的安全阀）。"""
    env = dict(os.environ)
    env["FORGE_HOME"] = str(FORGE_HOME)
    return subprocess.run(
        [FORGE, *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def emit(proc: subprocess.CompletedProcess) -> None:
    if proc.stdout.strip():
        say(indent(proc.stdout))
    if proc.stderr.strip():
        say(indent(proc.stderr, "    ! "))


def run_inbox(args: "list[str]") -> subprocess.CompletedProcess:
    """在项目根跑一条 ``inbox`` 命令，打印真实输出。"""
    return forge_run(["inbox", *args], cwd=DEMO)


# ------------------------------------------------------------------ 读盘
def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def has_key(obj, key: str) -> bool:
    """递归找 key 是否在结构里出现（用来证「没有 mergedInto / group 这类字段」）。"""
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


def show_artifacts() -> None:
    """把产物摊开列一遍。

    这一步是**必需的**，不是装饰：``.forge`` 是点开头的隐藏目录 ——
    资源管理器默认不显示，人翻目录时会以为"根本没落"。
    """
    rows: "list[tuple[str, str]]" = []
    if (DEMO / ".forge" / "inbox.json").is_file():
        rows.append(("demo/.forge/inbox.json", "念头账 · snapshot（活页 + 归档同一份）"))
    if (DEMO / ".forge" / "lock.json").is_file():
        rows.append(("demo/.forge/lock.json", "scaffold 的账本 —— forge new 落的"))
    if (FORGE_HOME / "projects.json").is_file():
        rows.append(("home/projects.json", "受管清单 —— 隔离的 home（真 ~/.forge 未被碰）"))
    for rel in ("AGENTS.md", "README.md", ".gitignore"):
        if (DEMO / rel).is_file():
            rows.append((f"demo/{rel}", "payload —— forge new 自己写"))

    width = max((len(path) for path, _ in rows), default=0)
    for path, note in rows:
        say(f"    {path:<{width}}   {note}")
    say()
    say("    ↑ `.forge` 是**点开头的隐藏目录** —— 资源管理器默认不显示，")
    say("      得开「显示隐藏文件」或用 `ls -a` 才看得见。")


# -------------------------------------------------------------------- 主流程
def main() -> int:
    global FORGE

    # -------------------------------------------------------- 0 · 探测
    head("0 · 探测命令")
    FORGE = shutil.which("forge") or ""
    say(f"  forge  {FORGE or '（不在 PATH）'}")
    if not FORGE:
        say()
        say("FAIL：找不到 `forge` 命令。先在仓库里装：uv tool install --editable .")
        return 1

    # -------------------------------------------------- 1 · 隔离 + 起项目
    head("1 · 隔离环境 + 起一个真项目")
    if RUN.exists():
        shutil.rmtree(RUN)
    DEMO.mkdir(parents=True)
    FORGE_HOME.mkdir(parents=True)
    say(f"  FORGE_HOME  {FORGE_HOME}")
    say(f"  目标项目    {DEMO}")
    proc = forge_run(["new", str(DEMO), "--blueprint", BLUEPRINT], cwd=DEMO)
    emit(proc)
    check("forge new 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("项目骨架已落（.forge/）", (DEMO / ".forge").is_dir())

    # -------------------------------------------------------- 2 · 捕获
    head("2 · 捕获念头 —— 先看**空**的 inbox，再一条条记")
    proc = run_inbox(["list"])
    emit(proc)
    check("空 inbox 不报错、给提示", proc.returncode == 0, f"rc={proc.returncode}")

    for index, (text, tags, source, note) in enumerate(CAPTURES, start=1):
        args = ["add", text]
        for tag in tags:
            args += ["--tag", tag]
        if source:
            args += ["--source", source]
        if note:
            args += ["--note", note]
        proc = run_inbox(args)
        emit(proc)
        check(f"捕获 INB-{index:03d} 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    # -------------------------------------------------------- 3 · 读视图
    head("3 · 读视图 —— list（一行）· list --full（全文）· show（含出处 / 备注）")
    say("  ▸ list —— 一眼扫完「还欠着什么」")
    emit(run_inbox(["list"]))

    say("  ▸ list --full —— 整理专用：text 全文（仍不给 note）")
    emit(run_inbox(["list", "--full"]))

    say("  ▸ show INB-004 —— 单条全文（出处 / 备注都在）")
    emit(run_inbox(["show", "INB-004"]))

    # -------------------------------------------------------- 4 · 整理
    head("4 · 整理 · 合并 —— agent 判断「哪几条是一件事」，引擎一次原子落盘")
    say("  ▸ 判据：同一处体验问题 → 合成一句够格的表述")
    for sources, text in MERGES:
        proc = run_inbox(["merge", *sources, "--text", text])
        emit(proc)
        check(f"合并 {'+'.join(sources)} 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    say("  ▸ 视图**不区分**产物与原料（都是普通条目，§10-36）—— list 里只看得到 open")
    emit(run_inbox(["list"]))

    # -------------------------------------------------------- 5 · 交接
    head("5 · 交接 —— open → 已交接 / 丢弃（各一步，带去向）")
    proc = run_inbox(["hand", "INB-008", "--to", "纳入 login 块"])
    emit(proc)
    check("交接（hand）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_inbox(["drop", "INB-006", "--to", "与既有的 README 重写计划重复"])
    emit(proc)
    check("丢弃（drop）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_inbox(["set", "INB-009", "--tag", "性能", "--note", "先做日志埋点"])
    emit(proc)
    check("兜底 set（不动状态）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    # -------------------------------------------------------- 6 · 再看视图
    head("6 · 再看视图 —— 6 条零散念头 → 1 条够格的主题")
    say("  ▸ list —— 活页（只剩 INB-009）")
    emit(run_inbox(["list"]))

    say("  ▸ list --all —— 加上归档（**只给标题行**）")
    emit(run_inbox(["list", "--all"]))

    say("  ▸ show INB-001 —— 那条念头最后去哪了：target 指着产物")
    emit(run_inbox(["show", "INB-001"]))

    # -------------------------------------------------------- 7 · 错路
    head("7 · 错路 —— 都该被拒（rc≠0），且一条都不落盘")

    say("  ▸ 合并只给 1 条源（1 条不需要「合并」）")
    proc = run_inbox(["merge", "INB-009", "--text", "x"])
    emit(proc)
    check("源 < 2 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ 合并一条**已经归档**的条目（一次交接即终结）")
    proc = run_inbox(["merge", "INB-009", "INB-001", "--text", "x"])
    emit(proc)
    check("合已归档条目被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ 交接缺 --to（这东西最后落哪了 —— 是事实）")
    proc = run_inbox(["hand", "INB-009"])
    emit(proc)
    check("hand 缺 --to 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ set 什么都不给")
    proc = run_inbox(["set", "INB-009"])
    emit(proc)
    check("set 空参被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ 不存在的条目")
    proc = run_inbox(["show", "INB-042"])
    emit(proc)
    check("show 不存在被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ id 形态不成立")
    proc = run_inbox(["show", "XYZ-1"])
    emit(proc)
    check("非法 id 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    # ----------------------------------------------------- 8 · --json
    head("8 · --json —— 同一视图的另一种序列化（不是原文，过滤与体量照旧）")
    proc = run_inbox(["list", "--json"])
    try:
        payload = json.loads(proc.stdout)
        check("--json 可解析", True)
        check("活页只剩 1 条（INB-009）",
              [row["id"] for row in payload] == ["INB-009"], f"{[r['id'] for r in payload]}")
        check("行里**没有** source / note / target（它们归 show）",
              all(set(row) <= {"id", "text", "status", "created", "tags"} for row in payload))
        check("set 追加的 tag 在", payload[0].get("tags") == ["性能"], f"{payload[0].get('tags')}")
    except (json.JSONDecodeError, ValueError) as exc:
        check("--json 可解析", False, str(exc))

    say("  ▸ list --full --all --json —— 归档条目**即使加了 --full 也只给标题行**")
    proc = run_inbox(["list", "--full", "--all", "--json"])
    try:
        rows = json.loads(proc.stdout)
        long_row = next(row for row in rows if row["id"] == LONG_ID)
        check("归档条目已截断（无界增长 → 不给正文）",
              long_row["text"].endswith("…"), f"len={len(long_row['text'])}")
        check("归档条目的 status 是 handed", long_row["status"] == "handed")
    except (json.JSONDecodeError, ValueError, StopIteration) as exc:
        check("list --full --all --json 可解析", False, str(exc))

    # ------------------------------------------------------ 9 · 产物核验
    head("9 · 产物核验 —— 落盘与设计逐条对齐")
    inbox_path = DEMO / ".forge" / "inbox.json"

    say("  ▸ inbox.json —— 一个 kind 一份文件（活页与归档同住这里）")
    if check("inbox.json 已落", inbox_path.is_file()):
        doc = read_json(inbox_path)
        items = doc.get("items", [])
        by_id = {item["id"]: item for item in items}

        check("version = 1", doc.get("version") == 1, f"version={doc.get('version')}")
        check(f"共 {EXPECTED_ITEMS} 条（{len(CAPTURES)} 条原始 + {len(MERGES)} 条产物）",
              len(items) == EXPECTED_ITEMS, f"{len(items)} 条")
        check("★ id 递增**不复用**（INB-001 … INB-009 连续）",
              [item["id"] for item in items]
              == [f"INB-{n:03d}" for n in range(1, EXPECTED_ITEMS + 1)],
              f"{[item['id'] for item in items]}")

        check("★ open 条目**没有** target 字段（schema 条件约束）",
              all("target" not in item for item in items if item["status"] == "open"))
        check("handed / dropped 的 target 都非空",
              all(item.get("target") for item in items if item["status"] != "open"))

        check("源 INB-001 / INB-002 的 target 指向产物 INB-007",
              by_id["INB-001"]["target"] == "并入 INB-007"
              and by_id["INB-002"]["target"] == "并入 INB-007",
              f"{by_id['INB-001']['target']} · {by_id['INB-002']['target']}")
        check("★ 链式：产物 INB-007 自己也被合走了（target → INB-008）",
              by_id["INB-007"]["status"] == "handed"
              and by_id["INB-007"]["target"] == "并入 INB-008")
        check("支付两条（INB-004 / INB-005）→ INB-009",
              by_id["INB-004"]["target"] == "并入 INB-009"
              and by_id["INB-005"]["target"] == "并入 INB-009")
        check("INB-006 是 dropped，去向写明理由",
              by_id["INB-006"]["status"] == "dropped"
              and "重复" in by_id["INB-006"]["target"])
        check("INB-008 是 handed，去向指向 login 块",
              by_id["INB-008"]["status"] == "handed"
              and by_id["INB-008"]["target"] == "纳入 login 块")
        check("★ 活页只剩 INB-009（open）",
              [item["id"] for item in items if item["status"] == "open"] == ["INB-009"])
        check("INB-009 的 tag / note 是 set 写的（产物**不继承**源的可选字段）",
              by_id["INB-009"].get("tags") == ["性能"]
              and by_id["INB-009"].get("note") == "先做日志埋点")

        check("★ 没有 mergedInto / mergedFrom / group 字段（合并**零模型改动**）",
              not any(has_key(doc, key) for key in ("mergedInto", "mergedFrom", "group")))

    # ---------------------------------------------------- 10 · 产物一览
    head("10 · 产物一览 —— 点开头的都是隐藏目录")
    show_artifacts()

    # -------------------------------------------------------- 结论
    head("结论")
    failed = [label for label, ok, _ in _checks if not ok]
    say(f"  断言通过 {len(_checks) - len(failed)}/{len(_checks)}")
    for label in failed:
        say(f"    FAIL · {label}")
    say(f"  产物留在  {RUN}")
    say(f"  受管清单  {FORGE_HOME / 'projects.json'}")
    say("            ↑ 这是**隔离的** FORGE_HOME —— 真 ~/.forge 全程未被碰 ✅")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
