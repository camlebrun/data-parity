"""Compact Markdown recap, even sampling of the drilled partitions and the offline `report` command."""
import datetime
import io
import json
import re
from pathlib import Path

from conftest import base_models, mutate

from data_parity.cli import main, write_report
from data_parity.drill import Options, compare, spread_sample
from data_parity.report import inline_parts, render_json, render_markdown
from data_parity.runner import CallResult, Side

RUN = ["run", "--a", "ta", "--b", "tb"]


# ---------------------------------------------------------------------------------------------- synthetic runs


def synthetic_call(n_days: int, n_diff: int):
    """A fake `call` producing PARITY lines for one model of n_days days; the first n_diff of every 'n_days / n_diff'
    days (spread over the whole range) have changed values; side B differs in a column that depends on the day."""
    start = datetime.date(2007, 1, 1)
    days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(n_days)]
    step = n_days / float(n_diff)
    differing = {days[int(i * step)] for i in range(n_diff)}

    def col_of(d: str) -> str:
        return "price" if int(d[-2:]) % 3 else "volume"

    def call(side: Side, args):
        b = 1 if side.key == "B" else 0
        lvl = args["level"]
        out = ["## big"]
        if lvl == 1:
            out += ["cols|id:int,price:num,volume:num", "pk|id"]
            out.append("table|rows=%d|h1=%d|h2=%d" % (n_days, 1000 + b * n_diff, 2000))
            for d in days:
                out.append("part|%s|rows=1|h1=%d|h2=7" % (d, 5 + (b if d in differing else 0)))
        elif lvl == 2:
            out.append("buckets|256")
            for p in args["drill"]["big"]:
                out.append("bucket|%s|17|rows=1|h1=%d|h2=7" % (p, 5 + b))
        else:
            out.append("buckets|256")
            for p, bk in args["drill_buckets"]["big"]:
                for c in ("price", "volume"):
                    out.append("col|%s|%s|%s|rows=1|h1=%d|h2=7" % (p, bk, c, 5 + (b if c == col_of(p) else 0)))
        return CallResult(side.key, 0, out, {}, "")

    return call, days, sorted(differing)


def run_synthetic(tmp_path: Path, n_days: int, n_diff: int, limit: int = 200):
    call, days, differing = synthetic_call(n_days, n_diff)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    saved = {}

    def save(name: str, text: str):
        saved[name] = text
        (run_dir / name).write_text(text, encoding="utf-8", newline="\n")

    a, b = Side("A", "ta", "ta", "dbt", tmp_path, None), Side("B", "tb", "tb", "dbt", tmp_path, None)
    res = compare(a, b, Options(models=["big"], drill_limit=limit), call=call, save=save)
    res.started, res.command = "2026-10-25 10:00", "data-parity run big --a ta --b tb"
    (run_dir / "parity.json").write_text(render_json(res), encoding="utf-8", newline="\n")
    write_report(run_dir / "parity.md", res)
    return res, run_dir, differing


def tables(md: str):
    """Every Markdown table as a list of rows (list of cell lists), escaped pipes kept inside their cell."""
    out, cur = [], []
    for ln in md.split("\n") + [""]:
        if ln.startswith("|"):
            cur.append([c.strip() for c in re.split(r"(?<!\\)\|", ln.strip())[1:-1]])
        elif cur:
            out.append(cur)
            cur = []
    return out


# ---------------------------------------------------------------------------------------------- sampling


def test_spread_sample_even_first_last_deterministic():
    keys = ["k%04d" % i for i in range(1000)]
    s = spread_sample(keys, 7)
    assert len(s) == 7 and s[0] == keys[0] and s[-1] == keys[-1]
    assert s == spread_sample(keys, 7)                           # deterministic
    idx = [keys.index(k) for k in s]
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    assert max(gaps) - min(gaps) <= 1                            # evenly spaced
    assert spread_sample(keys, 1) == [keys[0]] and spread_sample(keys, 1000) == keys and spread_sample(keys, 5000) == keys
    assert spread_sample(keys, 2) == [keys[0], keys[-1]]
    assert len(set(spread_sample(keys, 999))) == 999


