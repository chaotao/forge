# demo

由 `forge new` 从蓝图 `demo` 落成的项目骨架。

## 这个目录里有什么

| 路径 | 谁落的 |
| --- | --- |
| `README.md` · `AGENTS.md` | **forge 自己写**（蓝图里的 `payload:` 项） |
| `.forge/lock.json` | forge 的落位账本 —— **只记它自己落的那些** |
| `.<agent>/skills/forge/` | **委托 `skill install forge`** 落的，落点由 skill-library 自己定 |
| `.aiwf/workflows/std.review-check.json` | **委托 `aiwf wf install`** 落的 |

## 下一步

这颗骨架是**静态**的 —— forge 不做变量渲染、不做代码生成。
需要因地制宜的部分，自己改。
