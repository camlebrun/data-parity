"""Markdown recap, JSON result and the short terminal summary.

Only counts, hash sums, partition values, bucket numbers and column names appear here. Never a key, never a
row value. The Markdown / JSON files are UTF-8 (emoji, block characters); the terminal summary is ASCII only.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .drill import KIND_TEXT, ModelResult, PartDiff, RunResult, pkey, spread_sample

ICON = {"identical": "\u2705", "identical_scoped": "\u26a0\ufe0f", "differences": "\u274c", "error": "\U0001f4a5"}
_DATE = re.compile(r"^(\d{4})-(\d{2})-\d{2}")
INLINE_MAX = 15      # partition rows listed inline, whatever --detail says (parity.json holds the full list)
TABLE_MAX = 15       # rows of the bounded tables (columns, timeline)


# ---------------------------------------------------------------------------------------------- helpers


def ascii_safe(s: str) -> str:
    return s.encode("ascii", "backslashreplace").decode("ascii")


def esc(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def num(n: Optional[int]) -> str:
    return "-" if n is None else "{:,}".format(n)


def signed(n: Optional[int]) -> str:
    if n is None:
        return "-"
    return "0" if n == 0 else "{:+,}".format(n)


def pct(k: int, n: int) -> str:
    if n <= 0:
        return "-"
    v = 100.0 * k / n
    return "%.1f%%" % v if v < 10 else "%d%%" % round(v)


def plural(n: int, word: str) -> str:
    return "%s %s%s" % ("{:,}".format(n), word, "" if n == 1 else "s")


def bar(share: float, width: int = 10) -> str:
    share = max(0.0, min(1.0, share))
    filled = int(round(share * width))
    if share > 0 and filled == 0:
        filled = 1
    return "\u2588" * filled + "\u2591" * (width - filled)


def quote_arg(s: str, windows: Optional[bool] = None) -> str:
    """Quote one argument for the current shell (PowerShell on Windows, POSIX sh elsewhere)."""
    win = (os.name == "nt") if windows is None else windows
    if win:
        if s and re.fullmatch(r"[A-Za-z0-9_./:\\=+@%,-]+", s):
            return s
        return "'" + s.replace("'", "''") + "'"
    return shlex.quote(s)


def join_command(argv: Sequence[str], windows: Optional[bool] = None) -> str:
    return " ".join(quote_arg(a, windows) for a in argv)


def unit_of(m: ModelResult) -> str:
    parts = m.all_partitions
    return "day" if parts and all(_DATE.match(p) for p in parts) else "partition"


def what(kind: str, a: str, b: str) -> str:
    return KIND_TEXT[kind].format(a=a, b=b)


def short_what(kind: str, a: str, b: str) -> str:
    return {"onlyA": "only in " + a, "onlyB": "only in " + b, "rows": "missing/extra rows", "hash": "changed values"}[kind]


def bucket_cell(p: PartDiff) -> str:
    if not p.drilled:
        return "not sampled"
    if not p.buckets:
        return "none found"
    shown = ", ".join(p.buckets[:12])
    return "%d: %s%s" % (len(p.buckets), shown, ", ..." if len(p.buckets) > 12 else "")


def column_cell(p: PartDiff) -> str:
    if not p.drilled:
        return "not sampled"
    shown = p.columns[:5]
    cols = ", ".join("`%s`" % c for c in shown) + (" +%d more" % (len(p.columns) - 5) if len(p.columns) > 5 else "")
    if p.kind in ("rows", "onlyA", "onlyB"):
        return (cols + " (+ missing or extra rows)") if cols else "n/a (row set differs)"
    return cols or "not attributed"


# ---------------------------------------------------------------------------------------------- sample


def _word(m: ModelResult, n: int = 2) -> str:
    return unit_of(m) + ("" if n == 1 else "s")


def sample_basis(m: ModelResult) -> str:
    """Says exactly what the column attribution rests on (a sample, or every differing partition)."""
    k = len(m.parts)
    if not m.parts:
        return ""
    ordered = sorted(m.parts, key=lambda p: pkey(p.partition))
    if m.truncated:
        return "column attribution is based on a sample of %s of %s differing %s, spread evenly from %s to %s" % (
            "{:,}".format(m.sampled), "{:,}".format(k), _word(m), ordered[0].partition, ordered[-1].partition)
    return "column attribution covers all %s differing %s" % ("{:,}".format(k), _word(m, k))


def attribution_base(m: ModelResult) -> Tuple[int, str]:
    """(denominator, wording) of the column shares: the sampled days that have changed values."""
    hashes = sum(1 for p in m.parts if p.kind == "hash" and p.drilled)
    n = "{:,}".format(hashes)
    if m.truncated:
        every = hashes == m.sampled
        return hashes, ("the %s sampled %s" % (n, _word(m, hashes)) if every else "the %s sampled %s with changed values" % (n, _word(m, hashes)))
    return hashes, "all %s differing %s with changed values" % (n, _word(m, hashes))


# ---------------------------------------------------------------------------------------------- diagnosis


def diagnosis(m: ModelResult, la: str, lb: str) -> str:
    if m.verdict == "structure":
        return ("The structure differs (columns, types, key or scope), so the row hashes are not comparable; "
                "deeper passes were skipped for this model.")
    unit = unit_of(m)
    n = m.partitions_total
    if not m.parts:
        bits = []
        if m.delta:
            bits.append("%s has %s %s rows than %s" % (lb, "{:,}".format(abs(m.delta)), "more" if m.delta > 0 else "fewer", la))
        if m.table_columns:
            bits.append("values differ in %s (no partition column, so the whole table was compared)" % ", ".join("`%s`" % c for c in m.table_columns))
        if not bits:
            bits.append("the table hash differs and the difference could not be attributed to a column")
        return bits[0][0].upper() + bits[0][1:] + ("; " + "; ".join(bits[1:]) if len(bits) > 1 else "") + "."
    hashp = [p for p in m.parts if p.kind == "hash"]
    rowp = [p for p in m.parts if p.kind in ("rows", "onlyA", "onlyB")]
    bits: List[str] = []
    if hashp:
        s = "values changed in %s of %s" % ("{:,}".format(len(hashp)), "{:,}".format(n)) + " %ss" % unit
        attributed = [p for p in hashp if p.columns]
        base_n, base_txt = attribution_base(m)
        cnt: Dict[str, int] = {}
        for p in attributed:
            for c in p.columns:
                cnt[c] = cnt.get(c, 0) + 1
        ranked = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
        if ranked:
            top, k = ranked[0]
            share = "%s of %s" % ("{:,}".format(k), base_txt)
            if len(ranked) == 1:
                s += ", all in `%s` (%s)" % (top, share)
            elif k >= 0.6 * len(attributed):
                s += ", concentrated in `%s` (%s)" % (top, share)
            else:
                s += ", spread over %d columns (mostly `%s`, %s)" % (len(ranked), top, share)
        elif any(not p.drilled for p in hashp):
            s += " (columns not attributed: none of these %ss was sampled)" % unit
        bits.append(s)
    if rowp:
        if len(rowp) == 1:
            p = rowp[0]
            d = p.rows_a - p.rows_b
            if p.kind == "onlyA":
                bits.append("%s %s exists only in %s" % (unit, p.partition, la))
            elif p.kind == "onlyB":
                bits.append("%s %s exists only in %s" % (unit, p.partition, lb))
            else:
                bits.append("%s has %s %s row%s than %s on %s" % (
                    lb, "{:,}".format(abs(d)), "fewer" if d > 0 else "more", "" if abs(d) == 1 else "s", la, p.partition))
        else:
            net = sum(p.rows_b - p.rows_a for p in rowp)
            bits.append("missing or extra rows in %s %ss (net %s rows)" % ("{:,}".format(len(rowp)), unit, signed(net)))
    if hashp:
        bits.append(sample_basis(m))
    text = "; ".join(bits)
    return text[0].upper() + text[1:] + "."


# ---------------------------------------------------------------------------------------------- timeline


def timeline(m: ModelResult) -> List[Tuple[str, int, int]]:
    """[(period, differing, total)]: per month when the data spans at most two years, else per year, else per
    decade; the first granularity that fits in TABLE_MAX rows (the table is bounded whatever the table size)."""
    if unit_of(m) != "day" or not m.parts:
        return []
    years = {_DATE.match(p).group(1) for p in m.all_partitions}
    cuts = ([7] if len(years) <= 2 else []) + [4, 3]
    res: List[Tuple[str, int, int]] = []
    for cut in cuts:
        tot: Dict[str, int] = {}
        dif: Dict[str, int] = {}
        for p in m.all_partitions:
            tot[p[:cut]] = tot.get(p[:cut], 0) + 1
        for p in m.parts:
            dif[p.partition[:cut]] = dif.get(p.partition[:cut], 0) + 1
        res = [(k + ("x" if cut == 3 else ""), dif[k], tot.get(k, 0)) for k in sorted(dif)]
        if len(res) <= TABLE_MAX:
            break
    return res


# ---------------------------------------------------------------------------------------------- markdown


def _scope_line(r: RunResult) -> str:
    o = r.options
    if o.last is None and not o.as_of:
        return "whole tables"
    bits = []
    if o.last is not None:
        bits.append("the last %s per model" % plural(o.last, "partition"))
    if o.as_of:
        bits.append("partitions before %s only (later and NULL partitions excluded)" % o.as_of)
    return "; ".join(bits)


def not_checked(r: RunResult) -> List[str]:
    o = r.options
    out: List[str] = []
    if o.last is not None:
        det = []
        for m in r.models:
            if m.scope_info:
                det.append("`%s` %s of %s" % (m.name, m.scope_info.get("checked"), m.scope_info.get("total")))
        out.append("**Older partitions** (`--last %d`): only the %d latest partitions of each model were compared%s."
                   % (o.last, o.last, " (" + ", ".join(det) + ")" if det else ""))
    if o.as_of:
        out.append("**Partitions on or after %s**, and rows with a NULL partition value (`--as-of`): excluded on both sides "
                   "(a freshness gap is not a content gap)." % o.as_of)
    for m in r.models:
        if m.truncated:
            out.append("**Column attribution is sampled for `%s`** (`--drill-limit %d`): %s; the other %s %s are compared "
                       "at partition level only (buckets and columns not attributed)."
                       % (m.name, o.drill_limit, sample_basis(m), "{:,}".format(m.truncated), _word(m, m.truncated)))
    skipped = [m.name for m in r.models if m.verdict == "structure"]
    if skipped:
        out.append("**Row-level comparison skipped** for %s: structure differs, hashes are not comparable."
                   % ", ".join("`%s`" % s for s in skipped))
    whole = [m.name for m in r.models if m.verdict != "structure" and not m.partitioned]
    if whole and (o.last is not None or o.as_of):
        out.append("**No partition scope applies** to %s (no partition column): `--last` and `--as-of` do not apply to them, "
                   "they are compared as whole tables." % ", ".join("`%s`" % n for n in whole))
    ex = [(m.name, m.excluded) for m in r.models if m.excluded and m.excluded != "none"]
    if ex:
        out.append("**Excluded columns** (by configuration, `meta.data_parity`): "
                   + "; ".join("`%s`: %s" % (n, ", ".join("`%s`" % c for c in e.split(",") if c)) for n, e in ex) + ".")
    out.append("**Untagged models**: only models tagged `data_parity` (or with `meta.data_parity`) are part of the comparison.")
    return out


def _structure_word(m: ModelResult, r: RunResult) -> str:
    if m.structure_kind in ("only_a", "only_b"):
        side = "A" if m.structure_kind == "only_a" else "B"
        return "only in %s" % r.role(side)
    return "schema mismatch" if m.structure_kind == "schema" else "structure"


def _structure_section(m: ModelResult, r: RunResult) -> List[str]:
    la, lb = r.label_a, r.label_b
    if m.structure_kind in ("only_a", "only_b"):
        return ["Model `%s` is %s." % (m.name, r.one_sided_text(m)), "",
                "It was found on one side only (matched by its name, or by `meta.data_parity.compare_as`), so there is nothing to compare.", ""]
    out: List[str] = []
    if m.cols_only_a:
        out.append("- columns only in %s: %s" % (la, ", ".join("`%s`" % c for c in m.cols_only_a)))
    if m.cols_only_b:
        out.append("- columns only in %s: %s" % (lb, ", ".join("`%s`" % c for c in m.cols_only_b)))
    for t in m.cols_type_diff:
        out.append("- type class differs: %s" % t.replace("A:", la + ":", 1).replace("B:", lb + ":", 1))
    if out:
        out.append("")
    return out + ["Structure lines that differ (`cols`, `pk`, `scope`, `note`):", "", "```"] + m.structure_diff + ["```", ""]


def _overview(r: RunResult) -> List[str]:
    la, lb = r.label_a, r.label_b
    lines = ["| model | verdict | rows %s | rows %s | \u0394 rows | partitions differing | main columns |" % (esc(la), esc(lb)),
             "|---|---|--:|--:|--:|---|---|"]
    for m in r.models:
        if m.verdict == "iso":
            v, pdiff = ("\u26a0\ufe0f identical on scope" if r.options.scope_limited and m.partitioned else "\u2705 identical"), ("0/%s" % "{:,}".format(m.partitions_total)) if m.partitioned else "-"
            cols = "-"
        elif m.verdict == "structure":
            v, pdiff, cols = "\u274c " + _structure_word(m, r), "-", "-"
        else:
            v = "\u274c differs"
            if m.parts:
                k = len(m.parts)
                pdiff = "%s/%s (%s)" % ("{:,}".format(k), "{:,}".format(m.partitions_total), pct(k, m.partitions_total))
            else:
                pdiff = "table-level"
            cc = m.column_counts()
            cols = ", ".join("`%s`" % c for c, _ in cc[:3]) + (", ..." if len(cc) > 3 else "") if cc else "-"
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s |" % (
            esc(m.name), v, num(m.rows_a), num(m.rows_b), signed(m.delta) if m.verdict != "structure" else "-", pdiff, cols))
    return lines


def _spread_idx(k: int, n: int) -> List[int]:
    if n <= 0:
        return []
    return list(range(k)) if n >= k else ([0] if n == 1 else [i * (k - 1) // (n - 1) for i in range(n)])


def inline_parts(ordered: List[PartDiff], limit: int) -> List[PartDiff]:
    """Partitions listed inline: up to 3 row-count-only ones first (never attributed to columns, the most actionable;
    drilled ones preferred), then sampled (drilled) ones spread evenly, then, only if fewer than ``limit``, unsampled
    ones spread evenly. Sorted back into partition order."""
    if len(ordered) <= limit:
        return list(ordered)
    only_rows = [p for p in ordered if p.kind != "hash"]
    pick: List[PartDiff] = []
    for drilled in (True, False):
        pool = [p for p in only_rows if p.drilled == drilled]
        pick += [pool[i] for i in _spread_idx(len(pool), min(3, limit) - len(pick))]
    chosen = {id(p) for p in pick}
    for drilled in (True, False):
        rest = [p for p in ordered if p.drilled == drilled and id(p) not in chosen]
        for i in _spread_idx(len(rest), min(limit - len(pick), len(rest))):
            pick.append(rest[i])
    return sorted(pick, key=lambda p: pkey(p.partition))


def _model_section(m: ModelResult, r: RunResult, top: Optional[int]) -> List[str]:
    la, lb = r.label_a, r.label_b
    out = ["### \u274c `%s`" % m.name, ""]
    if m.structure_kind in ("only_a", "only_b"):
        out.append("> %s." % r.one_sided_text(m).capitalize())
    else:
        out.append("> %s" % diagnosis(m, la, lb))
    out.append("")
    if m.verdict == "structure":
        return out + _structure_section(m, r)
    facts = ["rows: %s = %s, %s = %s (\u0394 %s)" % (la, num(m.rows_a), lb, num(m.rows_b), signed(m.delta)),
             "primary key: %s" % ("none (buckets come from the full-row hash)" if m.pk == "none" else "`%s`" % m.pk)]
    if m.parts:
        facts.append("partitions differing: **%s of %s** (%s)" % ("{:,}".format(len(m.parts)), "{:,}".format(m.partitions_total), pct(len(m.parts), m.partitions_total)))
    facts += ["note: %s" % n for n in m.notes]
    if m.comment:
        facts.append(m.comment)
    out += ["- " + f for f in facts] + [""]
    if m.pk == "none":
        out += ["No primary key: a changed row appears in TWO buckets (old and new version), so columns are compared over the "
                "whole partition, not per bucket.", ""]

    cc = m.column_counts()
    if cc:
        if m.parts:
            denom, base_txt = attribution_base(m)
            sampled = bool(m.truncated)
            head = ["column", "sampled %ss affected" % unit_of(m) if sampled else "%ss affected" % unit_of(m),
                    "share of sampled %ss" % unit_of(m) if sampled else "share of differing %ss" % unit_of(m)]
            lead = "**Differing columns** (ranked). Shares are relative to %s." % base_txt
        else:
            denom = sum(1 for b in m.bucket_diffs if b[1] == "hash") or sum(n for _, n in cc)
            head = ["column", "buckets affected", "share of differing buckets"]
            lead = "**Differing columns** (ranked)"
        out += [lead, "", "| %s |" % " | ".join(head), "|---|--:|--:|"]
        for c, n in cc[:TABLE_MAX]:
            out.append("| `%s` | %s | %s |" % (esc(c), "{:,}".format(n), pct(n, denom)))
        if len(cc) > TABLE_MAX:
            out += ["", "+%d more column%s (the full ranking is in `parity.json`)." % (len(cc) - TABLE_MAX, "" if len(cc) - TABLE_MAX == 1 else "s")]
        out.append("")

    if m.parts:
        unit = unit_of(m)
        by = {k: [p for p in m.parts if p.kind == k] for k in ("onlyA", "onlyB", "rows", "hash")}
        out += ["**Nature of the differences**", "", "| nature | %ss |" % unit, "|---|--:|",
                "| changed values (same row count) | %s |" % "{:,}".format(len(by["hash"])),
                "| missing or extra rows | %s |" % "{:,}".format(len(by["rows"]))]
        if by["onlyA"]:
            out.append("| %s exists only in %s | %s |" % (unit, esc(la), "{:,}".format(len(by["onlyA"]))))
        if by["onlyB"]:
            out.append("| %s exists only in %s | %s |" % (unit, esc(lb), "{:,}".format(len(by["onlyB"]))))
        out.append("")

        ordered = sorted(m.parts, key=lambda p: pkey(p.partition))
        out.append("**Timeline**: first differing %s `%s`, last `%s`." % (unit, esc(ordered[0].partition), esc(ordered[-1].partition)))
        tl = timeline(m)
        if tl:
            out += ["", "| period | differing | of | share of the period | |", "|---|--:|--:|--:|---|"]
            top_share = max((d / t if t else 0) for _, d, t in tl) or 1
            for per, d, t in tl:
                out.append("| %s | %s | %s | %s | %s |" % (per, "{:,}".format(d), "{:,}".format(t), pct(d, t), bar((d / t if t else 0) / top_share)))
        out.append("")

        limit = INLINE_MAX if top is None else max(1, min(top, INLINE_MAX))
        shown = inline_parts(ordered, limit)
        n_drilled = sum(1 for p in shown if p.drilled)
        if len(shown) == len(ordered):
            title = "**Differing %ss**." % unit
        elif m.truncated and n_drilled == len(shown):
            title = "**%d of the %s sampled %ss (out of %s differing %ss), spread evenly from %s to %s; each shows the buckets and columns that differ.**" % (
                len(shown), "{:,}".format(m.sampled), unit, "{:,}".format(len(ordered)), unit, esc(shown[0].partition), esc(shown[-1].partition))
        elif m.truncated and n_drilled:
            title = "**%d of %s differing %ss: %d of the %s sampled ones (with their buckets and columns), completed by %d spread evenly and not sampled.**" % (
                len(shown), "{:,}".format(len(ordered)), unit, n_drilled, "{:,}".format(m.sampled), len(shown) - n_drilled)
        else:
            title = "**%d of %s differing %ss, spread evenly from first to last.**" % (len(shown), "{:,}".format(len(ordered)), unit)
        out += [title, "", "| partition | what | rows %s | rows %s | buckets | columns |" % (esc(la), esc(lb)), "|---|---|--:|--:|---|---|"]
        for p in shown:
            out.append("| %s | %s | %s | %s | %s | %s |" % (esc(p.partition), what(p.kind, esc(la), esc(lb)), num(p.rows_a), num(p.rows_b), esc(bucket_cell(p)), esc(column_cell(p))))
        out.append("")
        if len(ordered) > INLINE_MAX:
            out += ["Only %d of the %s differing %ss are listed. The complete list is in `parity.json` in the run directory (aggregates only)." % (len(shown), "{:,}".format(len(ordered)), unit), ""]
    else:
        if m.bucket_diffs:
            out += ["**Differing buckets**", "", "| bucket | what | rows %s | rows %s |" % (esc(la), esc(lb)), "|---|---|--:|--:|"]
            for b, k, ra, rb in m.bucket_diffs:
                out.append("| %s | %s | %s | %s |" % (esc(b), what(k, esc(la), esc(lb)), num(ra), num(rb)))
            out.append("")
    return out


def render_markdown(r: RunResult) -> str:
    la, lb = r.label_a, r.label_b
    o = r.options
    L: List[str] = ["# Parity report", ""]
    banner = {
        "identical": "**Identical**: all %d model%s match on both engines." % (len(r.models), "" if len(r.models) == 1 else "s"),
        "identical_scoped": "**Identical on a limited scope** (%s). This says nothing about the data outside that scope." % o.scope_text,
        "differences": "**Differences found**: %d of %d model%s %s." % (r.n_diff, len(r.models), "" if len(r.models) == 1 else "s", "differs" if r.n_diff == 1 else "differ"),
        "error": "**No verdict**: the comparison could not be completed (%s)." % (r.errors[0] if r.errors else "see below"),
    }[r.status]
    L += ["> %s %s" % (ICON[r.status], banner), ""]
    L += ["| | |", "|---|---|",
          "| Date | %s |" % esc(r.started),
          "| Engine A | `%s` (dbt target `%s`) |" % (esc(la), esc(r.target_a)),
          "| Engine B | `%s` (dbt target `%s`) |" % (esc(lb), esc(r.target_b)),
          "| Compared | counts and sums of per-row hashes, by partition, then by hash bucket (%d buckets), then by column |" % o.buckets,
          "| Scope | %s |" % esc(_scope_line(r)),
          "| Models | %s |" % (esc(", ".join(o.models)) if o.models else ("every tagged model (%d)" % len(r.models) if r.models else "every tagged model"))]
    L.append("")

    if r.status == "error":
        L += ["## Error", ""] + ["- %s" % e for e in r.errors] + [""]
        if r.error_detail:
            L += ["```"] + r.error_detail + ["```", ""]
    else:
        L += ["## Not checked", ""] + ["- " + x for x in not_checked(r)] + [""]
        L += ["## Overview", ""] + _overview(r) + [""]
        diff = [m for m in r.models if m.is_diff]
        if diff:
            L += ["## Differences", ""]
            for m in diff:
                L += _model_section(m, r, o.detail)
        same = [m for m in r.models if not m.is_diff]
        if same:
            L += ["## Identical models", ""]
            for m in same[:TABLE_MAX]:
                extra = ", %s" % plural(m.partitions_total, "partition") if m.partitioned else ""
                L.append("- %s `%s`: %s%s%s" % ("\u26a0\ufe0f" if o.scope_limited and m.partitioned else "\u2705", m.name, plural(m.rows_a or 0, "row"), extra,
                                             " (checked scope only)" if o.scope_limited and m.partitioned else ""))
            if len(same) > TABLE_MAX:
                L.append("- +%d more identical models" % (len(same) - TABLE_MAX))
            L.append("")

    cmd = r.command or "data-parity run"
    L += ["## How to reproduce", "", "```", cmd, "```", "",
          "Tool: data-parity %s. Exit codes: 0 identical, 1 differences, 2 error." % __version__, "",
          "**What this proves, and what it does not.** Each engine computes, per partition, the row count and the sum of "
          "per-row hashes (two independent hash sums); only these aggregates are compared, no row leaves the warehouse. "
          "Equal aggregates mean the tables are equal up to a hash collision or errors that cancel exactly inside a sum "
          "(probability about 2^-24 per sum for a single change). It does not check anything outside the scope above, "
          "excluded columns, or the meaning of the data.", ""]
    return "\n".join(L).rstrip("\n") + "\n"


def render_json(r: RunResult) -> str:
    return json.dumps(r.to_dict(), indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------------------------- terminal


def terminal_summary(r: RunResult) -> List[str]:
    """One short block per model, ASCII only."""
    la, lb = r.label_a, r.label_b
    out: List[str] = []
    for m in r.models:
        if m.verdict == "iso":
            if r.options.scope_limited:
                out.append("%-24s ISO on the checked scope (%s; %s rows)" % (m.name, r.options.scope_text, num(m.rows_a)))
            else:
                out.append("%-24s ISO   (%s rows identical)" % (m.name, num(m.rows_a)))
            continue
        if m.verdict == "structure":
            if m.structure_kind in ("only_a", "only_b"):
                out.append("%-24s DIFF  %s" % (m.name, r.one_sided_text(m)))
            elif m.structure_kind == "schema" and (m.cols_only_a or m.cols_only_b):
                bits = ["only in %s: %s" % (la, ", ".join(m.cols_only_a))] if m.cols_only_a else []
                bits += ["only in %s: %s" % (lb, ", ".join(m.cols_only_b))] if m.cols_only_b else []
                out.append("%-24s DIFF  schema mismatch (%s)" % (m.name, "; ".join(bits)))
            else:
                out.append("%-24s DIFF  structure (columns, types, key or scope) differs: see the report" % m.name)
            continue
        out.append("%-24s DIFF" % m.name)
        if m.delta == 0:
            out.append("  rows        : %s on both sides" % num(m.rows_a))
        else:
            out.append("  rows        : %s = %s, %s = %s (delta %s)" % (la, num(m.rows_a), lb, num(m.rows_b), signed(m.delta)))
        if m.parts:
            ordered = sorted(m.parts, key=lambda p: pkey(p.partition))
            nrows = sum(1 for p in m.parts if p.kind != "hash")
            out.append("  partitions  : %s of %s differ (%s -> %s)" % (num(len(m.parts)), num(m.partitions_total), ordered[0].partition, ordered[-1].partition))
            out.append("  nature      : %d with missing/extra rows, %d with changed values only" % (nrows, len(m.parts) - nrows))
            cc = m.column_counts()
            if cc:
                npart = sum(1 for p in m.parts if p.columns)
                out.append("  columns     : %s  [partitions with changed values: %d]" % (", ".join("%s (%d)" % kv for kv in cc), npart))
            if m.truncated:
                out.append("  sampled     : columns attributed on %s of %s differing partitions, spread evenly from %s to %s"
                           % (num(m.sampled), num(len(m.parts)), ordered[0].partition, ordered[-1].partition))
        else:
            out.append("  table-level difference (no partition column)")
            if m.table_columns:
                out.append("  columns     : %s" % ", ".join(m.table_columns))
    return [ascii_safe(x) for x in out]