def test_sample_represents_every_period(tmp_path):
    res, _, differing = run_synthetic(tmp_path, 3000, 1000, limit=100)
    m = res.models[0]
    drilled = [p.partition for p in m.parts if p.drilled]
    assert len(drilled) == 100 and drilled[0] == differing[0] and drilled[-1] == differing[-1]
    years = {d[:4] for d in drilled}
    assert years == {d[:4] for d in differing}                   # no year is left out
    assert m.truncated == 900


# ---------------------------------------------------------------------------------------------- size, no extra file, wording


def test_markdown_stays_compact_for_6000_differing_partitions(tmp_path):
    res, run_dir, differing = run_synthetic(tmp_path, 7200, 6000)
    md = (run_dir / "parity.md").read_text(encoding="utf-8")
    assert len(differing) == 6000
    assert len(md.split("\n")) <= 150, len(md.split("\n"))
    assert "<details>" not in md
    inline = [r for t in tables(md) if t[0][0] == "partition" for r in t[2:]]
    assert 0 < len(inline) <= 15
    assert md.endswith("\n") and not md.endswith("\n\n")
    assert "Only %d of the 6,000 differing days are listed." % len(inline) in md
    assert "`parity.json`" in md and "](" not in md


def test_no_extra_file_and_json_lists_every_differing_partition(tmp_path):
    res, run_dir, differing = run_synthetic(tmp_path, 7200, 6000)
    assert not list(run_dir.rglob("*.c" + "sv"))
    data = json.loads((run_dir / "parity.json").read_text(encoding="utf-8"))
    parts = data["models"][0]["differing_partitions"]
    assert [p["partition"] for p in parts] == differing and len(parts) == 6000
    assert sum(1 for p in parts if p["drilled"]) == 200 and {p["drilled"] for p in parts} == {True, False}
    assert set(parts[0]) == {"partition", "kind", "rows_a", "rows_b", "drilled", "differing_buckets", "differing_columns"}


def test_wording_names_the_sample(tmp_path):
    res, run_dir, differing = run_synthetic(tmp_path, 7200, 6000)
    md = (run_dir / "parity.md").read_text(encoding="utf-8")
    assert ("column attribution is based on a sample of 200 of 6,000 differing days, spread evenly from %s to %s"
            % (differing[0], differing[-1])) in md
    assert "sampled days affected" in md and "share of sampled days" in md and "of the 200 sampled days" in md
    # everything drilled: no sampling claim, "all N differing days"
    (tmp_path / "x").mkdir()
    res2, run_dir2, _ = run_synthetic(tmp_path / "x", 40, 10)
    md2 = (run_dir2 / "parity.md").read_text(encoding="utf-8")
    assert "covers all 10 differing days" in md2 and "sample of" not in md2
    assert "share of differing days" in md2


def inline_rows(md: str):
    return [r for t in tables(md) if t[0][0] == "partition" for r in t[2:]]


def test_inline_rows_are_all_drilled_and_show_columns(tmp_path):
    res, run_dir, differing = run_synthetic(tmp_path, 7200, 6000)
    md = (run_dir / "parity.md").read_text(encoding="utf-8")
    rows = inline_rows(md)
    assert len(rows) == 15
    for r in rows:
        assert r[4] not in ("", "not sampled") and r[5] not in ("", "not sampled", "not attributed"), r
    drilled = [p.partition for p in res.models[0].parts if p.drilled]
    assert rows[0][0] == drilled[0] and rows[-1][0] == drilled[-1]
    assert set(r[0] for r in rows) <= set(drilled)
    assert ("15 of the 200 sampled days (out of 6,000 differing days), spread evenly from %s to %s; "
            "each shows the buckets and columns that differ" % (drilled[0], drilled[-1])) in md
    assert "spread evenly from first to last" not in md
    assert "Only 15 of the 6,000 differing days are listed. The complete list is in `parity.json`" in md
    assert len(md.split("\n")) <= 150


