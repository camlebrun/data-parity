"""Terminal detection and small drawing helpers."""
import io
import json

from data_parity import ui


class FakeStream(io.StringIO):
    def __init__(self, tty=True, encoding="utf-8"):
        super().__init__()
        self._tty, self._enc = tty, encoding

    def isatty(self):
        return self._tty

    @property
    def encoding(self):
        return self._enc


def detect(stream=None, env=None, **kw):
    return ui.detect(stream or FakeStream(), env if env is not None else {}, enable_vt=False, **kw)


def test_not_a_terminal_is_plain_even_with_color_requested():
    t = detect(FakeStream(tty=False), {"FORCE_COLOR": "1"})
    assert t == ui.PLAIN and t.plain and not t.color and not t.unicode


def test_interactive_utf8_terminal_gets_everything():
    t = detect()
    assert t.interactive and t.color and t.unicode and t.width >= ui.MIN_WIDTH


def test_no_color_env_and_flag():
    assert not detect(env={"NO_COLOR": "1"}).color
    assert not detect(no_color=True).color
    assert detect(env={"NO_COLOR": ""}).color          # empty value does not count (no-color.org)


def test_dumb_terminal_is_not_interactive():
    t = detect(env={"TERM": "dumb"})
    assert not t.interactive and not t.color


def test_non_utf8_encoding_falls_back_to_ascii():
    assert not detect(FakeStream(encoding="ascii")).unicode
    assert not detect(FakeStream(encoding="cp1252")).unicode
    assert detect(FakeStream(encoding="utf-8")).unicode


def test_stream_without_isatty_is_plain():
    class Bare:
        encoding = "utf-8"
    assert ui.detect(Bare(), {}, enable_vt=False) == ui.PLAIN


def test_paint_only_with_color():
    on, off = ui.Terminal(True, True, True, 80), ui.Terminal(True, False, True, 80)
    assert ui.paint(on, "x", 1, 31) == "\x1b[1;31mx\x1b[0m"
    assert ui.paint(off, "x", 1, 31) == "x" and "\x1b" not in ui.paint(off, "x", 1)


def test_fit_truncates_with_ellipsis():
    assert ui.fit("short", 10) == "short"
    assert ui.fit("a" * 80, 20) == "a" * 19 + "…"
    assert ui.fit("a" * 80, 20, unicode=False) == "a" * 17 + "..."
    assert len(ui.fit("a" * 80, 2, unicode=False)) == 2 and ui.fit("abc", 0) == ""


def test_width_never_below_minimum(monkeypatch):
    monkeypatch.setenv("COLUMNS", "30")
    assert detect().width == ui.MIN_WIDTH


# ---------------------------------------------------------------------------------------------- verdict card

import pytest

from conftest import base_models, mutate
from data_parity.drill import RunResult
from test_cli import RUN, corrupted, two_sides

RICH = ui.Terminal(True, False, True, 80)
ASCII = ui.Terminal(True, False, False, 80)


def result(env, b_models=None, *extra):
    two_sides(env, b_models)
    env.run(*RUN, *extra)
    return RunResult.from_dict(env.last_json())


def test_card_differences_lists_differing_models_first(env):
    lines = ui.card_lines(result(env, corrupted()), RICH, 2.4)
    text = "\n".join(lines)
    assert "DIFFERENCES  3 of 3 models differ" in text and "2.4s" in text and "scope: all data" in text
    assert all(len(ln) == 80 for ln in lines)
    assert "3 of 6 partitions differ; amount, status" in text


def test_card_orders_differing_before_identical(env):
    r = result(env, mutate(base_models(), lambda m: m["customers"]["rows"][2].__setitem__(2, "zz")))
    body = [ln for ln in ui.card_lines(r, RICH) if "✗" in ln or "✓" in ln]
    assert "✗" in body[0] and "customers" in body[0] and all("✓" in ln for ln in body[1:])


def test_card_limited_scope_never_says_plain_identical(env):
    r = result(env, None, "--last", "2")
    text = "\n".join(ui.card_lines(r, RICH))
    assert "IDENTICAL on the checked scope" in text and "scope: " + r.options.scope_text in text
    assert "all data" not in text


@pytest.mark.parametrize("width", [60, 80, 120])
def test_card_fits_every_width_and_ascii(env, width):
    r = result(env, corrupted())
    r.models[0].name = "a_very_long_model_name_" * 4
    for unicode in (True, False):
        lines = ui.card_lines(r, ui.Terminal(True, False, unicode, width))
        assert {len(ln) for ln in lines} == {width}, lines
        if not unicode:
            "\n".join(lines).encode("ascii")


def test_card_color_only_when_allowed(env):
    r = result(env, corrupted())
    assert "\x1b" not in "\n".join(ui.card_lines(r, RICH))
    colored = ui.card_lines(r, ui.Terminal(True, True, True, 80))
    assert any("\x1b[1;31mDIFFERENCES" in ln for ln in colored)


def test_cli_prints_the_card_only_on_a_terminal(env, monkeypatch):
    two_sides(env, corrupted())
    plain = env.run(*RUN)
    assert "RESULT: DIFFERENCES" in plain.out and "┌" not in plain.out
    monkeypatch.setattr(ui, "detect", lambda *a, **k: RICH)
    tty = env.run(*RUN)
    assert tty.rc == plain.rc == 1 and "┌─ data-parity" in tty.out and "report: " in tty.out


