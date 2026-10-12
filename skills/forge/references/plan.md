# plan —— 项目的计划

把**一个项目里的若干份计划**记在项目里。一份计划 = 一个模块 / 一个 demo / 一个交付物，
每份计划有**目标 · 判据 · 状态**，以及它下面的**任务**和贯穿始终的**过程记录**。

> 命令要**在项目目录里**跑 —— 项目根 = 当前工作目录（`<项目>/.forge/` 就是它的数据）。

## 始终遵守（三条，最重要）

| # | 纪律 | 为什么 |
| --- | ------- | ------------------------------------------ |
| 1 | **不直读 `.forge/plans.json` / `.forge/plan_log.jsonl`** —— 一律走 `forge plan show` / `forge plan log` | 那是引擎的**存储**。直读会拿到全量原文（烧 token）、且绕过视图的过滤与默认体量。 |
| 2 | **写动作用语义动词** —— 认领 `claim` · 卡住 `block` · 打回 `reject` · 完成 `done` · 放弃 `drop`。**先找同名动词，找不到才 `set`** | 轨迹按**动词名**记 `op`。图省事全用 `set --status`，轨迹里就只剩 `op:"set"` —— **"打回 / 完成 / 放弃"的区分全丢了**（而"这任务反复被打回"恰恰是要留的痕）。 |
| 3 | **地址带计划** —— 指一个任务必须写全 `<plan>.<task>`（`login.T3`） | 计划标识项目内唯一、任务标识**只在计划内唯一**（跨计划 `T3` 可以重名）。**没有隐式解析**：`plan done T3` 引擎直接拒（退出码 1）。 |

## 命令

**写 —— 一个意图一个动词**：

| 你要做的事 | 命令 |
| --- | --- |
| 新建**一份计划** | `forge plan add <计划> --title "登录模块" --goal "让用户能安全登录 / 注册 / 找回密码" --criteria "注册后能收到验证邮件"` |
| 新建**一个任务** | `forge plan add login.T3 --title "实现登录页" [--done-when "能从登录页登录成功"]` |
| **认领** | `forge plan claim login.T3` |
| **完成**（任务） | `forge plan done login.T3 [--note "…"]` |
| **完成**（整计划） | `forge plan done login` |
| **打回** | `forge plan reject login.T3 --note "密码强度校验缺失，未过"` |
| **卡住 / 要人介入** | `forge plan block login.T3 --note "…"` |
| **放弃一份计划** | `forge plan drop login --note "需求砍了"` |
| 其余改动（只改 note / 改 title·goal / 转 owner） | `forge plan set <地址> [--status][--owner][--note][--goal][--title]` |

**强制项**（不给自己留含糊）：建计划必给 `--title` + `--goal` + **至少一条 `--criteria`**；建任务必给 `--title`；
`reject` / `block` / `drop` **必给 `--note`**（不给原因 = 这条轨迹没意义）。

建任务用 `--deps` 声明前置：`--deps login.T1`（**全地址**）或 `--deps T1`（**计划内 id**）**都行** —— 只能指**同一份计划内**的任务。

`done` **对两级通用** —— 任务 → `done`，计划 → `reached`（"这实体成了"只有一种表达）。
计划的状态三态：`open` / `reached` / `dropped`；任务四态：`todo` / `doing` / `done` / `blocked`。

**读 —— 4 个视图**（都不吐原始文件；默认结构化文本，加 `--json` 拿机器可读）：

| 视图 | 给什么 | 默认体量 |
| --- | --- | --- |
| `forge plan show` | **全局列表**：每份计划 `id` / `title` / `状态` / 任务完成度 | 全量 |
| `forge plan show login` | **单份计划**：`title` / `goal` / `criteria` + 任务列表 | 全量 |
| `forge plan show login.T3` | **单任务交接包**：状态 + **完整轨迹**（可 `--tail N`） | 全量 |
| `forge plan log` | **跨计划时间线**（最近发生了什么），可 `--plan <计划>` | **尾部 20 条** |

