"""Defaults file ``./.data-parity.env``.

Only the keys A, B and PROJECT_B are honoured. Binary names (DBT_A/DBT_B) are deliberately NOT read from
the file: a file checked into a repository must not be able to choose which program gets executed.
The file is parsed, never executed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

ALLOWED_KEYS = ("A", "B", "PROJECT_B")


def parse_env_text(text: str) -> Tuple[Dict[str, str], List[str]]:
    """Return (values for the allowed keys, names of the ignored keys).

    Tolerates a UTF-8 BOM, CRLF or LF line ends, a missing trailing newline, blank lines, ``# comments``,
    an optional ``export`` prefix, spaces around ``=`` and single or double quoted values.
    """
    values: Dict[str, str] = {}
    ignored: List[str] = []
    if text.startswith("﻿"):
        text = text[1:]
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value.find(value[0], 1) != -1:
            value = value[1:value.find(value[0], 1)]  # quoted: whatever follows the closing quote is ignored
        else:
            # unquoted value: a ' #' starts a trailing comment
            cut = value.find(" #")
            if cut != -1:
                value = value[:cut].rstrip()
        if key in ALLOWED_KEYS:
            values[key] = value
        elif key:
            ignored.append(key)
    return values, ignored


def load_env_file(path: Path) -> Tuple[Dict[str, str], List[str]]:
    """Load ``path`` if it exists; ({}, []) otherwise."""
    try:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return {}, []
    return parse_env_text(text)
