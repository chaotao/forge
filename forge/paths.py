"""路径解析 —— 一处定值，别处只读（L1 约定 D：单一事实源）。

依据 ``03-store`` §2：``FORGE_HOME`` 默认 ``~/.forge/``，可覆盖；
沿用 ``aiwf`` 的 ``AIWF_HOME`` 既有做法（实测 ``aiwf/engine/aiwf/storage.py:252``）。
"""
import os

#: 本包所在目录 —— 包内静态资源（``schemas/`` · ``blueprints/``）的根。
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

#: global 域的根。环境变量可覆盖。
FORGE_HOME_ENV = "FORGE_HOME"
DEFAULT_FORGE_HOME = "~/.forge"


def forge_home() -> str:
    """global 域的绝对路径（``<FORGE_HOME>``，默认 ``~/.forge/``）。"""
    raw = os.environ.get(FORGE_HOME_ENV) or DEFAULT_FORGE_HOME
    return os.path.abspath(os.path.expanduser(raw))


def schemas_dir() -> str:
    """``store`` 的 ``kind`` 目录（``03-store`` §6.2 / §6.3）。

    注意：**只收 ``store`` 的 ``kind``** —— 其它模块的模型 schema 与自己的资产放一起
    （``05-scaffold`` §2.5）。
    """
    return os.path.join(PACKAGE_DIR, "schemas")


def blueprints_dir(explicit=None) -> str:
    """蓝图库的根（``05-scaffold`` §2.3）。

    优先级：显式参数 > ``FORGE_BLUEPRINTS_DIR`` > 包内 ``forge/blueprints/``。
    **替换语义，不叠加**（L1 约定 A1）；既例 = aiwf 的 ``AIWF_LIBRARY_DIR`` / ``--library``。
    """
    if explicit:
        return os.path.abspath(explicit)
    env_dir = os.environ.get("FORGE_BLUEPRINTS_DIR")
    if env_dir:
        return os.path.abspath(env_dir)
    return os.path.join(PACKAGE_DIR, "blueprints")
