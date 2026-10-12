"""共用夹具。

两条纪律（对应 L1 约定 **G**：确定性行为必须有自动化验证）：

- 每个测试跑在**临时 ``FORGE_HOME``** 下，绝不碰真实的 ``~/.forge/``；
- 上游 CLI 一律用**桩**（记录 argv / CWD / 环境），不真调 ``skill`` / ``aiwf``。
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """把 global 域钉在 tmp 里（``FORGE_HOME``）。"""
    home = tmp_path / "forge-home"
    monkeypatch.setenv("FORGE_HOME", str(home))
    monkeypatch.delenv("FORGE_BLUEPRINTS_DIR", raising=False)
    return home


@pytest.fixture(autouse=True)
def reset_kind_cache():
    """每个测试前后清掉 ``store`` 的 ``kind`` 表缓存 —— 有测试会临时换 ``schemas/`` 目录。"""
    from forge import store

    store.reset_cache()
    yield
    store.reset_cache()


def write_stub(directory, name, log, exit_code=0):
    """造一个上游 CLI 桩：把 ``CWD`` / ``ARGV`` / ``AIWF_HOME`` 追加进 ``log``。

    Windows 用 ``.cmd``（``CreateProcess`` 能直接跑），其它平台用带执行位的 shell 脚本。
    """
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        path = directory / f"{name}.cmd"
        text = (
            "@echo off\r\n"
            f'>>"{log}" echo CWD=%CD%\r\n'
            f'>>"{log}" echo ARGV=%*\r\n'
            f'>>"{log}" echo AIWF_HOME=[%AIWF_HOME%]\r\n'
            f"exit /b {exit_code}\r\n"
        )
    else:
        path = directory / name
        text = (
            "#!/bin/sh\n"
            f'echo "CWD=$PWD" >> "{log}"\n'
            f'echo "ARGV=$*" >> "{log}"\n'
            f'echo "AIWF_HOME=[${{AIWF_HOME}}]" >> "{log}"\n'
            f"exit {exit_code}\n"
        )
    path.write_text(text, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o755)
    return path


@pytest.fixture
def stub_path(tmp_path, monkeypatch):
    """把桩目录放到 ``PATH`` 最前；返回 ``(目录, 记录日志)``。"""
    directory = tmp_path / "stub-bin"
    log = tmp_path / "stub.log"

    def install(name, exit_code=0):
        return write_stub(directory, name, log, exit_code=exit_code)

    monkeypatch.setenv(
        "PATH", str(directory) + os.pathsep + os.environ.get("PATH", "")
    )
    return install, log


def read_log(log):
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def library(tmp_path):
    """造一个临时蓝图库：一个 payload 文件 + 一棵 payload 目录树 + 两个委托项。"""
    base = tmp_path / "blueprints"
    blueprint = base / "demo"
    payload = blueprint / "payload"
    (payload / "tree" / "sub").mkdir(parents=True)
    (payload / "AGENTS.md").write_text("hi\n", encoding="utf-8")
    (payload / "tree" / "a.txt").write_text("a\n", encoding="utf-8")
    (payload / "tree" / "sub" / "b.txt").write_text("b\n", encoding="utf-8")
    manifest = {
        "version": 1,
        "id": "demo",
        "description": "测试用蓝图",
        "items": [
            {"from": "payload:AGENTS.md", "to": "AGENTS.md"},
            {"from": "payload:tree", "to": "docs"},
            {"from": "skill:comfy-run"},
            {"from": "aiwf:std.demo"},
        ],
    }
    (blueprint / "blueprint.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return base
