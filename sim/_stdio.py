"""仿真脚本共用的 stdio 引导 —— 把 ``stdout`` / ``stderr`` 钉成 **UTF-8**。

**为什么需要**：Windows 控制台默认 **cp936**，而各 ``drive.py`` 的输出含 **非 GBK 字形**
（``▸`` / ``⏳`` / ``✓`` …）⇒ ``print`` 直接 ``UnicodeEncodeError``、整个仿真中断。
这与 ``forge`` 入口层的 ``cli._configure_stdio()`` 是**同一处理**：那边服务 CLI，
这边服务仿真脚本（仿真不经 CLI 入口，所以拿不到那边的配置）。

用法（各 ``drive.py`` 在 ``HERE = Path(__file__).resolve().parent`` 之后）::

    sys.path.insert(0, str(HERE.parent))
    from _stdio import configure_stdout

    configure_stdout()
"""
import sys


def configure_stdout() -> None:
    """把 ``stdout`` / ``stderr`` 钉成 UTF-8；失败也不抛（最坏退回原行为）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
