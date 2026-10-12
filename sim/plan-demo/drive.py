#!/usr/bin/env python3
"""plan 端到端仿真 —— 走一遍**真实项目里的计划流程**，验证落盘与视图。

跑法（任意目录均可，建议在仓库根）：

    python sim/plan-demo/drive.py

全程落在 ``sim/plan-demo/_run/`` 内，**绝不碰真的 ``~/.forge``**：

    0. 探测 ``forge``
    1. 清空 ``_run/``、建隔离 ``FORGE_HOME``；``forge new`` 起一个真项目
       （``minimal`` 蓝图 —— 纯 payload、无委托，所以这一步不依赖 skill / aiwf）
    2. 建块 + 建任务                       —— ``plan add``
    3. 「打回两次后完成」                  —— ``claim`` → ``reject`` ×2 → ``done``
    4. 读视图                              —— ``show`` / ``show <块>`` / ``show <块>.<任务>`` / ``log``
    5. 块级动作 + 第二个块                 —— ``done <块>`` → reached · ``drop <块>`` → dropped
    6. 错路                                —— 裸地址 / 缺 ``--note`` / 不存在 / ``deps`` 跨块
    7. ``--json`` 一致性
    8. 产物核验                            —— ``plans.json`` 集合快照 + ``plan_log.jsonl`` 轨迹 + note **零重复**
    9. 产物一览 · 结论

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

#: 演示内容：一块正常推进 + 一块被放弃。
BLOCK = "login"
BLOCK2 = "pay"
TASK = "login.T1"
REJECT_NOTES = ("密码强度校验缺失，未过", "强度校验补了，但错误文案是英文")

#: 计划里的写操作总数（一步一条轨迹，§2.1 纪律 1）—— 错路那几条**不落盘**，不计入。
EXPECTED_EVENTS = 12

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


def run_plan(args: "list[str]") -> subprocess.CompletedProcess:
    """在项目根跑一条 ``plan`` 命令，打印真实输出。"""
    return forge_run(["plan", *args], cwd=DEMO)


# ------------------------------------------------------------------ 读盘
def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> "list[dict]":
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def has_key(obj, key: str) -> bool:
    """递归找 key 是否在结构里出现（用来证「note 零重复」）。"""
    if isinstance(obj, dict):
        return key in obj or any(has_key(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(has_key(v, key) for v in obj)
    return False


def find_plan(doc: dict, plan_id: str) -> "dict | None":
    for plan in doc.get("plans", []):
        if plan.get("id") == plan_id:
            return plan
    return None


def show_artifacts() -> None:
    """把产物摊开列一遍。

    这一步是**必需的**，不是装饰：``.forge`` 是点开头的隐藏目录 ——
    资源管理器默认不显示，人翻目录时会以为"根本没落"。
    """
    rows: "list[tuple[str, str]]" = []
    if (DEMO / ".forge" / "plans.json").is_file():
        rows.append(("demo/.forge/plans.json", "块集合 · snapshot（一个 kind 一个文件）"))
    if (DEMO / ".forge" / "plan_log.jsonl").is_file():
        rows.append(("demo/.forge/plan_log.jsonl", "过程轨迹 · log（一行一事件，只追加）"))
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
    say("  先 `forge new` 建骨架，再在这个真项目里做计划。")
    proc = forge_run(["new", str(DEMO), "--blueprint", BLUEPRINT], cwd=DEMO)
    emit(proc)
    check("forge new 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("项目骨架已落（.forge/）", (DEMO / ".forge").is_dir())

    # ------------------------------------------------ 2 · 建块 + 建任务
    head("2 · 建块 + 建任务（写动词 add）")
    proc = run_plan([
        "add", BLOCK,
        "--title", "登录模块",
        "--goal", "让用户能安全登录 / 注册 / 找回密码",
        "--criteria", "注册后能收到验证邮件",
        "--criteria", "会话过期需重新登录",
    ])
    emit(proc)
    check("建块退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    for addr, title, done_when, deps in (
        ("login.T1", "实现登录页", "能从登录页登录成功", []),
        # T2 用**规范全地址**（§2.1 纪律 5「地址带块」）；T3 用**块内裸 id** —— 两种都收（H2）
        ("login.T2", "会话保持", "关掉浏览器再开仍在线", ["login.T1"]),
        ("login.T3", "错误提示", "错误文案是中文且准确", ["T1"]),
    ):
        args = ["add", addr, "--title", title, "--done-when", done_when]
        for dep in deps:
            args += ["--deps", dep]
        proc = run_plan(args)
        emit(proc)
        check(f"建任务 {addr} 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    # ---------------------------------------- 3 · 打回两次后完成
    head("3 · 「打回两次后完成」—— 一次意图 = 一次调用")
    proc = run_plan(["claim", TASK])
    emit(proc)
    check("认领（claim）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    for note in REJECT_NOTES:
        proc = run_plan(["reject", TASK, "--note", note])
        emit(proc)
        check(f"打回（reject）退出码 0 · {note[:8]}…", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_plan(["done", TASK, "--note", "补了中文文案，通过"])
    emit(proc)
    check("完成（done）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    say()
    say("  ▸ 兜底 set：只改 note（状态不变）—— 轨迹照样追记，plans.json 不动")
    proc = run_plan(["set", "login.T2", "--note", "已在做，等 T1 落地"])
    emit(proc)
    check("set（只改 note）退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    # ------------------------------------------------------ 4 · 读视图
    head("4 · 读视图 —— show / show <块> / show <块>.<任务> / log")

    say("  ▸ show —— 全局列表（不含 goal / 轨迹）")
    emit(run_plan(["show"]))

    say("  ▸ show login —— 单块（title / goal / 判据 / 任务）")
    emit(run_plan(["show", BLOCK]))

    say("  ▸ show login.T1 —— 单任务交接包（状态 + 完整轨迹）")
    packet = run_plan(["show", TASK])
    emit(packet)

    say("  ▸ log —— 跨块时间线（默认尾部 20 条）")
    emit(run_plan(["log"]))

    # ------------------------------------------ 5 · 块级动作 + 第二个块
    head("5 · 块级动作 + 第二个块")
    proc = run_plan([
        "add", BLOCK2,
        "--title", "支付模块",
        "--goal", "让用户能完成一次支付",
        "--criteria", "支付成功后有订单记录",
    ])
    emit(proc)
    check("建第二块退出码 0", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_plan(["drop", BLOCK2, "--note", "预算砍了，本季不做"])
    emit(proc)
    check("放弃一块（drop）→ dropped", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_plan(["done", BLOCK, "--note", "登录整块验收通过"])
    emit(proc)
    check("完成一块（done）→ reached", proc.returncode == 0, f"rc={proc.returncode}")

    say("  ▸ show —— 现在的全局列表")
    emit(run_plan(["show"]))

    # -------------------------------------------------------- 6 · 错路
    head("6 · 错路 —— 都该被拒（rc≠0），且一条都不落盘")

    say("  ▸ 裸地址给任务动词（login 是块，不是任务）")
    proc = run_plan(["claim", BLOCK])
    emit(proc)
    check("claim <块> 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ 裸任务地址（T1 无块前缀 → 无隐式解析）")
    proc = run_plan(["done", "T1"])
    emit(proc)
    check("done T1 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ reject 缺 --note（打回不给原因）")
    proc = run_plan(["reject", TASK])
    emit(proc)
    check("reject 无 --note 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ 不存在的块")
    proc = run_plan(["show", "nope"])
    emit(proc)
    check("show nope 被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ deps 指向不存在的任务")
    proc = run_plan(["add", "login.T9", "--title", "多余的", "--deps", "login.T99"])
    emit(proc)
    check("deps 不存在被拒", proc.returncode != 0, f"rc={proc.returncode}")

    say("  ▸ deps 跨块（pay.T1 —— 依赖别的块）")
    proc = run_plan(["add", "login.T9", "--title", "多余的", "--deps", "pay.T1"])
    emit(proc)
    check("deps 跨块被拒", proc.returncode != 0, f"rc={proc.returncode}")

    # ----------------------------------------------------- 7 · --json
    head("7 · --json —— 同一视图的另一种序列化")
    proc = run_plan(["show", TASK, "--json"])
    try:
        payload = json.loads(proc.stdout)
        check("--json 可解析", True)
        check("status 与文本视图一致 = done", payload.get("status") == "done",
              f"status={payload.get('status')}")
        check("轨迹 5 条（add·claim·reject·reject·done）", len(payload.get("events", [])) == 5,
              f"{len(payload.get('events', []))} 条")
    except (json.JSONDecodeError, ValueError) as exc:
        check("--json 可解析", False, str(exc))

    # ------------------------------------------------------ 8 · 产物核验
    head("8 · 产物核验 —— 落盘与设计逐条对齐")

    plans_path = DEMO / ".forge" / "plans.json"
    log_path = DEMO / ".forge" / "plan_log.jsonl"

    say("  ▸ plans.json —— 块集合快照")
    if check("plans.json 已落", plans_path.is_file()):
        doc = read_json(plans_path)
        check("version = 1", doc.get("version") == 1, f"version={doc.get('version')}")
        plans = doc.get("plans", [])
        check("是**集合**（2 块）", isinstance(plans, list) and len(plans) == 2,
              f"{len(plans)} 块")
        login = find_plan(doc, BLOCK)
        pay = find_plan(doc, BLOCK2)
        check("login 存在且 status = reached", bool(login) and login.get("status") == "reached",
              f"status={login.get('status') if login else '—'}")
        check("login 有 3 个任务", bool(login) and len(login.get("tasks", [])) == 3,
              f"{len(login.get('tasks', [])) if login else '—'} 个")
        check("T2（全地址）/ T3（裸 id）的 deps 都归一化为 ['T1']",
              bool(login) and all(
                  t.get("deps") == ["T1"]
                  for t in login.get("tasks", [])
                  if t.get("id") in ("T2", "T3")
              ))
        check("login.T2 仍是 todo（set --note 未改状态）",
              bool(login) and any(t.get("id") == "T2" and t.get("status") == "todo"
                                  for t in login.get("tasks", [])))
        check("login.T1 status = done",
              bool(login) and any(t.get("id") == "T1" and t.get("status") == "done"
                                  for t in login.get("tasks", [])))
        check("pay 存在且 status = dropped", bool(pay) and pay.get("status") == "dropped",
              f"status={pay.get('status') if pay else '—'}")
        check("★ plans.json 里**没有 note 字段**（note 只住 log，零重复）",
              not has_key(doc, "note"))

    say("  ▸ plan_log.jsonl —— 过程轨迹（一行一事件）")
    if check("plan_log.jsonl 已落", log_path.is_file()):
        events = read_jsonl(log_path)
        check("每行都是合法 JSON、都带 plan / id / op",
              all({"plan", "id", "op"} <= set(e) for e in events))
        check(f"事件总数 = {EXPECTED_EVENTS}（一步一条轨迹）", len(events) == EXPECTED_EVENTS,
              f"{len(events)} 条")

        t1 = [e for e in events if e.get("plan") == BLOCK and e.get("id") == "T1"]
        ops = [e.get("op") for e in t1]
        check("login.T1 轨迹 = add·claim·reject·reject·done",
              ops == ["add", "claim", "reject", "reject", "done"], f"{ops}")

        reject_notes = [e.get("note") for e in t1 if e.get("op") == "reject"]
        check("两条打回的原因都在、且不同",
              reject_notes == list(REJECT_NOTES), f"{reject_notes}")

        # claim 与 reject 的 (from → to) 都是 todo/doing → doing —— 靠 op 区分（§2.5.1）
        claim_ev = next((e for e in t1 if e.get("op") == "claim"), {})
        reject_ev = next((e for e in t1 if e.get("op") == "reject"), {})
        check("claim 与 reject 都落到 doing（靠 op 区分语义）",
              claim_ev.get("to") == "doing" and reject_ev.get("to") == "doing",
              f"claim→{claim_ev.get('to')} · reject→{reject_ev.get('to')}")

        set_ev = [e for e in events if e.get("op") == "set"]
        check("set（只改 note）也追了轨迹，且无 from/to",
              len(set_ev) == 1 and "from" not in set_ev[0] and "to" not in set_ev[0])

        drop_ev = [e for e in events if e.get("plan") == BLOCK2 and e.get("op") == "drop"]
        check("pay 的 drop 事件在（→ dropped）",
              len(drop_ev) == 1 and drop_ev[0].get("to") == "dropped")

        done_block = [e for e in events if e.get("plan") == BLOCK and e.get("id") == BLOCK
                      and e.get("op") == "done"]
        check("login 的块级 done 事件在（→ reached）",
              len(done_block) == 1 and done_block[0].get("to") == "reached")

    # ---------------------------------------------------- 9 · 产物一览
    head("9 · 产物一览 —— 点开头的都是隐藏目录")
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
