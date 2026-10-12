# Godot 游戏项目

由 `forge new --blueprint godot` 初始化 —— 请把它改成这个游戏真正的一句话。

## 装了什么

- **Godot 工程**：`project.godot` —— 已启用 **Godot AI** 插件（含它的游戏侧 autoload）。
- **Godot AI 插件**：`addons/godot_ai/` —— 经 MCP 把 AI 接进 Godot 编辑器
  （读场景树 / 建节点 / 改属性 / 跑测试 / 搜工程文件）。
  插件**随项目入库**（MIT，许可证见 `addons/godot_ai/LICENSE`），换机器克隆下来就能用。

## 从这里开始

**① 用 Godot 打开** —— Godot **4.5+**（4.7 更佳）打开本目录的 `project.godot`。

**② 把插件接上 MCP** —— Project > Project Settings > Plugins 勾选 **Godot AI**；
在弹出的 Godot AI 面板里选你的 MCP 客户端，按 **Configure**。插件会自己起 MCP 服务（走 WebSocket）。
> 需要本机有 [`uv`](https://docs.astral.sh/uv/)（插件用它拉起 Python 服务）。

**③ 定创作方向** —— 玩法、目标平台、范围（含什么 / 不含什么），写进 `design/`。**这一步由人拍板。**

**④ 开工** —— 让 agent 按 `AGENTS.md` 的约定推进；进度用 `forge plan …` 记账。

## 目录（建议，可调）

| 位置 | 说明 |
| --- | --- |
| `scenes/` | 场景（`.tscn`） |
| `scripts/` | GDScript（建议再分 `data/` 数据 · `actors/` 表现 · …） |
| `shaders/` | 着色器（`.gdshader`） |
| `assets/` | 素材（图 / 字体 / 音频） |
| `design/` · `spec/` | 玩法设计 · 技术约定 |
| `tests/` · `tools/` | 无头测试 · 生成脚本 |
| `addons/` | Godot 插件（当前只有 `godot_ai/`） |
| `.forge/` | 项目状态目录（由 forge 维护；`lock.json` 不要手改） |

> 目录名与划分**没有强制规定** —— 只要做到「同一份内容只在一个地方是权威」。
