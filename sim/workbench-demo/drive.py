#!/usr/bin/env python3
"""workbench 端到端仿真 —— 走一遍**跨项目总览 + 本项目一眼状态**。

跑法（任意目录均可，建议在仓库根）：

    python sim/workbench-demo/drive.py

全程落在 ``sim/workbench-demo/_run/`` 内，**绝不碰真的 ``~/.forge``**：

    0. 探测 ``forge``（顺带记录 ``aiwf`` 是否可用）
    1. 清空 ``_run/``、建隔离 ``FORGE_HOME``；``forge new`` 起**两个**真项目
    2. 往 alpha 灌数据            —— ``plan add`` / ``claim`` / ``done`` / ``block`` + ``inbox add`` / ``hand``
    3. 从**任意目录**跑 ``forge workbench`` —— 一项目一段 · 身份行 · 「焦点 无」
    4. 进 alpha 跑 ``forge status`` —— **去身份行**（与全局那段同形）
    5. 未受管目录跑 ``forge status`` —— 退化标注「（未受管）」
    6. ``--json`` 一致性          —— ``workbench`` 里 alpha 那段 **==** alpha 里的 ``status``
    7. 不受 CWD 影响              —— 两个不同目录跑 ``workbench``，输出逐字一致
    8. aiwf 那一路（**真调**）     —— 隔离项目没接流程 → 「流程 0 · 运行 0 —— 未接流程」
    9. 真环境**只读**快照（可选）  —— 真 ``~/.forge`` 上跑一次 ``workbench``（**只读，不写**）
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
ALPHA = RUN / "alpha"
BETA = RUN / "beta"
GAMMA = RUN / "gamma"  # 不登记 —— 演「未受管」

BLUEPRINT = "minimal"

#: 由 ``main`` 探测后填入。
FORGE = ""
AIWF = ""

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
def forge_run(
    args: "list[str]", *, cwd: "Path | None" = None, isolate: bool = True
) -> subprocess.CompletedProcess:
    """跑 ``forge``。``isolate=True`` 时 **FORGE_HOME 固定指向隔离目录**（安全阀）。"""
    env = dict(os.environ)
    if isolate:
        env["FORGE_HOME"] = str(FORGE_HOME)
    else:
        env.pop("FORGE_HOME", None)  # 真环境：用真的 ~/.forge（只读用途）
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
    return forge_run(["plan", *args], cwd=ALPHA)


def run_inbox(args: "list[str]", cwd: "Path | None" = None) -> subprocess.CompletedProcess:
    return forge_run(["inbox", *args], cwd=cwd or ALPHA)


def norm_path(text: str) -> str:
    """把路径规整到可比较（大小写 + 分隔符）—— Windows 盘符大小写会漂。"""
    return os.path.normcase(os.path.normpath(text))


# -------------------------------------------------------------------- 主流程
def main() -> int:
    global FORGE, AIWF

    # -------------------------------------------------------- 0 · 探测
    head("0 · 探测命令")
    FORGE = shutil.which("forge") or ""
    AIWF = shutil.which("aiwf") or ""
    say(f"  forge  {FORGE or '（不在 PATH）'}")
    say(f"  aiwf   {AIWF or '（不在 PATH —— aiwf 那一路会显示「读不到」）'}")
    if not FORGE:
        say()
        say("FAIL：找不到 `forge` 命令。先在仓库里装：uv tool install --editable .")
        return 1

    # -------------------------------------------------- 1 · 隔离 + 起项目
    head("1 · 隔离环境 + 起两个真项目（+ 一个不登记，演未受管）")
    if RUN.exists():
        shutil.rmtree(RUN)
    FORGE_HOME.mkdir(parents=True)
    for path in (ALPHA, BETA, GAMMA):
        path.mkdir(parents=True)
    say(f"  FORGE_HOME  {FORGE_HOME}")
    for path, name in ((ALPHA, "alpha"), (BETA, "beta")):
        proc = forge_run(["new", str(path), "--blueprint", BLUEPRINT], cwd=path)
        check(f"forge new {name} 退出码 0", proc.returncode == 0, f"rc={proc.returncode}")
    say(f"  gamma       {GAMMA}（**不登记** —— 演示未受管）")

    # -------------------------------------------------- 2 · 灌数据（alpha）
    head("2 · 往 alpha 灌数据 —— 一份计划（4 任务各态）+ 三条念头")
    proc = run_plan(
        [
            "add",
            "login",
            "--title",
            "登录闭环",
            "--goal",
            "账号能登进来",
            "--criteria",
            "注册后能收到验证邮件",
            "--criteria",
            "会话过期需重新登录",
        ]
    )
    check("建计划 login", proc.returncode == 0, f"rc={proc.returncode}")
    for address, title in (
        ("login.T1", "接验证邮件"),
        ("login.T2", "会话续期"),
        ("login.T3", "失败提示统一"),
        ("login.T4", "第三方登录"),
    ):
        proc = run_plan(["add", address, "--title", title])
        check(f"建任务 {address}", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_plan(["claim", "login.T2"])
    check("claim login.T2 → doing", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_plan(["done", "login.T1", "--note", "邮件通了"])
    check("done login.T1", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_plan(["block", "login.T4", "--note", "等第三方开放平台审批"])
    check("block login.T4 → blocked", proc.returncode == 0, f"rc={proc.returncode}")

    proc = run_inbox(["add", "登录页报错文案要统一", "--tag", "体验", "--source", "login"])
    check("记念头 1", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_inbox(["add", "支付日志缺 request id", "--tag", "性能"])
    check("记念头 2", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_inbox(["add", "README 安装步骤过期"])
    check("记念头 3", proc.returncode == 0, f"rc={proc.returncode}")
    proc = run_inbox(["hand", "INB-003", "--to", "文档重写计划"])
    check("交接 INB-003 → handed", proc.returncode == 0, f"rc={proc.returncode}")

    # ------------------------------------------- 3 · workbench（任意目录）
    head("3 · forge workbench —— 从**任意目录**跑，一项目一段")
    proc = forge_run(["workbench"], cwd=HERE)
    emit(proc)
    board = proc.stdout
    check("workbench rc 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("抬头「2 个项目 · 焦点 无」", "2 个项目 · 焦点 无" in board)
    check("alpha 有身份行（alias · blueprint · path）", f"alpha · {BLUEPRINT} · " in board)
    check("beta 有身份行", f"beta · {BLUEPRINT} · " in board)
    check(
        "alpha 的 plan 一行（计数 + 分布 + 上次变动）",
        "1 份计划（open 1 · reached 0 · dropped 0）" in board
        and "任务 4（done 1 · doing 1 · todo 1 · blocked 1）" in board
        and "上次变动 " in board,
    )
    check("alpha 的「在做」行", "在做 1  login.T2 会话续期" in board)
    check("alpha 的「卡住」行", "卡住 1  login.T4 第三方登录" in board)
    check(
        "alpha 的 inbox 一行（分布 + 最近 + 最老未处理）",
        "3 条（open 2 · handed 1 · dropped 0）" in board
        and "最老未处理 " in board,
    )
    check("beta 的 plan 空态「未记账」", "0 份计划 —— 未记账" in board)
    check("beta 的 inbox 空态「0 条」", "0 条" in board)
    # ★ v12：inbox 最近加入 3 条（主人 2026-10-11）
    check(
        "alpha 的 inbox「最近加入」三条",
        "最近加入" in board and "INB-001" in board and "INB-002" in board and "INB-003" in board,
    )
    # ★ v12：顶部「最近动态」时间线（按时间降序 · 跨项目）
    check("顶部有「最近动态」时间线", "最近动态" in board)
    check("时间线含 alpha 的在做", "在做 1 项：login.T2 会话续期" in board)
    check("时间线含 alpha 的念头", "记了念头 INB-003" in board)

    # aiwf 那一路 —— 真调（隔离项目没接流程）
    if AIWF:
        check(
            "alpha 的 aiwf 段 = 「未接流程」（真调 aiwf 的空态）",
            "流程 0 · 运行 0 —— 未接流程" in board,
        )
    else:
        check("alpha 的 aiwf 段显示「读不到」（aiwf 不在 PATH）", "读取失败：aiwf 不在 PATH" in board)

    # ------------------------------------------------- 4 · status（本项目）
    head("4 · forge status —— 进 alpha 跑，**去身份行**（与全局那段同形）")
    proc = forge_run(["status"], cwd=ALPHA)
    emit(proc)
    status_text = proc.stdout
    lines = [line for line in status_text.splitlines() if line.strip()]
    check("status rc 0", proc.returncode == 0, f"rc={proc.returncode}")
    check(
        "首行 = alpha 的绝对路径（**没有** alias · blueprint）",
        lines and norm_path(lines[0]) == norm_path(str(ALPHA)),
        f"首行={lines[0] if lines else '（空）'}",
    )
    check("status 段与全局那段同形（plan 行一致）", "1 份计划（open 1 · reached 0 · dropped 0）" in status_text)
    check("status 段含「在做」行", "在做 1  login.T2 会话续期" in status_text)
    check("status 也带本项目「最近动态」", "最近动态" in status_text)
    check("status 的 inbox 也有「最近加入」", "最近加入" in status_text)

    # ------------------------------------------------- 5 · 未受管目录
    head("5 · 未受管目录跑 forge status —— 退化标注")
    proc = forge_run(["status"], cwd=GAMMA)
    emit(proc)
    check("未受管 rc 0（照常可用）", proc.returncode == 0, f"rc={proc.returncode}")
    check("标注「（未受管）」", "（未受管）" in proc.stdout)
    check("未受管三分区照读", "0 份计划 —— 未记账" in proc.stdout and "0 条" in proc.stdout)

    # ------------------------------------------------- 6 · --json 一致
    head("6 · --json 一致性 —— workbench 里 alpha 那段 == alpha 里的 status")
    board_json = forge_run(["workbench", "--json"], cwd=HERE)
    status_json = forge_run(["status", "--json"], cwd=ALPHA)
    try:
        whole = json.loads(board_json.stdout)
        piece = json.loads(status_json.stdout)
        alpha = next(p for p in whole["projects"] if p["alias"] == "alpha")
        # path 归一后比较（大小写 / 分隔符可能漂）
        normalized = dict(alpha)
        normalized["path"] = norm_path(alpha["path"])
        piece_norm = dict(piece)
        piece_norm["path"] = norm_path(piece["path"])
        check("workbench --json 可解析", True)
        check("status --json 可解析", True)
        check("★ 两出口是**同一份视图**（同形）", normalized == piece_norm)
        check("三分区结构齐", set(alpha) >= {"plan", "inbox", "aiwf", "alias", "path"})
    except (json.JSONDecodeError, ValueError, StopIteration) as exc:
        check("--json 一致", False, str(exc))

    # ------------------------------------------------- 7 · 不受 CWD 影响
    head("7 · workbench 不受 CWD 影响 —— 两个不同目录跑，输出逐字一致")
    from_home = forge_run(["workbench"], cwd=HERE)
    from_alpha = forge_run(["workbench"], cwd=ALPHA)
    check("从 HERE 与从 alpha 跑 workbench 输出一致", from_home.stdout == from_alpha.stdout)
    check("workbench（全局）**不**含「（未受管）」", "（未受管）" not in from_home.stdout)

    # ------------------------------------------- 8 · 真环境只读快照（可选）
    head("8 · 真环境**只读**快照 —— 真 ~/.forge 上跑一次 workbench（不写任何东西）")
    say("  ▸ 这一节**只读**：workbench 没有写动词（§8-1），全程不改一个字节。")
    real = forge_run(["workbench"], cwd=HERE, isolate=False)
    if real.returncode == 0 and real.stdout.strip():
        emit(real)
        has_running = "在等你：" in real.stdout
        say(f"  ▸ 真环境里有停在人工门的 run：{'是' if has_running else '否'}（有则说明 aiwf running/wait 那一路在真机上通了）")
    else:
        say("  （真 ~/.forge 下没有受管项目，跳过）")

    # ---------------------------------------------------- 9 · 产物一览
    head("9 · 产物一览 —— 点开头的都是隐藏目录")
    rows: "list[tuple[str, str]]" = []
    for label, path, note in (
        ("home/projects.json", FORGE_HOME / "projects.json", "受管清单 —— 隔离的 home（真 ~/.forge 未被碰）"),
        ("alpha/.forge/plans.json", ALPHA / ".forge" / "plans.json", "alpha 的计划账"),
        ("alpha/.forge/plan_log.jsonl", ALPHA / ".forge" / "plan_log.jsonl", "alpha 的过程轨迹"),
        ("alpha/.forge/inbox.json", ALPHA / ".forge" / "inbox.json", "alpha 的念头账"),
        ("beta/.forge/lock.json", BETA / ".forge" / "lock.json", "beta 的 scaffold 账本"),
    ):
        if path.is_file():
            rows.append((label, note))
    width = max((len(label) for label, _ in rows), default=0)
    for label, note in rows:
        say(f"    {label:<{width}}   {note}")
    say()
    say("    ↑ `.forge` 是**点开头的隐藏目录** —— 资源管理器默认不显示，得 `ls -a` 才看得见。")

    # -------------------------------------------------------- 结论
    head("结论")
    failed = [label for label, ok, _ in _checks if not ok]
    say(f"  断言通过 {len(_checks) - len(failed)}/{len(_checks)}")
    for label in failed:
        say(f"    FAIL · {label}")
    say(f"  产物留在  {RUN}")
    say(f"  受管清单  {FORGE_HOME / 'projects.json'}")
    say("            ↑ 这是**隔离的** FORGE_HOME —— 真 ~/.forge 全程只被**读**过 ✅")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
