# sim —— forge 的仿真

把手动、**真实**、端到端地跑一遍 forge，看它在真环境里到底干了什么。

**与 `tests/` 的分工**

| | 上游 CLI | 怎么跑 | 用途 |
| --- | --- | --- | --- |
| `tests/` | 用**桩**替掉 | `pytest`，可自动回归 | 引擎的确定性行为（L1 约定 G） |
| `sim/` | **真调** | 手动 | 端到端看结果、给人验收 |

`sim/` 不进 pytest —— 它依赖机器上真实的 `skill` / `aiwf`，在别的机器上会合理地失败。

## 场景

| 场景 | 验什么 |
| --- | --- |
| [`new-demo`](new-demo/) | `forge new` 的**三类落位**：`payload:` 自落 · `skill:` 委托 skill-library · `aiwf:` 委托 aiwf |
| [`plan-demo`](plan-demo/) | `forge plan` 的**完整流程**：`forge new` 起项目 → 建块 / 建任务 → 「打回两次后完成」→ 三个读视图 → 块级 `done`/`drop` → 错路 → 落盘核验（`plans.json` 集合 + `plan_log.jsonl` 轨迹 + note 零重复） |
| [`inbox-demo`](inbox-demo/) | `forge inbox` 的**完整流程**：`forge new` 起项目 → 捕获 **6 条零散念头** → `list` / `list --full` / `show` → **整理 · 合并**（三组，含**链式** —— 产物被再合）→ `hand` / `drop` 交接 → 活页只剩 **1 条够格的主题** → 错路 → 落盘核验（`inbox.json`：id **不复用** · 源 `target` 指向产物 · **open 无 `target`** · **无 `mergedInto`/`group`** —— 合并零模型改动） |
| [`workbench-demo`](workbench-demo/) | `forge workbench` / `forge status` 的**两个读视图**：起**两个**真项目 → 灌计划 / 念头 → 全局**一项目一段**（身份行 · `plan` 恒 1 行 + `在做`/`卡住` · `inbox` · `aiwf`）· 本项目 `status`（**去身份行**，与全局那段**同形**）· 未受管目录 → 「（未受管）」· `--json` 一致 · 不受 CWD 影响 · 真环境**只读**快照 |

## 跑法

```bash
python sim/new-demo/drive.py      # forge new —— 三类落位
python sim/plan-demo/drive.py     # forge plan —— 计划流程
python sim/inbox-demo/drive.py    # forge inbox —— 念头流程（捕获 → 整理 → 交接）
python sim/workbench-demo/drive.py  # forge workbench / status —— 跨项目总览 + 本项目一眼状态
```

脚本会打印每一步的真实输出与逐条断言，最后给 `PASS/FAIL` 汇总；
退出码 `0` = 全通过。

> `plan-demo` 与 `inbox-demo` 都用内置的 **`minimal`** 蓝图（items 全是 `payload:`，**零委托**）——
> 所以它们只依赖 `forge` 一个命令，不依赖 `skill` / `aiwf`。

## 产物落在哪

每个场景自己管自己的产物，都在 `_run/` 下（已 gitignore，可随时删）：

```
sim/new-demo/_run/
├── home/         隔离的 FORGE_HOME —— 受管清单 projects.json 在这
└── demo/         被 forge 新建出来的那个项目

sim/plan-demo/_run/
├── home/         隔离的 FORGE_HOME
└── demo/         真项目；计划落在 demo/.forge/{plans.json, plan_log.jsonl}

sim/inbox-demo/_run/
├── home/         隔离的 FORGE_HOME
└── demo/         真项目；念头账落在 demo/.forge/inbox.json
sim/workbench-demo/_run/
├── home/         隔离的 FORGE_HOME
├── alpha/        真项目（有计划 + 念头；`.forge/` 三份账）
├── beta/         真项目（空 —— 演「未记账」）
└── gamma/        **不登记**的目录 —— 演「（未受管）」
```

**隔离是刻意的**：真 `~/.forge` 全程不会被碰。

> ⚠ 三个 `drive.py` 里的 `forge_run()` 每次都**硬注入** `FORGE_HOME`——
> 光在外部 `unset FORGE_HOME` **不生效**。想拿真环境试，直接在别的目录手动跑一次
> `forge new <路径>`（那才会写真的 `~/.forge/projects.json`）。
