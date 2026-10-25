"""Calls dbt (``dbt run-operation``) and extracts the ``PARITY|`` lines from its output.

No shell is ever involved: the command is a list handed to ``subprocess.run`` (``shell=False``), so the JSON
passed through ``--args`` is a single argument on every OS (Python quotes it for Windows itself).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_LINES = re.compile(r"\r\n|\n|\r")
_NOTE = re.compile(r"NOTE\|([^|]+)\|excluded=(.*)$")

RECONCILE_MACRO = "data_parity_reconcile"
CONFORMANCE_MACRO = "data_parity_conformance"


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def split_lines(text: str) -> List[str]:
    return _LINES.split(text)


def extract_parity(text: str) -> List[str]:
    """Lines that contain ``PARITY|``, with everything up to and including that marker removed.

    Tolerates ANSI colour codes, dbt timestamps / log prefixes and CRLF line ends.
    """
    out: List[str] = []
    for line in split_lines(strip_ansi(text)):
        _, sep, rest = line.partition("PARITY|")
        if sep:
            out.append(rest)
    return out


def extract_excluded(text: str) -> Dict[str, str]:
    """``NOTE|<model>|excluded=<columns>`` informational lines -> {model: columns}."""
    out: Dict[str, str] = {}
    for line in split_lines(strip_ansi(text)):
        idx = line.find("NOTE|")
        if idx == -1:
            continue
        m = _NOTE.search(line[idx:])
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def resolve_binary(name: str) -> Optional[str]:
    """Absolute path of a dbt binary (shutil.which handles dbt.exe / dbt.cmd on Windows), or None."""
    found = shutil.which(name)
    if found:
        return found
    p = Path(name)
    if p.is_file():
        return str(p.resolve())
    return None


@dataclass
class Side:
    key: str  # "A" or "B"
    label: str
    target: str
    binary: str = "dbt"
    project: Path = field(default_factory=Path.cwd)
    profiles_dir: Optional[Path] = None


@dataclass
class CallResult:
    side: str
    returncode: int
    lines: List[str]
    excluded: Dict[str, str]
    raw: str

    @property
    def failed(self) -> bool:
        return self.returncode != 0 or not self.lines

    def reason(self) -> str:
        if self.returncode != 0:
            return "dbt exited with code %s" % self.returncode
        return "dbt produced no PARITY output (is the data_parity package installed? run `dbt deps`)"

    def tail(self, n: int = 15) -> List[str]:
        lines = [x for x in split_lines(strip_ansi(self.raw)) if x.strip()]
        return lines[-n:]


def build_command(side: Side, binary: str, macro: str, args: Optional[Dict[str, Any]]) -> List[str]:
    cmd = [binary, "run-operation", macro, "--target", side.target]
    if side.profiles_dir is not None:
        cmd += ["--profiles-dir", str(Path(side.profiles_dir).resolve())]
    if args is not None:
        # ONE list element holding JSON (a YAML subset): robust for quotes, no shell, same on every OS
        cmd += ["--args", json.dumps(args, ensure_ascii=False, separators=(", ", ": "))]
    return cmd


def run_macro(side: Side, macro: str, args: Optional[Dict[str, Any]] = None) -> CallResult:
    """Run one macro on one side. Never raises: failures are reported in the result."""
    binary = resolve_binary(side.binary)
    if binary is None:
        return CallResult(side.key, 127, [], {}, "dbt binary not found: %s" % side.binary)
    env = dict(os.environ)
    env.setdefault("DBT_USE_COLORS", "0")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            build_command(side, binary, macro, args),
            cwd=str(side.project),  # DuckDB paths in profiles are relative to this directory
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            shell=False,
        )
    except OSError as exc:
        return CallResult(side.key, 126, [], {}, "cannot run %s: %s" % (binary, exc))
    text = proc.stdout.decode("utf-8", errors="replace")
    return CallResult(side.key, proc.returncode, extract_parity(text), extract_excluded(text), text)


def run_reconcile(side: Side, args: Dict[str, Any]) -> CallResult:
    return run_macro(side, RECONCILE_MACRO, args)


def run_conformance(side: Side) -> CallResult:
    return run_macro(side, CONFORMANCE_MACRO, None)
