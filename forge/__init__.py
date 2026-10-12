"""forge —— 面向 AI 协作项目的一站式工具。

设计分四层，见 ``docs/design/``：

- L0 定位与范围   ``00-positioning.md``
- L1 设计原则     ``01-principles.md``
- L2 概要架构     ``02-architecture.md``（数据访问层 ``store`` / ``observer`` + 用例 4 个）
- L3 模块设计     ``03-store.md`` · ``05-scaffold.md`` …

本包当前实现 **L3 首阶段范围（A「能建」）**：``store`` + ``scaffold`` + CLI 入口，
目标是 ``forge new <路径>`` 跑通 —— 蓝图落位 + 登记受管。

此后**阶段推进**：``plan`` 用例已落地（``forge plan …``，见 ``06-plan.md``）——
为此 ``store`` 补上了 ``log`` 形态（``.jsonl`` 追加），用例接口对形态**无感**。

再后：``scaffold`` 补上依赖管理入口 ``forge sync``（``05-scaffold`` §7 T5）——
增量与 ``new`` 走**同一条** ``preview`` / ``apply`` / 报账链路，故幂等。
"""

__version__ = "0.1.0"