def test_inline_fills_with_unsampled_when_few_drilled(tmp_path):
    res, run_dir, _ = run_synthetic(tmp_path, 7200, 6000, limit=5)
    md = (run_dir / "parity.md").read_text(encoding="utf-8")
    rows = inline_rows(md)
    assert len(rows) == 15
    assert sum(1 for r in rows if r[5] == "not sampled") == 10
    assert sum(1 for r in rows if r[5] != "not sampled") == 5
    assert len({len(r) for r in rows}) == 1
    assert "5 of the 6,000 sampled" not in md and "completed by 10 spread evenly and not sampled" in md


def test_inline_includes_row_count_only_partitions(tmp_path):
    res, _, _ = run_synthetic(tmp_path, 7200, 6000)
    m = res.models[0]
    for i in (3, 1000, 2500, 4000, 5999):                        # mostly NOT drilled
        m.parts[i].kind = "rows"
    shown = inline_parts(sorted(m.parts, key=lambda p: p.partition), 15)
    assert len(shown) == 15 and sum(1 for p in shown if p.kind == "rows") == 3
    assert [p.partition for p in shown] == sorted(p.partition for p in shown)
    md = render_markdown(res)
    rows = inline_rows(md)
    assert sum(1 for r in rows if "same row count" not in r[1]) == 3 and len(rows) == 15
    assert len({len(r) for r in rows}) == 1


def test_markdown_tables_are_consistent(tmp_path):
    res, run_dir, _ = run_synthetic(tmp_path, 7200, 6000)
    md = (run_dir / "parity.md").read_text(encoding="utf-8")
    ts = tables(md)
    assert len(ts) >= 5
    for t in ts:
        widths = {len(r) for r in t}
        assert len(widths) == 1, t[:3]
        assert re.fullmatch(r"\|?(:?-+:?\|)+", "|" + "|".join(t[1]) + "|") or all(set(c) <= set("-:") for c in t[1])
    assert md.count("`") % 2 == 0
    heads = [len(m.group(1)) for m in re.finditer(r"^(#+) ", md, re.M)]
    assert heads[0] == 1 and all(b - a <= 1 for a, b in zip(heads, heads[1:]))


# ---------------------------------------------------------------------------------------------- report sub-command


def test_report_command_regenerates_identical_report(tmp_path):
    res, run_dir, _ = run_synthetic(tmp_path, 400, 120, limit=30)
    original = (run_dir / "parity.md").read_text(encoding="utf-8")
    (run_dir / "parity.md").unlink()
    assert main(["report", str(run_dir)]) == 0
    assert (run_dir / "parity.md").read_text(encoding="utf-8") == original
    out = tmp_path / "elsewhere" / "recap.md"
    assert main(["report", str(run_dir), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == original
    assert [p.name for p in out.parent.iterdir()] == ["recap.md"]


def test_report_command_needs_a_run_dir(tmp_path, capsys):
    assert main(["report", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "parity.json" in err and "not a usable run directory" in err
    assert main(["report", str(tmp_path / "nope")]) == 2


def test_cli_run_then_report_roundtrip(env):
    def f(m):
        for i in range(0, 15, 3):
            m["orders"]["rows"][i][2] = "7.77"
    two_sides = mutate(base_models(), f)
    env.world("ta")
    env.world("tb", two_sides)
    assert env.run("run", "orders", *RUN[1:], "--drill-limit", "3").rc == 1
    d = env.run_dirs()[-1]
    before = (d / "parity.md").read_text(encoding="utf-8")
    assert "sample of 3 of 5 differing days, spread evenly from 2025-01-01 to 2025-01-05" in before
    assert [p["partition"] for p in env.last_json()["models"][0]["differing_partitions"] if p["drilled"]] == \
        ["2025-01-01", "2025-01-03", "2025-01-05"]
    assert main(["report", str(d)]) == 0
    assert (d / "parity.md").read_text(encoding="utf-8") == before
    # --report FILE: only the Markdown is written there; parity.json stays in the run directory
    rep = env.tmp / "rep" / "mine.md"
    assert env.run("run", "orders", *RUN[1:], "--report", str(rep)).rc == 1
    assert [p.name for p in rep.parent.iterdir()] == ["mine.md"]
