"""``observer`` —— 外部运行状态的**唯一读口**。设计见 ``docs/design/04-observer.md``。

它是**数据访问层**的**适配器**角色（L2 §2.1 / §2.3②）—— **只读、不存**：

1. 调**外部工具的 CLI**（本轮 = ``aiwf``，**一律 ``--json``**）读**运行状态**；
2. 归一成**读时投影**（``AiwfSection`` / ``AiwfRun``）—— **一个字节都不落盘**、
   不进 ``forge/schemas/``（它不是 ``kind``；L2 §2.3②「什么都不存」· L1 约定 **D**）；
3. 失败时**返回原因**而不是抛错（§6）。

对外**只有一个读面**：``observe(project_root, *, runner=_run_aiwf) -> Observation``。

**模型归属**（L1 约定 **C**）：``observer`` **没有落盘模型** —— 投影的形状（§3）是它与
``workbench`` 的**跨模块契约**（L1 约定 **F**）；改形状 = 改对外契约。

★ **本模块的六条实测硬约束**（§4.4 + 本轮新增第 5/6 条，**均由实跑得出，不是推断**）：

===========  ============================================================================
① 过期副本    ``.aiwf/`` 下的 ``record.json`` 是 run **诞生快照**（``phase: executing``），
              与 CLI 现算（``awaiting_review``）**不一致** ⇒ **绝不直读任何运行时文件**，一律走 CLI
② 一律 JSON   三条命令**全走 ``--json``**（``aiwf`` 侧 2026-10-10 补齐后两条）⇒ 本模块
              **无文本解析、不切列**
③ 空不是错    非 aiwf 项目 / 装了没跑过 ⇒ aiwf **rc=0 静默** ⇒ 照实为零（不报错）
④ 读 CWD      aiwf 的相对 ``.aiwf`` 相对**进程 CWD** ⇒ 必须显式 ``cwd=``，且要把
              「路径不可达」与「真没跑过」**分开**
⑤ AIWF_HOME   ★ **``AIWF_HOME`` 非空时 aiwf 改读那个目录，且静默返回空**（实测：指向空目录
              ⇒ 两条命令都给 ``[]``、rc=0）⇒ 采集**必须清掉 ``AIWF_HOME``**，否则把
              「读不到」谎报成「什么都没有」（L1 **H2**）
⑥ 管道编码    ★ **绝不假设子进程 stdout 的编码** —— Windows 下 aiwf 的**管道 stdout 默认按
              locale（``cp936``）输出**中文；父进程按 ``utf-8`` 严格解码会 ``UnicodeDecodeError``
              （实测：``0xb6`` @ position 47 ⇒ 整条命令读成「无输出」）。两道处理：
              ① 子进程环境**显式设 ``PYTHONIOENCODING=utf-8``**；② 父侧**抓 bytes**，
              按 ``utf-8 → gbk`` **兜底解码**（``_decode``）。
===========  ============================================================================

**不做**（§7）：不写任何东西 · 不做语义判断 · 无 CLI / 无 ``--json`` · **不 import 任何用例模块**
（不调 ``plan`` / ``inbox`` —— L2 §4.2：数据访问层不引用用例）。
"""
import json
import os
import subprocess
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

# ------------------------------------------------------------------ 常量
#: 被观察的外部命令（L1 约定 **F**：外部词表归外部所有）。
AIWF_COMMAND = "aiwf"
#: ★ 必须从子进程环境里清掉的变量 —— 见模块头 ⑤。
AIWF_HOME_ENV = "AIWF_HOME"
#: ★ 传给子进程的 stdio 编码 —— 见模块头 ⑥（治 Windows 管道 stdout 的 locale 编码）。
AIWF_STDOUT_ENCODING_ENV = "PYTHONIOENCODING"
#: 父侧解码的兜底顺序（L1 约定 **G**：确定性 —— 顺序固定，可回归）。
STDOUT_DECODINGS = ("utf-8", "gbk")
#: 子进程超时（秒）—— 防外部工具卡住整张总览（§4.1 · L1 约定 **G**）。
DEFAULT_TIMEOUT = 30

