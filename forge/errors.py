"""forge 的错误类型。

入口层按类型渲染文案（L1 约定 B：文案不出模块）。
"""


class ForgeError(Exception):
    """forge 的基类错误 —— 可预期的失败，入口层捕获后打印为一行消息。"""


class BlueprintError(ForgeError):
    """蓝图缺失 / 结构非法 / 引用不成立（``05-scaffold`` §2.5）。"""


class StoreError(ForgeError):
    """``kind`` 未登记 / 数据不合 schema / 形态未实现（``03-store`` §5 / §6）。"""


class DelegateError(ForgeError):
    """委托上游安装失败：命令缺失、上游非零退出（``05-scaffold`` §4.2）。"""


class PlanError(ForgeError):
    """计划非法：地址不成立 / 计划或任务不存在 / 重名 / 词表越界 / 依赖不成环（``06-plan`` §3 / §6）。"""


class InboxError(ForgeError):
    """念头非法：条目不存在 / id 不成立 / 交接缺去向 / 合并的源不合规（``07-inbox`` §3 / §6）。"""
