"""Terminal rendering helpers (standard library only).

This module decides *how* to draw (rich, ASCII or plain); it never decides *what* the result is. The report files are
produced elsewhere, so nothing here can change ``parity.md`` or ``parity.json``.

Rules (spec: cli-experience):
- anything that is not an interactive terminal gets the plain text output of the previous versions;
- ``NO_COLOR`` (any non-empty value) and ``--no-color`` switch colors off;
- box-drawing characters are used only when the stream encoding can represent them, otherwise ASCII.
"""
from __future__ import annotations

import os
import re
import shutil
import textwrap
import threading
import time
from dataclasses import dataclass
from typing import Mapping, Optional, TextIO

MIN_WIDTH = 60
DEFAULT_WIDTH = 80
_BOX_SAMPLE = "─│┌┐└┘✓✗…"


@dataclass(frozen=True)
class Terminal:
    """What a stream can show."""
    interactive: bool   # the stream is a terminal: animation and the card are allowed
    color: bool         # ANSI colors allowed
    unicode: bool       # box-drawing and symbols allowed
    width: int          # usable columns, never below MIN_WIDTH

    @property
    def plain(self) -> bool:
        return not self.interactive


PLAIN = Terminal(interactive=False, color=False, unicode=False, width=DEFAULT_WIDTH)


def _can_encode(stream: TextIO, text: str) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def _is_tty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def enable_windows_vt() -> bool:
    """Turn on ANSI escape processing on Windows 10+ consoles. Returns False when it cannot be enabled."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING already on
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        return False


def detect(stream: TextIO, env: Optional[Mapping[str, str]] = None, no_color: bool = False,
           enable_vt: bool = True) -> Terminal:
    """Describe ``stream``. Not a terminal -> PLAIN, whatever the environment says."""
    env = os.environ if env is None else env
    if not _is_tty(stream):
        return PLAIN
    try:
        width = shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns
    except (ValueError, OSError):
        width = DEFAULT_WIDTH
    # COLUMNS is honored by get_terminal_size already; clamp so a card always fits (>= 60 columns).
    width = max(MIN_WIDTH, width)
    dumb = env.get("TERM", "") == "dumb"
    vt_ok = enable_windows_vt() if (enable_vt and os.name == "nt") else True
    color = not (no_color or env.get("NO_COLOR") or dumb) and vt_ok
    return Terminal(interactive=not dumb, color=color, unicode=_can_encode(stream, _BOX_SAMPLE), width=width)


def paint(term: Terminal, text: str, *codes: int) -> str:
    """Wrap ``text`` in ANSI SGR codes, only when colors are allowed."""
    if not term.color or not codes:
        return text
    return "\x1b[%sm%s\x1b[0m" % (";".join(str(c) for c in codes), text)


def fit(text: str, width: int, unicode: bool = True) -> str:
    """Cut ``text`` to ``width`` characters, ending with an ellipsis when something was removed."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    ell = "…" if unicode else "..."
    if width <= len(ell):
        return ell[:width]
    return text[: width - len(ell)] + ell


# ------------------------------------------------------------------------------------------------ verdict card

_BOX = {True: dict(tl="┌", tr="┐", bl="└", br="┘", h="─", v="│", ml="├", mr="┤"),
        False: dict(tl="+", tr="+", bl="+", br="+", h="-", v="|", ml="+", mr="+")}


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return "%.1fs" % seconds
    m, s = divmod(int(round(seconds)), 60)
    return "%dm %02ds" % (m, s)


def _model_detail(r, m) -> str:
    """One short phrase per model (what differs), no row value."""
    if m.verdict == "iso":
        rows = "{:,}".format(m.rows_a) if m.rows_a is not None else "-"
        return "identical (%s rows)" % rows
    if m.verdict == "structure":
        if m.structure_kind in ("only_a", "only_b"):
            return r.one_sided_text(m)
        return "structure differs"
    cols = ", ".join(c for c, _ in m.column_counts()) if m.parts else ", ".join(m.table_columns)
    if m.parts:
        head = "%d of %d partitions differ" % (len(m.parts), m.partitions_total)
    else:
        head = "table-level difference"
    return "%s; %s" % (head, cols) if cols else head


_BACKTICK = re.compile(r"`([^`]+)`")


def fix_command(r) -> Optional[str]:
    """The command suggested by a known error (the first `...` in its message), or None."""
    for text in list(r.errors) + list(r.error_detail):
        m = _BACKTICK.search(text)
        if m:
            return m.group(1)
    return None