#: aiwf 的词表（**透传，不翻译** —— §3.2 口径 2 · §7-5）。
STATUS_RUNNING = "running"
STAGE_DONE = "done"
STAGE_WAITING = "waiting"

#: 失败原因（§6 表）—— 文案在本模块，渲染在消费方。
ERROR_PATH_UNREACHABLE = "项目路径不可达"
ERROR_AIWF_MISSING = "aiwf 不在 PATH"
ERROR_TIMEOUT = "aiwf 调用超时"

#: 注入用的 runner 签名：``runner(argv, *, cwd) -> 鸭子类型的 CompletedProcess``
#: （需有 ``.stdout`` 属性）。★ 它是本模块**唯一的子进程出口**（§4.6）——
#: 可注入才可确定性回归（L1 约定 **G**）。
Runner = Callable[..., Any]


# ------------------------------------------------------------ 默认子进程出口
def _run_aiwf(argv: Sequence[str], *, cwd: str, timeout: int = DEFAULT_TIMEOUT):
    """跑一次 ``aiwf``，返回 ``CompletedProcess``（**唯一的子进程出口**，§4.6）。

    ★ 三点实现约束（§4.1 / 模块头 ④⑤⑥）：

    ① **显式 ``cwd=``** —— aiwf 的 ``.aiwf`` 是**相对进程 CWD** 的（实测
       ``aiwf/storage.py:241``）；用 ``cwd=`` 而非 ``os.chdir`` —— 后者有**进程级副作用**。
    ② **清 ``AIWF_HOME``** —— 它非空时 aiwf 改读那个目录，且**静默返回空**
       （实测：指向空目录 ⇒ ``runs list --json`` / ``workflows --json`` 都给 ``[]``、rc=0）。
       不清就会把「读不到」谎报成「什么都没有」（L1 **H2**）。
    ③ **强制子进程 UTF-8 + 抓 bytes** —— 见模块头 ⑥：不 ``text=True``，
       子进程环境设 ``PYTHONIOENCODING=utf-8``；文本解码交给父侧的 ``_decode``。
    """
    env = dict(os.environ)
    env.pop(AIWF_HOME_ENV, None)
    env[AIWF_STDOUT_ENCODING_ENV] = "utf-8"  # ★ 模块头 ⑥：不靠 locale（cp936）
    return subprocess.run(
        [AIWF_COMMAND, *argv],
        cwd=cwd,
        env=env,
        capture_output=True,  # ★ 不 text=True：抓 bytes，解码由 _decode 收口（模块头 ⑥）
        timeout=timeout,
        check=False,  # ★ 不以 rc 判成败：``(no runs)`` 是 rc=0 的正常空返回（模块头 ③）
    )


# ------------------------------------------------------------------ 读面
def observe(project_root: str, *, runner: Runner = _run_aiwf) -> Dict[str, Any]:
    """**本模块唯一的读面**（§5）—— 一次给一个项目的**全部外部状态**（L1 约定 **H3**）。

    返回 ``Observation``：一个**按数据源分字段**的容器（L2 §2.5 适配器扩展点）——

    - ``aiwf``：``AiwfSection``（§3.1）—— **本轮唯一字段**；
    - 将来 ``git`` / ``path`` 各加一个字段，**对外调用方式不变**（L1 约定 **A3**）。

    **从不抛到消费方**（§6）—— 失败进 ``aiwf.error``。
    """
    return {"aiwf": _observe_aiwf(os.path.abspath(project_root), runner=runner)}


