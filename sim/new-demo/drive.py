#!/usr/bin/env python3
"""forge new / forge sync 端到端仿真 —— 建一个真项目，验证三类落位与增量对齐。

跑法（任意目录均可，建议在仓库根）：

    python sim/new-demo/drive.py

全程落在 ``sim/new-demo/_run/`` 内，**绝不碰真的 ``~/.forge``**：

    0. 探测 forge / skill / aiwf 三个命令
    1. 清空 ``_run/``，建隔离的 ``FORGE_HOME``（蓝图库也拷一份副本进 ``_run/``）
    2. ``forge new <demo> --dry-run``   —— 看变更集，一个字节都不写
    3. ``forge new <demo>``             —— 真跑：payload 自落 + skill / aiwf 委托上游
    4. 逐条断言，打印 PASS / FAIL
    5. 同一路径重跑                    —— 期望**幂等**：全「跳过」、一个字节都不动
    6. ``forge sync <demo>``            —— 模拟「蓝图更新」→ 补落新项（依赖管理入口，§7 T5）

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
LIB = HERE / "blueprints"
#: 跑起来用的库 = ``LIB`` 的**副本**（步骤 6 要改蓝图，绝不改仓库里的源）。
LIB_RUN = RUN / "blueprints"

BLUEPRINT = "demo"
SKILL_NAME = "forge"
AIWF_WORKFLOW = "std.review-check"

PAYLOAD_FILES = ("README.md", "AGENTS.md")

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


def delegated_skills() -> "list[str]":
    """动态发现委托 skill 的落点 —— 不硬编码 agent 表（那是上游的事）。

    只看 ``<demo>/*/skills/<名>/SKILL.md`` 这一层，两种目录命名都能覆盖。
    """
    hits = []
    for child in sorted(DEMO.iterdir()):
        marker = child / "skills" / SKILL_NAME / "SKILL.md"
        if child.is_dir() and marker.is_file():
            hits.append(marker.relative_to(DEMO).as_posix())
    return hits


def show_artifacts() -> None:
    """把产物摊开列一遍。

    这一步是**必需的**，不是装饰：``.trae`` / ``.workbuddy`` / ``.doubao`` /
    ``.qoder`` / ``.aiwf`` / ``.forge`` **全是点开头的隐藏目录** ——
    资源管理器默认不显示，人翻目录时会以为"委托项根本没落"。
    """
    rows: "list[tuple[str, str]]" = []
    for rel in PAYLOAD_FILES:
        rows.append((f"demo/{rel}", "payload —— forge 自己写"))
    if (DEMO / ".forge" / "lock.json").is_file():
        rows.append(("demo/.forge/lock.json", "forge 的账本 —— 只记 payload 项"))
    if (FORGE_HOME / "projects.json").is_file():
        rows.append(("home/projects.json", "受管清单 —— 隔离的 home（真 ~/.forge 未被碰）"))
    for hit in delegated_skills():
        rows.append((f"demo/{hit}", "委托 skill install 落的"))
    for wf in sorted((DEMO / ".aiwf" / "workflows").glob("*.json")):
        rows.append((f"demo/{wf.relative_to(DEMO).as_posix()}", "委托 aiwf wf install 落的"))
    if (DEMO / ".aiwf" / "installed.json").is_file():
        rows.append(("demo/.aiwf/installed.json", "aiwf 自己的账本（权威在它那）"))
    if (DEMO / "CHANGELOG.md").is_file():
        rows.append(("demo/CHANGELOG.md", "forge sync 补落的 —— 蓝图后补项"))

    width = max((len(path) for path, _ in rows), default=0)
    for path, note in rows:
        say(f"    {path:<{width}}   {note}")
    say()
    say("    ↑ `.trae` `.workbuddy` `.doubao` `.qoder` `.aiwf` `.forge` **全是点开头的** ——")
    say("      资源管理器默认隐藏，得开「显示隐藏文件」或用 `ls -a` 才看得见。")


# -------------------------------------------------------------------- 主流程
def main() -> int:
    global FORGE

    head("0 · 探测命令")
    tools = {name: shutil.which(name) for name in ("forge", "skill", "aiwf")}
    for name in ("forge", "skill", "aiwf"):
        say(f"  {name:<6} {tools[name] or '（不在 PATH）'}")
    if not tools["forge"]:
        say()
        say("FAIL：找不到 `forge` 命令。先在仓库里装：uv tool install --editable .")
        return 1
    FORGE = tools["forge"]
    absent = [name for name in ("skill", "aiwf") if not tools[name]]
    if absent:
        say(f"  注意：{'、'.join(absent)} 不在 PATH —— 对应的委托项会**停在那一步**"
            f"（前面的照常保留，这是 forge 的既定行为，不是 bug）。")

    head("1 · 隔离环境")
    if RUN.exists():
        shutil.rmtree(RUN)
    DEMO.mkdir(parents=True)
    FORGE_HOME.mkdir(parents=True)
    shutil.copytree(LIB, LIB_RUN)
    say(f"  FORGE_HOME  {FORGE_HOME}")
    say(f"  目标项目    {DEMO}")
    say(f"  蓝图库      {LIB_RUN}（仓库源的副本 —— 步骤 6 会改它，源不动）")
    say("  提示：产物里 `.trae` / `.workbuddy` / `.aiwf` 等**都是点开头的隐藏目录** ——")
    say("        资源管理器默认不显示。跑完末尾会列一份「产物一览」。")

    # ---------------------------------------------------------- 2 · 预演
    head("2 · 预演（--dry-run）—— 应当一个字节都不写")
    base = ["new", str(DEMO), "--blueprints", str(LIB_RUN), "--blueprint", BLUEPRINT]
    proc = forge_run([*base, "--dry-run"])
    emit(proc)
    check("退出码为 0", proc.returncode == 0, f"rc={proc.returncode}")
    check("输出标明「预演（未落盘）」", "预演" in proc.stdout)
    written = sorted(p.relative_to(DEMO).as_posix() for p in DEMO.rglob("*") if p.is_file())
    check("目标目录仍为空", not written, f"却多出 {written}" if written else "0 个文件")

    # ---------------------------------------------------------- 3 · 真跑
    head("3 · 真跑 —— payload 自落，skill / aiwf 委托上游")
    proc = forge_run(base, cwd=DEMO)
    emit(proc)
    check("退出码为 0", proc.returncode == 0, f"rc={proc.returncode}")

    # ---------------------------------------------------------- 4 · 验收
    head("4 · 验收")

    say("  ▸ payload —— forge 自己写")
    for rel in PAYLOAD_FILES:
        check(f"落位 · {rel}", (DEMO / rel).is_file())

    say("  ▸ lock —— 只记 payload 项")
    lock_path = DEMO / ".forge" / "lock.json"
    if check("forge 落了 .forge/lock.json", lock_path.is_file()):
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        entries = lock.get("entries", [])
        paths = sorted(str(e.get("path", "")) for e in entries)
        froms = sorted({str(e.get("from", "")) for e in entries})
        check("记的正是 payload 落点", paths == sorted(PAYLOAD_FILES), f"entries={paths}")
        check("来源收敛为 blueprint:<id>", froms == [f"blueprint:{BLUEPRINT}"], f"from={froms}")
        check("不含委托项（账在上游）", not any("skill" in v or "aiwf" in v for v in paths + froms))

    say("  ▸ 受管清单 —— store 的 global 域")
    projects_path = FORGE_HOME / "projects.json"
    if check("登记了 projects.json", projects_path.is_file()):
        data = json.loads(projects_path.read_text(encoding="utf-8"))
        same = [
            p for p in data.get("projects", [])
            if os.path.normcase(str(p.get("path", ""))) == os.path.normcase(str(DEMO))
        ]
        check("demo 在受管清单里", bool(same),
              f"alias={same[0].get('alias') if same else '—'}")

    say("  ▸ 委托 —— 落点归上游，forge 只负责发起")
    if tools["skill"]:
        landed = delegated_skills()
        check(f"skill install {SKILL_NAME} 已落地", bool(landed),
              f"{len(landed)} 处：{landed}" if landed else "没找到 */skills/…")
    else:
        say("  [SKIP] 委托 skill —— `skill` 不在 PATH")

    workflow = DEMO / ".aiwf" / "workflows" / f"{AIWF_WORKFLOW}.json"
    if tools["aiwf"]:
        check(f"aiwf wf install {AIWF_WORKFLOW} 已落地", workflow.is_file(),
              workflow.relative_to(DEMO).as_posix())
    else:
        say("  [SKIP] 委托 aiwf —— `aiwf` 不在 PATH")

    # ---------------------------------------------------------- 5 · 重跑
    head("5 · 重跑同一路径 —— 期望幂等（已受管 + 蓝图一致 → 一个字节都不写）")
    before = (DEMO / "AGENTS.md").read_bytes()
    proc = forge_run(base, cwd=DEMO)
    emit(proc)
    check("rc=0（不再拒绝 —— v1.0 改判）", proc.returncode == 0, f"rc={proc.returncode}")
    check("全项「跳过」", "跳过" in proc.stdout)
    check("已落的文件一个字节没动", (DEMO / "AGENTS.md").read_bytes() == before)

    # ---------------------------------------------------------- 6 · sync
    head("6 · sync —— 增量：蓝图后补的项补落（已受管项目跟上蓝图）")
    (LIB_RUN / BLUEPRINT / "payload" / "CHANGELOG.md").write_text(
        "蓝图后补的一行\n", encoding="utf-8"
    )
    manifest_path = LIB_RUN / BLUEPRINT / "blueprint.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["items"].append({"from": "payload:CHANGELOG.md", "to": "CHANGELOG.md"})
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    say(f"  模拟「蓝图更新」：{BLUEPRINT} 加一项 payload:CHANGELOG.md"
        f"（{len(manifest['items']) - 1} → {len(manifest['items'])} 项）")

    sync_base = ["sync", str(DEMO), "--blueprints", str(LIB_RUN)]
    proc = forge_run([*sync_base, "--dry-run"], cwd=DEMO)
    emit(proc)
    check("sync 预演 rc=0", proc.returncode == 0, f"rc={proc.returncode}")
    check("预演报出将写入 CHANGELOG.md", "CHANGELOG.md" in proc.stdout)
    check("预演一个字节都没写", not (DEMO / "CHANGELOG.md").exists())

    proc = forge_run(sync_base, cwd=DEMO)
    emit(proc)
    check("sync rc=0", proc.returncode == 0, f"rc={proc.returncode}")
    check("补齐了蓝图新增的项", (DEMO / "CHANGELOG.md").is_file())
    lock_now = json.loads((DEMO / ".forge" / "lock.json").read_text(encoding="utf-8"))
    check("新项进 lock",
          any(e.get("path") == "CHANGELOG.md" for e in lock_now.get("entries", [])))
    check("已落过的项目不动（AGENTS.md 仍是最初那份）",
          (DEMO / "AGENTS.md").read_bytes() == before)

    proc = forge_run(sync_base, cwd=DEMO)
    check("sync 幂等（再跑全「跳过」）",
          proc.returncode == 0 and "跳过" in proc.stdout)

    proc = forge_run(["sync", str(RUN / "not-managed")], cwd=DEMO)
    check("未受管路径 sync 被拒，并指向 `forge new`",
          proc.returncode != 0 and "forge new" in (proc.stdout + proc.stderr))

    # -------------------------------------------------------- 产物一览
    head("产物一览 —— 点开头的都是隐藏目录")
    show_artifacts()

    # ---------------------------------------------------------- 结论
    head("结论")
    failed = [label for label, ok, _ in _checks if not ok]
    say(f"  断言通过 {len(_checks) - len(failed)}/{len(_checks)}")
    for label in failed:
        say(f"    FAIL · {label}")
    say(f"  产物留在  {RUN}")
    say(f"  受管清单  {FORGE_HOME / 'projects.json'}")
    say(f"            ↑ 这是**隔离的** FORGE_HOME —— 真 ~/.forge 全程未被碰 ✅")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
