# 小说项目

由 `forge new --blueprint novel` 初始化 —— 请把它改成这部小说真正的一句话。

## 从这里开始

**① 写好创作方向** —— 建 `novel/inputs.json`（工作流的入参）：

```json
{
  "project_dir": "<本项目的绝对路径>",
  "genre": "科幻",
  "theme": "人在绝境中的道德选择",
  "target_words": "20万",
  "inspiration": "无"
}
```

**② 启动创作流程**：

```bash
aiwf run std.novel-writing --input-file novel/inputs.json --json
```

**③ 按每次返回的任务包推进**，关键节点由你验收（`review` 的结论只能由人给）。

## 目录

| 位置 | 说明 |
| --- | --- |
| `novel/` | 创作产物（构思 / 世界观 / 角色 / 大纲 / 章节…），由工作流写入 |
| `.aiwf/` | 工作流定义与运行记录（由 aiwf 维护；`runs/` 进版本库） |
| `.forge/` | 项目状态目录（由 forge 维护；`lock.json` 不要手改） |
| `AGENTS.md` | 人与 agent 共用的约定入口 |