def _observe_aiwf(project_root: str, *, runner: Runner) -> Dict[str, Any]:
    """``aiwf`` 一路的适配器（§2 扩展点：加数据源 = 加一个这样的内部函数）。"""
    # 步 1：路径可达性预判（§4.5）—— 不可达就**不调 aiwf**（有确定性信号，不靠它报错）
    if not os.path.isdir(project_root):
        return _failed(ERROR_PATH_UNREACHABLE)

    # ① 已装工作流数 —— ★ 它的**独立价值**：``workflows=0`` = 没接流程；
    #    ``workflows>0`` 且 ``runs_total=0`` = 装了流程没跑（两态只能靠它区分，§3.1）。
    workflows, error = _call_json(
        runner, ["workflows", "--json"], project_root, label="workflows --json"
    )
    if error is not None:
        return _failed(error)
    if not isinstance(workflows, list):
        return _failed("aiwf workflows --json 输出不是数组")

    # ② 顶层 run 列表 —— aiwf 侧**已按 ``started_at`` 降序**排好（``sorted(rows, reverse=True)``）
    #    ⇒ 本模块**不重排**（§7-7）：``running`` 保原序，``last`` 取**首个非 running**即「最近一次」。
    runs, error = _call_json(
        runner, ["runs", "list", "--json"], project_root, label="runs list --json"
    )
    if error is not None:
        return _failed(error)
    if not isinstance(runs, list):
        return _failed("aiwf runs list --json 输出不是数组")

    records = [_base_run(row) for row in runs]
    running = [run for run in records if run["status"] == STATUS_RUNNING]
    # 「已结束」用**排除法**（``!= running``）而非枚举 ``completed``/``failed`` ——
    # aiwf 是词表所有者（L1 **F**），将来加终态（如 cancelled）时排除法不会漏。
    last = next((run for run in records if run["status"] != STATUS_RUNNING), None)

    # ③ ★ **只对 ``running`` 追加 ``--view``**（§4.2）—— ``last`` **不取**：
    #    它的消费者只要「上次那轮结束了 / 失败了」这个事实；``current`` / ``wait``
    #    对已结束的 run 本就无意义；而 ``--view`` 是**逐 run 一次子进程**（L1 约定 **A1**）。
    for run in running:
        view, error = _call_json(
            runner,
            ["runs", "show", "--view", "--json", run["run_id"]],
            project_root,
            label="runs show %s --view --json" % run["run_id"],
        )
        if error is not None:
            return _failed(error)
        run.update(_view_fields(view))

    return {
        "workflows": len(workflows),
        "runs_total": len(runs),
        "running": running,
        "last": last,
        "error": None,
    }


