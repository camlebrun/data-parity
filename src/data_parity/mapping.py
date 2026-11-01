"""Mapping file (``--mapping-a`` / ``--mapping-b``): the table read for a model on one side.

One entry per line, ``model: [database.]schema.table``; blank lines and ``# comments`` are ignored. Parsed, never
executed, and validated like the macros do (letters, digits, ``_`` and ``-`` in every part).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

_PART = re.compile(r"^[A-Za-z0-9_-]+$")


class MappingError(ValueError):
    pass


def parse_mapping_text(text: str, source: str = "mapping") -> Dict[str, str]:
    if text.startswith("﻿"):
        text = text[1:]
    out: Dict[str, str] = {}
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cut = line.find(" #")
        if cut != -1:
            line = line[:cut].rstrip()
        model, sep, rel = line.partition(":")
        model, rel = model.strip(), rel.strip().strip("'\"").strip()
        where = "%s:%d" % (source, n)
        if not sep or not model or not rel:
            raise MappingError("%s: expected `model: [database.]schema.table`, got %r" % (where, raw.strip()))
        if not _PART.match(model):
            raise MappingError("%s: invalid model name %r" % (where, model))
        parts = rel.split(".")
        if len(parts) not in (2, 3) or not all(_PART.match(p) for p in parts):
            raise MappingError("%s: %r must be [database.]schema.table (letters, digits, _ and -)" % (where, rel))
        if model in out:
            raise MappingError("%s: model %r is mapped twice" % (where, model))
        out[model] = rel
    return out


def load_mapping(path: Path) -> Dict[str, str]:
    try:
        text = Path(path).read_bytes().decode("utf-8")
    except OSError as exc:
        raise MappingError("cannot read the mapping file %s: %s" % (path, exc))
    except UnicodeDecodeError:
        raise MappingError("the mapping file %s is not UTF-8" % path)
    return parse_mapping_text(text, str(path))