**轨迹只跟随「单任务」或「显式 `log`」**；列表与单份计划视图**不带轨迹** —— 那是为 token 设的闸。

## 完整流程（新会话接手一个项目）

主人说一句"继续推进"，你按这个顺序走：

1. `forge plan show` —— 看**有几份计划、各到哪了**，挑一份计划。
2. `forge plan show <计划>` —— 看这份计划的**目标 / 判据 / 任务列表**，挑一个任务。
3. `forge plan show <计划>.<任务>` —— **交接包**：现在什么状态 + 怎么走到今天的（轨迹末条 note = 当前上下文）。
4. `forge plan claim <计划>.<任务>` → 干活 → `forge plan done <计划>.<任务> --note "…"`。

验收方在你之后：
- 没过 → `forge plan reject <计划>.<任务> --note "<为什么没过>"`（**必带原因**）；
- 遇阻要人拍板 → `forge plan block <计划>.<任务> --note "<卡在哪>"`。

**你从头到尾不用"另外写日志"** —— 轨迹是这些动作的**副产品**，自己长出来。

## 一个真实场景走一遍

任务 `login.T3`（实现登录页），被**打回两次**后完成：

```bash
$ forge plan claim login.T3
claim login.T3  todo → doing

$ forge plan reject login.T3 --note "密码强度校验缺失，未过"
reject login.T3  doing → doing  · 密码强度校验缺失，未过

$ forge plan reject login.T3 --note "强度校验补了，但错误文案是英文"
reject login.T3  doing → doing  · 强度校验补了，但错误文案是英文

$ forge plan done login.T3 --note "补了中文文案，通过"
done login.T3  doing → done  · 补了中文文案，通过
```

回头看这一个任务：

```
$ forge plan show login.T3
login.T3 · 实现登录页 · done · owner=agent
  done_when  能从登录页登录成功
  轨迹 4 条
    2026-10-09T13:52:36+08:00  → todo
    2026-10-09T13:52:37+08:00  todo → doing
    2026-10-09T13:52:37+08:00  doing → doing  密码强度校验缺失，未过
    2026-10-09T13:52:38+08:00  doing → doing  强度校验补了，但错误文案是英文
    2026-10-09T13:52:38+08:00  doing → done   补了中文文案，通过
```

> 注意中间两行 `doing → doing`：**状态没变，但打回这件事留了痕** —— 因为 `op` 记的是你用的动词（`reject`），不是状态差。

看全局：

```
$ forge plan show
计划 1 个
  login  登录模块  open     0/1
```

## 落在哪

| 产物 | 内容 | 谁写 |
| --- | --- | --- |
| `<项目>/.forge/plans.json` | **当前状态**：每份计划的 goal / criteria / status + 任务（快照，每次覆盖） | **只有 `forge plan …`** |
| `<项目>/.forge/plan_log.jsonl` | **过程轨迹**：一行一个事件，只增不改 | 同上，自动追行 |

两个文件都在**版本控制里**（可 diff、有历史）—— 所以**永远不要手改它们**：
手改 = 绕过引擎校验，且让下一次 `forge plan` 写的和你看的对不上。要改，用命令。

## 边界（别指望它做更多）

- **它不判"完成"** —— "这任务算不算做完""这份计划成了没"是**你（agent）的判断**，写进去即落盘。forge 只存、不猜。
- **它不管"项目"本身** —— 项目清单（受管项目 / 阶段）在 `~/.forge/projects.json`，是 `scaffold` 的地盘。
- **它不连工作流** —— 任务与 `aiwf` run **没有外键**，最多在文字里引一句。
- **它不落"计划书"** —— 要看，就 `show` / `log` 渲染视图；没有一份 `PLAN.md` 给你读。

---

要**解释行为**或**改设计**时读：`F:\data\project\github\forge\docs\design\06-plan.md`。