# ------------------------------------------------------------------ 内部
def _decode(raw: Any) -> str:
    """把子进程 stdout 解成 ``str``（模块头 ⑥）—— **绝不抛**。

    - ``bytes``：按 ``STDOUT_DECODINGS``（``utf-8 → gbk``）依次试；全失败则
      ``utf-8`` + ``errors="replace"`` 兜底（最坏情况由 ``json.loads`` 报「无法解析」，
      **不在这里崩** —— L1 约定 **G**）。
    - ``str``：原样返回（注入的 ``runner`` 桩可喂文本）。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    for encoding in STDOUT_DECODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _call_json(
    runner: Runner, argv: Sequence[str], cwd: str, *, label: str
) -> Tuple[Optional[Any], Optional[str]]:
    """调一次 aiwf 并解析 JSON：成功 → ``(值, None)``；失败 → ``(None, 原因)``。

    **异常顺序有意义**：``FileNotFoundError`` / ``NotADirectoryError`` 都是 ``OSError``
    子类，必须排在 ``except OSError`` 之前（§4.5 步 2 的判别器）。
    """
    try:
        proc = runner(argv, cwd=cwd)
    except FileNotFoundError:
        return None, ERROR_AIWF_MISSING  # [WinError 2]
    except subprocess.TimeoutExpired:
        return None, ERROR_TIMEOUT
    except NotADirectoryError:
        return None, ERROR_PATH_UNREACHABLE  # [WinError 267]（isdir 已拦，兜底）
    except OSError as exc:
        return None, "aiwf 调用失败：%s" % exc

    text = _decode(getattr(proc, "stdout", None)).strip()
    if not text:
        return None, "aiwf %s 无输出" % label
    try:
        return json.loads(text), None
    except ValueError:
        return None, "aiwf %s 输出无法解析" % label  # ★ 不猜（L1 **H2**）


def _base_run(row: Any) -> Dict[str, Any]:
    """``runs list --json`` 的一行 → ``AiwfRun`` 的 5 个基础字段（§3.2）。

    ★ ``title`` **也在 ``runs list`` 里**（2026-10-11 起）—— aiwf 侧 ``run_title()`` 解析好、
    **永不为空**（``view.dynamic_title`` → 起跑时的 ``record.title`` → ``run_id``）⇒ 它**每条
    run 都有**（``running`` 与 ``last`` 一样），旧 run 自动落到 ``run_id``。**这就是它
    不必为 ``last`` 空跑 ``--view`` 的原因**（与下面的 4 项不同）。

    ``--view`` 那 4 项**缺省 ``None``** —— 只有 ``running`` 的 run 会被补上。
    外部数据**不校验**（§7-10），但**不崩**：形状不符时给 ``None``。
    """
    if not isinstance(row, dict):
        row = {}
    return {
        "run_id": row.get("run_id"),
        "workflow_id": row.get("workflow_id"),
        "title": row.get("title"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        # —— 以下 4 项来自 ``runs show --view --json``（仅 running 取，§4.2）——
        "stages_done": None,
        "stages_total": None,
        "current": None,
        "wait": None,
    }


def _view_fields(view: Any) -> Dict[str, Any]:
    """``runs show --view --json`` → 4 个派生字段（§3.2）—— **只取值，不解释**（§7-5/6）。

    - ``stages_done``：``status == "done"`` 的阶段数；
    - ``stages_total``：阶段总数；
    - ``current``：``status == "waiting"`` 的那个阶段名（多个取**第一个**，确定性）；
    - ``wait``：★ 该阶段 ``action.kind`` 的**透传**（如 ``review``）——「在等谁」，
      **不拼命令、不翻译**（§8-13/14）。

    ★ ``title`` **不在这里** —— 它来自 ``runs list --json``（见 ``_base_run``）：aiwf 的
    ``--view`` 也带 ``title``，但本模块**不取**（同一事实只从一处取，L1 约定 **D**）。
    """
    if not isinstance(view, dict):
        view = {}
    stages = view.get("stages")
    if not isinstance(stages, list):
        stages = []

    done = 0
    waiting: Optional[Dict[str, Any]] = None
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        status = stage.get("status")
        if status == STAGE_DONE:
            done += 1
        elif status == STAGE_WAITING and waiting is None:
            waiting = stage

    action = waiting.get("action") if waiting else None
    if not isinstance(action, dict):
        action = {}
    return {
        "stages_done": done,
        "stages_total": len(stages),
        "current": waiting.get("name") if waiting else None,
        "wait": action.get("kind"),
    }


def _failed(reason: str) -> Dict[str, Any]:
    """**段级降级**（§6）—— ``error`` 给原因，**数据字段一律 ``null``**（**不取 ``0``**）。

    ★ ``running`` 例外给 ``[]``：它的类型是 ``[AiwfRun]``（不可空，§3.1），且 §3.1 口径 3
    规定消费方在 ``error`` 非空时**只看 ``error``**。

    **为什么整段降级而不是给半份**：半份数据比没有更误导 —— 若 ``workflows`` 读到了、
    ``runs list`` 没读到，报 ``workflows=3 / runs_total=0`` 会被读成「装了 3 个流程、
    一个都没跑」，而真相是「根本没读到」（L1 **H2**）。
    """
    return {
        "workflows": None,
        "runs_total": None,
        "running": [],
        "last": None,
        "error": reason,
    }
