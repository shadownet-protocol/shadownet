from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadownet_conformance.errors import ConformanceError

if TYPE_CHECKING:
    from pathlib import Path


class EmitterError(ConformanceError):
    """Raised when an emitter subprocess fails."""


@dataclass(frozen=True, slots=True)
class CrossCheckResult:
    bytes_: bytes
    py_bytes: bytes
    go_bytes: bytes
    matched: bool


def cross_check_emit(kind: str, spec: dict[str, object], go_emit_path: Path) -> CrossCheckResult:
    """Pipe `spec` to both emitters as JSON; return their bytes + match status."""
    spec_json = json.dumps(spec).encode()
    py_bytes = _run_py_emitter(kind, spec_json)
    go_bytes = _run_go_emitter(kind, spec_json, go_emit_path)
    matched = py_bytes == go_bytes
    return CrossCheckResult(
        bytes_=py_bytes if matched else b"",
        py_bytes=py_bytes,
        go_bytes=go_bytes,
        matched=matched,
    )


def _run_py_emitter(kind: str, spec_json: bytes) -> bytes:
    cmd = [sys.executable, "-m", "shadownet_conformance.regen.py_emit", kind]
    return _run(cmd, spec_json, "py_emit")


def _run_go_emitter(kind: str, spec_json: bytes, go_emit_path: Path) -> bytes:
    if not go_emit_path.is_file():
        raise EmitterError(
            f"go-emit binary not found at {go_emit_path}. "
            "Build it with: cd fixtures/_regen/go-emit && go build -o go-emit ."
        )
    return _run([str(go_emit_path), kind], spec_json, "go-emit")


def _run(cmd: list[str], stdin: bytes, label: str) -> bytes:
    try:
        proc = subprocess.run(  # noqa: S603 — cmd is internally constructed
            cmd,
            input=stdin,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise EmitterError(f"{label}: command not found: {cmd[0]}") from exc
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").rstrip()
        raise EmitterError(f"{label} (exit {proc.returncode}): {stderr}")
    return proc.stdout


def find_go_emit_binary(repo_root: Path) -> Path:
    candidate = repo_root / "fixtures" / "_regen" / "go-emit" / "go-emit"
    if candidate.is_file():
        return candidate
    raise EmitterError(
        f"go-emit binary not found at {candidate}. "
        "Run: cd fixtures/_regen/go-emit && go build -o go-emit ."
    )


def ensure_go_toolchain() -> str:
    path = shutil.which("go")
    if not path:
        raise EmitterError("Go toolchain not found on PATH; install Go 1.25+.")
    return path