def card_lines(r, term: Terminal, duration: Optional[float] = None, report_path: Optional[str] = None,
               next_cmd: Optional[str] = None) -> list:
    """The verdict card: a frame of exactly ``term.width`` columns, then the report path and the next command.

    It ends with the report path and one next command; on an error it gives the cause, the fix and the command to run."""
    box = _BOX[term.unicode]
    inner = term.width - 4
    mark_diff, mark_ok = ("✗", "✓") if term.unicode else ("DIFF", "ISO ")
    ordered = sorted(r.models, key=lambda m: (m.verdict == "iso", m.name))
    if r.status == "identical_scoped":
        verdict = "IDENTICAL on the checked scope"
    elif r.status == "identical":
        verdict = "IDENTICAL"
    elif r.status == "differences":
        verdict = "DIFFERENCES"
    else:
        verdict = "ERROR"
    color = {"IDENTICAL": (1, 32), "DIFFERENCES": (1, 31), "ERROR": (1, 31)}.get(verdict.split(" on")[0], (1, 33))
    n = len(r.models)
    summary = {"differences": "%d of %d model%s differ" % (r.n_diff, n, "" if n == 1 else "s"),
               "error": "no verdict, the comparison did not finish"}.get(r.status, "all %d model%s identical" % (n, "" if n == 1 else "s"))
    scope = r.options.scope_text if r.scope_limited else "all data"
    meta = "%s vs %s · scope: %s" % (r.label_a, r.label_b, scope)
    if duration is not None:
        meta += " · " + format_duration(duration)
    if not term.unicode:
        meta = meta.replace(" · ", " | ")

    def row(text: str, styled: Optional[str] = None) -> str:
        shown = fit(text, inner, term.unicode)
        body = styled if (styled is not None and shown == text) else shown
        return "%s %s%s %s" % (box["v"], body, " " * (inner - len(shown)), box["v"])

    def rule(left: str, right: str, title: str = "") -> str:
        lead = box["h"] + (" %s " % title if title else "")
        return left + lead + box["h"] * (term.width - 2 - len(lead)) + right

    lines = [rule(box["tl"], box["tr"], "data-parity"),
             row("%s  %s" % (verdict, summary), "%s  %s" % (paint(term, verdict, *color), summary)),
             row(meta)]
    if ordered:
        lines.append(rule(box["ml"], box["mr"]))
        name_w = min(max(len(m.name) for m in ordered), max(8, inner // 3))
        mark_w = max(len(mark_diff), len(mark_ok))
        for m in ordered:
            mark = mark_ok if m.verdict == "iso" else mark_diff
            name = fit(m.name, name_w, term.unicode).ljust(name_w)
            head = "%s %s  " % (mark.ljust(mark_w), name)
            detail = fit(_model_detail(r, m), inner - len(head), term.unicode)
            plain = head + detail
            styled = paint(term, mark.ljust(mark_w), 32 if m.verdict == "iso" else 31) + plain[mark_w:]
            lines.append(row(plain, styled))
    if r.status == "error":
        lines.append(rule(box["ml"], box["mr"]))
        for ln in textwrap.wrap(r.errors[0] if r.errors else "no verdict", inner)[:6]:
            lines.append(row(ln))
        for ln in [x.strip() for x in r.error_detail if x.strip()][:4]:
            lines.append(row("  " + ln))
        fix = fix_command(r)
        if fix:
            lines.append(row("fix:  " + fix, "fix:  " + paint(term, fix, 1)))
    lines.append(rule(box["bl"], box["br"]))
    # report path and next command sit under the frame, never cut: a truncated command could not be pasted
    if report_path:
        lines.append("report: " + report_path)
    if next_cmd:
        lines.append("next:   " + next_cmd)
    return lines


# ------------------------------------------------------------------------------------------------ live progress

TOTAL_PASSES = 3
_SPIN_UNICODE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_ASCII = "|/-\\"
_PASS = re.compile(r"^pass (\d+)\b\s*\(?(.*?)\)?$")


class Progress:
    """Live progress on an interactive stderr. Callable: ``progress("pass 2 (buckets of ...)")``.

    The current pass is drawn in place with a spinner; a finished pass stays on screen with its duration.
    Nothing is written to stdout. Use only when ``term.interactive``; otherwise keep the plain line-per-message output.
    """

    def __init__(self, stream: TextIO, term: Terminal, interval: float = 0.1) -> None:
        self.stream, self.term, self.interval = stream, term, interval
        self._lock = threading.Lock()
        self._current: Optional[tuple] = None   # (number, text, started)
        self._seen: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._frame = 0

    # -- drawing ---------------------------------------------------------------------------------------------
    def _label(self, number: int, text: str) -> str:
        return "pass %d of %d · %s" % (number, TOTAL_PASSES, text) if self.term.unicode else \
            "pass %d of %d - %s" % (number, TOTAL_PASSES, text)

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def _spin(self) -> None:
        spin = _SPIN_UNICODE if self.term.unicode else _SPIN_ASCII
        while not self._stop.wait(self.interval):
            with self._lock:
                if self._current is None:
                    continue
                number, text, started = self._current
                self._frame = (self._frame + 1) % len(spin)
                line = "%s %s  %s" % (spin[self._frame], self._label(number, text), format_duration(time.monotonic() - started))
                self._write("\r\x1b[2K" + paint(self.term, fit(line, self.term.width - 1, self.term.unicode), 36))

    def _settle(self, mark: str, colour: int) -> None:
        """Replace the spinner line of the current pass by its final line."""
        if self._current is None:
            return
        number, text, started = self._current
        line = "%s %s  %s" % (mark, self._label(number, text), format_duration(time.monotonic() - started))
        self._write("\r\x1b[2K" + paint(self.term, fit(line, self.term.width - 1, self.term.unicode), colour) + "\n")
        self._current = None

    # -- public ----------------------------------------------------------------------------------------------
    def __call__(self, message: str) -> None:
        m = _PASS.match(message)
        number, text = (int(m.group(1)), m.group(2) or message) if m else (len(self._seen) + 1, message)
        with self._lock:
            self._settle("✓" if self.term.unicode else "ok", 32)
            self._seen.add(number)
            self._current = (number, text, time.monotonic())
        if self._thread is None:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def close(self, ok: bool = True) -> None:
        """Stop the animation, settle the last pass and show the passes that did not run as skipped."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._lock:
            if ok:
                self._settle("✓" if self.term.unicode else "ok", 32)
            else:
                self._settle("✗" if self.term.unicode else "xx", 31)
            if ok:
                dash = "–" if self.term.unicode else "-"
                for n in range(1, TOTAL_PASSES + 1):
                    if n not in self._seen:
                        line = "%s pass %d of %d  skipped, nothing left to drill" % (dash, n, TOTAL_PASSES)
                        self._write(paint(self.term, fit(line, self.term.width - 1, self.term.unicode), 2) + "\n")
