"""The top-level help (`data-parity help`), described once and drawn two ways.

On an interactive terminal: bold section titles, colored commands and options, text wrapped to the terminal width.
Anywhere else (pipe, file, NO_COLOR, ASCII only): the same content as plain text at a fixed width, so the output is
stable and diffable. The content lives here, not in the layout code, so the two renderings cannot drift apart.
"""
from __future__ import annotations

import textwrap
from typing import List, Optional, Tuple

from . import __version__
from .ui import Terminal, paint, PLAIN

PLAIN_WIDTH = 100
INDENT = 2

Row = Tuple[str, str, str]     # (what to type, what it does, optional example)
# (title, scope hint, kind, rows, left column cap); kind "steps" = command line then its description underneath
SECTIONS: List[Tuple[str, str, str, List[Row], int]] = [
    ("Quick start", "in your dbt project", "steps", [
        ("data-parity run --a dev --b prod", "compare every model tagged data_parity", ""),
        ("data-parity report RUN_DIR", "re-render the report of a saved run, no dbt call", ""),
        ("data-parity conformance --a dev --b prod", "check once that the row hash is portable between the engines", ""),
    ], 0),
    ("Commands", "", "table", [
        ("run [MODEL ...]", "compare the models (every model tagged data_parity if none is given)",
         "data-parity run orders customers --a dev --b prod --last 30"),
        ("report RUN_DIR", "re-render parity.md from a saved run directory, without dbt",
         "data-parity report parity_out/run-20250101-120000 --technical"),
        ("conformance", "check that the row hash is portable between the two engines",
         "data-parity conformance --a dev --b prod"),
        ("help", "show this help (also: data-parity <command> --help)", ""),
    ], 18),
    ("Sides", "run, conformance", "table", [
        ("--a TARGET --b TARGET", "dbt targets of side A and side B", ""),
        ("--dbt-a BIN --dbt-b BIN", "dbt binary per side (default: dbt)", ""),
        ("--project DIR", "dbt project directory (default: current directory)", ""),
        ("--project-b DIR", "project directory of side B, if different", ""),
        ("--profiles-dir-a/-b DIR", "dbt profiles directory per side", ""),
        ("--schema-a/-b [DB.]SCHEMA", "read each model's table in this schema instead of the project's ref()", ""),
        ("--mapping-a/-b FILE", "`model: [db.]schema.table` lines; wins over --schema-a/-b", ""),
        ("--label-a/-b TEXT", "side names in the report (default: the targets)", ""),
    ], 28),
    ("Scope and cost", "run", "table", [
        ("--as-of YYYY-MM-DD", "exclude partitions on or after this date (a freshness gap is not a content gap)", ""),
        ("--last N", "only the N latest partitions of each model (bounds the scan cost)", ""),
        ("--buckets N", "hash buckets per partition (default 256)", ""),
        ("--drill-limit N", "max partitions drilled per model (default 200); above it, the drilled partitions "
                            "are spread evenly from the first to the last differing one", ""),
    ], 28),
    ("Columns", "run", "table", [
        ("--exclude-column COL", "column left out on both sides, or MODEL.COL for one model (repeatable)", ""),
        ("--exclude-prefix PREFIX", "columns starting with PREFIX are left out (repeatable; meta_ always is)", ""),
        ("--no-default-exclude", "also compare the meta_ columns", ""),
        ("--legacy a|b", "which side is the legacy one (default b); words models found on one side only", ""),
    ], 28),
    ("Output", "run, report", "table", [
        ("--out DIR", "work directory; a fresh run-<timestamp> sub-directory is created inside "
                      "(default: ./parity_out in the dbt project)", ""),
        ("--report FILE", "Markdown report path (default: <out>/run-<timestamp>/parity.md); "
                          "parity.json stays in the run directory", ""),
        ("--json FILE", "also write a machine-readable result", ""),
        ("--detail N", "partitions listed inline in the report (default and maximum 15; the complete list "
                       "is in parity.json)", ""),
        ("--no-hint", "leave out the \"To inspect\" line of each differing model", ""),
        ("--no-timeline", "leave out the per-month table of differing days", ""),
        ("--technical", "also show hash buckets and the method details", ""),
        ("--no-color", "no colors in the terminal (NO_COLOR is honored too)", ""),
    ], 28),
]

FOOTER_NOTES = [
    "Defaults can be stored in ./.data-parity.env (keys A, B and PROJECT_B only): A=dev  B=local",
]
EXIT_CODES = [("0", "identical"), ("1", "differences"), ("2", "error")]
_WORDS = ("data-parity", "run", "report", "conformance", "help")


def _style_left(term: Terminal, left: str) -> str:
    """Bold the commands and options, dim the placeholders (TARGET, DIR, N...)."""
    out = []
    for tok in left.split(" "):
        bold = tok.startswith("-") or tok in _WORDS
        out.append(paint(term, tok, 1) if bold else paint(term, tok, 2))
    return " ".join(out)


def render(term: Optional[Terminal] = None, version: str = __version__) -> str:
    """The help text for ``term`` (PLAIN when None)."""
    term = term or PLAIN
    width = PLAIN_WIDTH if term.plain else min(term.width, PLAIN_WIDTH)
    dash = "\u2500" if term.unicode else "-"
    lines: List[str] = []
    pad = " " * INDENT

    def rule() -> str:
        return paint(term, dash * (width - 1), 2)

    def title(name: str, hint: str) -> None:
        lines.append("")
        if term.interactive:
            lines.append(paint(term, name, 1, 36) + (paint(term, "  " + hint, 2) if hint else ""))
        else:
            lines.append("%s%s:" % (name, " (%s)" % hint if hint else ""))

    lines.append("%s %s" % (paint(term, "data-parity", 1), paint(term, "v" + version, 2)))
    lines += textwrap.wrap("Check that dbt models are identical on two engines, without moving data.", width)
    if term.interactive:
        lines.append(rule())
    lines.append("")
    lines.append("usage: data-parity <command> [options]")

    for name, hint, kind, rows, cap in SECTIONS:
        title(name, hint)
        if kind == "steps":
            for left, right, _ in rows:
                lines.append(pad + paint(term, "$ ", 32) + _style_left(term, left))
                lines += [pad + "  " + paint(term, w, 2 if term.interactive else 0) if term.interactive else pad + "  " + w
                          for w in textwrap.wrap(right, width - INDENT - 2)]
            continue
        left_w = min(max(len(left) for left, _, _ in rows), cap)
        text_w = max(20, width - INDENT - left_w - 2)
        hang = " " * (INDENT + left_w + 2)
        for left, right, example in rows:
            wrapped = textwrap.wrap(right, text_w) or [""]
            if len(left) > left_w:                        # too long for the column: description on the next line
                lines.append(pad + _style_left(term, left))
                lines += [hang + w for w in wrapped]
            else:
                lines.append(pad + _style_left(term, left) + " " * (left_w - len(left) + 2) + wrapped[0])
                lines += [hang + w for w in wrapped[1:]]
            if example:
                lines += [hang + paint(term, w, 2) for w in textwrap.wrap("e.g. " + example, text_w, subsequent_indent="    ")]

    title("Exit codes", "")
    lines.append(pad + "   ".join("%s %s" % (paint(term, c, 1, 32 if c == "0" else 33 if c == "1" else 31), d)
                                  for c, d in EXIT_CODES))
    lines.append("")
    for note in FOOTER_NOTES:
        lines += textwrap.wrap(note, width)
    return "\n".join(lines) + "\n"
