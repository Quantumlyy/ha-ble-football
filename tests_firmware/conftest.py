"""Fixtures for the firmware tests.

These tests need a C++ compiler, and the host-platform test also needs the
esphome command. Anything missing skips the tests that depend on it.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from .reference import COMMON

HERE = Path(__file__).resolve().parent


def _find_compiler() -> str | None:
    for name in (os.environ.get("CXX"), "c++", "g++", "clang++"):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


@pytest.fixture(scope="session")
def harness(tmp_path_factory) -> Path:
    """The footfall.h command-line harness, compiled once per test run."""
    compiler = _find_compiler()
    if compiler is None:
        pytest.skip("no C++ compiler found (set CXX)")
    out = tmp_path_factory.mktemp("harness") / "harness"
    base = [
        compiler,
        "-std=c++17",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-O1",
        "-g",
        f"-I{COMMON}",
        str(HERE / "harness.cpp"),
        "-o",
        str(out),
    ]
    # Prefer a build with the address and undefined-behaviour sanitizers, so a
    # bad pointer in the buffer code fails the test instead of passing by luck.
    sanitized = subprocess.run(
        [*base, "-fsanitize=address,undefined", "-fno-sanitize-recover=all"],
        capture_output=True,
        text=True,
    )
    if sanitized.returncode != 0:
        plain = subprocess.run(base, capture_output=True, text=True)
        assert plain.returncode == 0, plain.stderr
    return out


@pytest.fixture
def footfall(harness):
    """Run a list of harness commands in a fresh process; returns the answers."""

    def run(commands: list[str]) -> list[str]:
        result = subprocess.run(
            [str(harness)],
            input="\n".join(commands) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    return run


@pytest.fixture(scope="session")
def esphome_bin() -> str:
    candidates = [
        os.environ.get("ESPHOME_BIN"),
        shutil.which("esphome"),
        str(Path(sys.executable).parent / "esphome"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    pytest.skip("esphome command not found (pip install esphome, or set ESPHOME_BIN)")