# ---------------------------------------------------------------------------------------------- live progress


def test_progress_settles_each_pass_and_marks_skipped_ones():
    s = io.StringIO()
    p = ui.Progress(s, RICH, interval=0.01)
    p("pass 1 (one grouped scan per side)")
    import time
    time.sleep(0.05)
    p.close()
    out = s.getvalue()
    assert "✓ pass 1 of 3 · one grouped scan per side" in out
    assert "– pass 2 of 3  skipped" in out and "– pass 3 of 3  skipped" in out
    assert "pass 1 of 3  skipped" not in out and out.count("\n") == 3


def test_progress_ascii_and_failure_marks_no_skipped_line():
    s = io.StringIO()
    p = ui.Progress(s, ASCII, interval=0.01)
    p("pass 1 (scan)")
    p("pass 2 (buckets)")
    p.close(ok=False)
    out = s.getvalue()
    out.encode("ascii")
    assert "ok pass 1 of 3 - scan" in out and "xx pass 2 of 3 - buckets" in out and "skipped" not in out


def test_progress_runs_through_the_cli_on_stderr_only(env, monkeypatch):
    two_sides(env)
    monkeypatch.setattr(ui, "detect", lambda *a, **k: RICH)
    r = env.run(*RUN)
    assert r.rc == 0 and "pass 1 of 3" in r.err and "pass 2 of 3  skipped" in r.err and "pass 3 of 3  skipped" in r.err
    assert "pass 1" not in r.out and "\r" not in r.out and "\x1b" not in r.out


# ---------------------------------------------------------------------------------------------- next step and errors


def test_card_ends_with_report_and_one_next_command(env, monkeypatch):
    two_sides(env, corrupted())
    monkeypatch.setattr(ui, "detect", lambda *a, **k: RICH)
    out = env.run(*RUN).out.splitlines()
    assert out[-1].startswith("next:   data-parity report") and "--technical" in out[-1]
    assert out[-2].startswith("report: ") and out[-3].startswith("└")


def test_scoped_identical_suggests_the_same_run_without_the_scope(env, monkeypatch):
    two_sides(env)
    monkeypatch.setattr(ui, "detect", lambda *a, **k: ui.Terminal(True, False, True, 200))
    out = env.run(*RUN, "--last", "2").out
    nxt = [ln for ln in out.splitlines() if ln.startswith("next:")][0]
    assert "data-parity run" in nxt and "--last" not in nxt


def test_error_card_names_side_cause_and_fix(env, monkeypatch):
    env.world("ta")
    env.world("tb", fail_text="Compilation Error: macro 'data_parity_reconcile' is undefined")
    monkeypatch.setattr(ui, "detect", lambda *a, **k: ui.Terminal(True, False, True, 100))
    r = env.run(*RUN)
    assert r.rc == 2
    assert "side B" in r.out and "data_parity package is not installed" in r.out and "fix:  dbt deps" in r.out
    framed = [ln for ln in r.out.splitlines() if ln[:1] in "┌│├└"]
    assert framed and all(len(ln) == 100 for ln in framed)


# ---------------------------------------------------------------------------------------------- display never changes results


def _artifacts(env, index):
    d = env.run_dirs()[index]
    data = json.loads((d / "parity.json").read_text(encoding="utf-8"))
    started = data.pop("started", "") or "-"
    md = (d / "parity.md").read_text(encoding="utf-8").replace(started, "<started>").replace(d.name, "<run>")
    return data, md


@pytest.mark.parametrize("scenario", ["identical", "differences", "scoped", "error"])
def test_exit_code_report_and_json_are_the_same_with_either_display(env, monkeypatch, scenario):
    if scenario == "error":
        env.world("ta")
    else:
        two_sides(env, corrupted() if scenario == "differences" else None)
    argv = RUN + (["--last", "2"] if scenario == "scoped" else [])
    if scenario == "error":
        argv = ["run", "--a", "ta", "--b", "ghost"]
    plain = env.run(*argv)
    with monkeypatch.context() as m:
        m.setattr(ui, "detect", lambda *a, **k: ui.Terminal(True, True, True, 80))
        rich = env.run(*argv)
    assert plain.rc == rich.rc
    assert _artifacts(env, 0) == _artifacts(env, 1)


# ---------------------------------------------------------------------------------------------- help rendering

from data_parity import helpview


@pytest.mark.parametrize("width", [60, 80, 100])
def test_rich_help_fits_the_terminal_and_keeps_every_option(width):
    text = helpview.render(ui.Terminal(True, False, True, width))
    assert all(len(ln) <= width for ln in text.splitlines()), [ln for ln in text.splitlines() if len(ln) > width]
    for flag in ("--a", "--last", "--exclude-prefix", "--technical", "--no-color"):
        assert flag in text


def test_rich_help_colors_only_when_allowed_and_plain_has_none():
    assert "\x1b" in helpview.render(ui.Terminal(True, True, True, 80))
    assert "\x1b" not in helpview.render(ui.Terminal(True, False, True, 80))
    plain = helpview.render()
    assert "\x1b" not in plain and "─" not in plain and "Quick start (in your dbt project):" in plain
    plain.encode("ascii")


def test_help_content_is_the_same_in_both_renderings():
    flags = lambda t: {w for w in t.replace("/", " ").split() if w.startswith("--")}      # noqa: E731
    assert flags(helpview.render(ui.Terminal(True, False, True, 100))) == flags(helpview.render()) != set()
