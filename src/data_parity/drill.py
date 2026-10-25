"""The three passes of the drill-down, as pure logic on the PARITY| lines.

pass 1  one grouped scan per side            -> table line + one line per partition
pass 2  buckets of the differing partitions  -> which hash buckets differ
pass 3  columns of the buckets whose row count is equal but whose hash differs

Nothing but counts, hash sums, partition values, bucket numbers and column names is ever handled.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .runner import CallResult, Side, run_conformance, run_reconcile

STRUCT_KEYS = ("cols", "pk", "as_of", "scope", "note")
KIND_TEXT = {
    "onlyA": "present only in {a}",
    "onlyB": "present only in {b}",
    "rows": "row counts differ: missing or extra rows",
    "hash": "same row count, different hashes: changed values",
}


class ParityError(Exception):
    """A run that cannot conclude (exit code 2). ``detail`` holds extra lines (log tails...)."""

    def __init__(self, message: str, detail: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or []


@dataclass
class Options:
    models: List[str] = field(default_factory=list)
    buckets: int = 256
    as_of: Optional[str] = None
    last: Optional[int] = None
    drill_limit: int = 200
    detail: Optional[int] = 15  # None = every partition

    @property
    def scope_limited(self) -> bool:
        return bool(self.as_of) or self.last is not None

    @property
    def scope_text(self) -> str:
        parts = []
        if self.last is not None:
            parts.append("last %d partition%s" % (self.last, "" if self.last == 1 else "s"))
        if self.as_of:
            parts.append("before %s" % self.as_of)
        return " / ".join(parts)


# ---------------------------------------------------------------------------------------------- parsing


def pkey(p: str) -> Tuple[int, int, str]:
    """Sort key for partition values: numeric when integer-like (so 9 < 10), else lexicographic."""
    if re.fullmatch(r"-?\d+", p):
        return (0, int(p), "")
    return (1, 0, p)


def spread_sample(keys: List[str], n: int) -> List[str]:
    """Pick ``n`` of ``keys`` (already sorted) spread evenly from the first to the last, deterministically.

    The first and the last are always included; with n < len(keys) the picks are the evenly spaced positions
    ``i * (K - 1) // (n - 1)``, so every stretch of the range is represented. Order is preserved.
    """
    k = len(keys)
    if n >= k:
        return list(keys)
    if n <= 1:
        return list(keys[:1])
    return [keys[i * (k - 1) // (n - 1)] for i in range(n)]


def _bkey(b: str) -> Tuple[int, int, str]:
    return (0, int(b), "") if re.fullmatch(r"\d+", b) else (1, 0, b)


@dataclass
class Section:
    name: str
    struct: List[str] = field(default_factory=list)
    pk: str = ""
    table_raw: Optional[str] = None
    table: Optional[Tuple[int, str, str]] = None
    parts: Dict[str, Tuple[int, str, str]] = field(default_factory=dict)
    buckets: Dict[Tuple[str, str], Tuple[int, str, str]] = field(default_factory=dict)
    cols: Dict[Tuple[str, str, str], Tuple[int, str, str]] = field(default_factory=dict)

    @property
    def has_parts(self) -> bool:
        return bool(self.parts)


def _agg(fields: List[str], where: str) -> Tuple[int, str, str]:
    try:
        r, h1, h2 = fields
        if not (r.startswith("rows=") and h1.startswith("h1=") and h2.startswith("h2=")):
            raise ValueError
        return int(r[5:]), h1[3:], h2[3:]
    except ValueError:
        raise ParityError("malformed PARITY line (%s): expected rows=..|h1=..|h2=.." % where)


def parse_sections(lines: List[str]) -> Dict[str, List[str]]:
    """Group the lines by ``## <model>`` header (lines before the first header are ignored)."""
    out: Dict[str, List[str]] = {}
    cur: Optional[List[str]] = None
    for ln in lines:
        if ln.startswith("## "):
            cur = out.setdefault(ln[3:].strip(), [])
        elif cur is not None and ln != "":
            cur.append(ln)
    return out


def parse_section(name: str, lines: List[str]) -> Section:
    s = Section(name)
    for ln in lines:
        kind, _, rest = ln.partition("|")
        if kind in STRUCT_KEYS:
            s.struct.append(ln)
        if kind == "pk":
            s.pk = rest
        elif kind == "table":
            s.table_raw = ln
            s.table = _agg(rest.split("|"), "%s table" % name)
        elif kind == "part":
            pieces = rest.rsplit("|", 3)
            if len(pieces) != 4:
                raise ParityError("malformed PARITY line (%s part)" % name)
            s.parts[pieces[0]] = _agg(pieces[1:], "%s part" % name)
        elif kind == "bucket":
            pieces = rest.rsplit("|", 3)
            if len(pieces) != 4 or "|" not in pieces[0]:
                raise ParityError("malformed PARITY line (%s bucket)" % name)
            p, b = pieces[0].rsplit("|", 1)
            s.buckets[(p, b)] = _agg(pieces[1:], "%s bucket" % name)
        elif kind == "col":
            pieces = rest.rsplit("|", 3)
            head = pieces[0].rsplit("|", 2)
            if len(pieces) != 4 or len(head) != 3:
                raise ParityError("malformed PARITY line (%s col)" % name)
            s.cols[(head[0], head[1], head[2])] = _agg(pieces[1:], "%s col" % name)
    return s


def diff_keyed(da: Dict[Any, Tuple[int, str, str]], db: Dict[Any, Tuple[int, str, str]]) -> List[Tuple[Any, str, int, int]]:
    """[(key, kind, rows_a, rows_b)] for every key that is not strictly equal. kind: onlyA onlyB rows hash."""
    out = []
    for k in set(da) | set(db):
        a, b = da.get(k), db.get(k)
        if a is not None and b is None:
            out.append((k, "onlyA", a[0], 0))
        elif b is not None and a is None:
            out.append((k, "onlyB", 0, b[0]))
        elif a[0] != b[0]:
            out.append((k, "rows", a[0], b[0]))
        elif a[1:] != b[1:]:
            out.append((k, "hash", a[0], b[0]))
    return out


# ---------------------------------------------------------------------------------------------- results


@dataclass
class PartDiff:
    partition: str
    kind: str
    rows_a: int
    rows_b: int
    drilled: bool = False
    buckets: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partition": self.partition,
            "kind": self.kind,
            "rows_a": self.rows_a,
            "rows_b": self.rows_b,
            "drilled": self.drilled,
            "differing_buckets": self.buckets,
            "differing_columns": self.columns,
        }


    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PartDiff":
        return cls(d["partition"], d["kind"], d["rows_a"], d["rows_b"], bool(d.get("drilled")),
                   list(d.get("differing_buckets") or []), list(d.get("differing_columns") or []))


def parse_scope_info(scope: List[str]) -> Dict[str, str]:
    info: Dict[str, str] = {}
    for sc in scope:
        m = re.match(r"last=(\d+)\|checked=(\d+)\|total=(\d+)\|from=(.*?)\|to=(.*)$", sc)
        if m:
            info = dict(zip(("last", "checked", "total", "from", "to"), m.groups()))
    return info


@dataclass
class ModelResult:
    name: str
    verdict: str  # iso | diff | structure
    rows_a: Optional[int] = None
    rows_b: Optional[int] = None
    pk: str = ""
    partitioned: bool = True
    partitions_total: int = 0
    all_partitions: List[str] = field(default_factory=list)  # not serialised
    parts: List[PartDiff] = field(default_factory=list)  # every differing partition, sorted
    truncated: int = 0  # differing partitions NOT drilled because of the drill limit
    bucket_diffs: List[Tuple[str, str, int, int]] = field(default_factory=list)  # table-level: bucket, kind, ra, rb
    table_columns: List[str] = field(default_factory=list)  # table-level differing columns
    column_buckets: Dict[str, int] = field(default_factory=dict)  # table-level: column -> buckets affected
    structure_diff: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    scope: List[str] = field(default_factory=list)
    scope_info: Dict[str, str] = field(default_factory=dict)
    excluded: str = ""
    comment: str = ""

    @property
    def delta(self) -> Optional[int]:
        if self.rows_a is None or self.rows_b is None:
            return None
        return self.rows_b - self.rows_a

    @property
    def is_diff(self) -> bool:
        return self.verdict != "iso"

    def column_counts(self) -> List[Tuple[str, int]]:
        """[(column, partitions affected)] ranked (table-level models: buckets affected)."""
        if not self.parts:
            return sorted(self.column_buckets.items(), key=lambda kv: (-kv[1], kv[0]))
        cnt: Dict[str, int] = {}
        for p in self.parts:
            for c in p.columns:
                cnt[c] = cnt.get(c, 0) + 1
        return sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))

    @property
    def sampled(self) -> int:
        """Differing partitions drilled to buckets and columns."""
        return len(self.parts) - self.truncated

    @classmethod
    def from_dict(cls, d: Dict[str, Any], all_partitions: Optional[List[str]] = None) -> "ModelResult":
        m = cls(d["model"], d["verdict"], d.get("rows_a"), d.get("rows_b"), d.get("primary_key") or "",
                bool(d.get("partitioned", True)), int(d.get("partitions_total") or 0))
        m.all_partitions = list(all_partitions or [])
        m.parts = [PartDiff.from_dict(x) for x in d.get("differing_partitions") or []]
        m.truncated = int(d.get("partitions_not_drilled") or 0)
        m.bucket_diffs = [(b["bucket"], b["kind"], b["rows_a"], b["rows_b"]) for b in d.get("table_level_buckets") or []]
        if not m.parts and not m.partitioned:
            m.column_buckets = {c["column"]: c["affected"] for c in d.get("columns") or []}
            m.table_columns = sorted(m.column_buckets)
        m.structure_diff = list(d.get("structure_diff") or [])
        m.notes = list(d.get("notes") or [])
        m.scope = list(d.get("scope") or [])
        m.scope_info = parse_scope_info(m.scope)
        m.excluded = d.get("excluded_columns") or ""
        m.comment = d.get("comment") or ""
        return m

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.name,
            "verdict": self.verdict,
            "rows_a": self.rows_a,
            "rows_b": self.rows_b,
            "delta_rows": self.delta,
            "primary_key": self.pk,
            "partitioned": self.partitioned,
            "partitions_total": self.partitions_total,
            "partitions_differing": len(self.parts),
            "partitions_not_drilled": self.truncated,
            "columns": [{"column": c, "affected": n} for c, n in self.column_counts()],
            "differing_partitions": [p.to_dict() for p in self.parts],
            "table_level_buckets": [
                {"bucket": b, "kind": k, "rows_a": ra, "rows_b": rb} for b, k, ra, rb in self.bucket_diffs
            ],
            "structure_diff": self.structure_diff,
            "notes": self.notes,
            "scope": self.scope,
            "excluded_columns": self.excluded,
            "comment": self.comment,
        }


@dataclass
class RunResult:
    status: str = "error"  # identical | identical_scoped | differences | error
    label_a: str = ""
    label_b: str = ""
    target_a: str = ""
    target_b: str = ""
    options: Options = field(default_factory=Options)
    models: List[ModelResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    error_detail: List[str] = field(default_factory=list)
    started: str = ""
    command: str = ""
    requested: List[str] = field(default_factory=list)
    passes: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {"identical": 0, "identical_scoped": 0, "differences": 1}.get(self.status, 2)

    @property
    def scope_limited(self) -> bool:
        return self.options.scope_limited or any(m.truncated for m in self.models)

    @property
    def n_diff(self) -> int:
        return sum(1 for m in self.models if m.is_diff)

    @property
    def verdict_text(self) -> str:
        n = len(self.models)
        if self.status == "identical":
            return "ISO: all %d model%s identical" % (n, "" if n == 1 else "s")
        if self.status == "identical_scoped":
            return "ISO on the checked scope (%s): all %d model%s identical" % (
                self.options.scope_text, n, "" if n == 1 else "s")
        if self.status == "differences":
            return "DIFFERENCES: %d of %d model%s %s" % (self.n_diff, n, "" if n == 1 else "s", "differs" if self.n_diff == 1 else "differ")
        return "ERROR: no verdict (%s)" % (self.errors[0] if self.errors else "unknown")

    @classmethod
    def from_dict(cls, d: Dict[str, Any], parts_by_model: Optional[Dict[str, List[str]]] = None) -> "RunResult":
        o = d.get("options") or {}
        opts = Options(models=list(o.get("models") or []), buckets=o.get("buckets", 256), as_of=o.get("as_of"),
                       last=o.get("last"), drill_limit=o.get("drill_limit", 200))
        r = cls(status=d.get("verdict", "error"), label_a=d["side_a"]["label"], label_b=d["side_b"]["label"],
                target_a=d["side_a"]["target"], target_b=d["side_b"]["target"], options=opts,
                errors=list(d.get("errors") or []), error_detail=list(d.get("error_detail") or []),
                started=d.get("started", ""), command=d.get("command", ""), requested=list(opts.models))
        pbm = parts_by_model or {}
        r.models = [ModelResult.from_dict(m, pbm.get(m["model"])) for m in d.get("models") or []]
        return r

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": "data-parity",
            "verdict": self.status,
            "exit_code": self.exit_code,
            "verdict_text": self.verdict_text,
            "scope_limited": self.scope_limited,
            "scope": self.options.scope_text or "whole tables",
            "started": self.started,
            "side_a": {"label": self.label_a, "target": self.target_a},
            "side_b": {"label": self.label_b, "target": self.target_b},
            "options": {
                "models": self.options.models,
                "buckets": self.options.buckets,
                "as_of": self.options.as_of,
                "last": self.options.last,
                "drill_limit": self.options.drill_limit,
            },
            "command": self.command,
            "models_total": len(self.models),
            "models_differing": self.n_diff,
            "errors": self.errors,
            "error_detail": self.error_detail,
            "models": [m.to_dict() for m in self.models],
        }


# ---------------------------------------------------------------------------------------------- engine

Caller = Callable[[Side, Dict[str, Any]], CallResult]
Progress = Callable[[str], None]
Saver = Callable[[str, str], None]


def _noop(*_a: Any) -> None:
    return None


class Drill:
    def __init__(self, a: Side, b: Side, opts: Options, call: Caller = run_reconcile,
                 progress: Progress = _noop, save: Saver = _noop):
        self.a, self.b, self.opts = a, b, opts
        self.call, self.progress, self.save = call, progress, save
        self.result = RunResult(
            label_a=a.label, label_b=b.label, target_a=a.target, target_b=b.target,
            options=opts, requested=list(opts.models),
        )
        self.excluded: Dict[str, str] = {}

    # -- one pass on both sides; any dbt failure on either side aborts the run (never "identical")
    def _both(self, key: str, name: str, args: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        self.progress(name)
        self.result.passes.append(name)
        res = [self.call(self.a, args), self.call(self.b, args)]
        bad = [r for r in res if r.failed]
        for r, side in zip(res, (self.a, self.b)):
            self.save("%s_%s.txt" % (key, r.side), "\n".join(r.lines) + "\n")
            if r.failed:
                self.save("raw_%s_%s.log" % (key, r.side), r.raw)
        if bad:
            msgs, detail = [], []
            for r, side in zip(res, (self.a, self.b)):
                if r.failed:
                    msgs.append("dbt failed on side %s (target %s, %s): %s" % (r.side, side.target, name.split(" (")[0], r.reason()))
                    detail.append("--- last log lines, side %s ---" % r.side)
                    detail.extend(r.tail())
            raise ParityError("; ".join(msgs), detail)
        if not self.excluded:
            self.excluded = dict(res[0].excluded)
            for k, v in res[1].excluded.items():
                self.excluded.setdefault(k, v)
        return res[0].lines, res[1].lines

    def _base_args(self, level: int) -> Dict[str, Any]:
        o = self.opts
        args: Dict[str, Any] = {}
        if o.models and level == 1:
            args["only"] = list(o.models)
        args["level"] = level
        args["buckets"] = o.buckets
        if o.as_of:
            args["as_of"] = o.as_of
        return args

    def run(self) -> RunResult:
        o = self.opts
        args1 = self._base_args(1)
        if o.last is not None:
            args1["partitions"] = {"last": o.last}
        la, lb = self._both("p1", "pass 1 (one grouped scan per side)", args1)
        raw_a, raw_b = parse_sections(la), parse_sections(lb)

        if o.models:
            gaps = []
            for m in o.models:
                lacking = [s for s, d in (("A", raw_a), ("B", raw_b)) if m not in d]
                if lacking:
                    gaps.append("%s (missing on side %s)" % (m, " and ".join(lacking)))
            if gaps:
                raise ParityError(
                    "model(s) not found in the dbt output: %s. Check the spelling, and that the model is tagged "
                    "`data_parity` (or has config.meta.data_parity) and not disabled." % ", ".join(gaps))
        names = sorted(set(raw_a) | set(raw_b))
        if not names:
            raise ParityError("no model in the outputs: tag your models with `data_parity` (see the README)")

        secs: Dict[str, Tuple[Section, Section]] = {}
        state: Dict[str, Dict[str, Any]] = {}
        results: Dict[str, ModelResult] = {}
        for n in names:
            sa, sb = parse_section(n, raw_a.get(n, [])), parse_section(n, raw_b.get(n, []))
            secs[n] = (sa, sb)
            mr = ModelResult(n, "iso", pk=sa.pk or sb.pk)
            ref = sa if n in raw_a else sb
            mr.partitioned = ref.has_parts
            mr.rows_a = sa.table[0] if sa.table else None
            mr.rows_b = sb.table[0] if sb.table else None
            mr.notes = [x.split("|", 1)[1] for x in ref.struct if x.startswith("note|")]
            mr.scope = [x.split("|", 1)[1] for x in ref.struct if x.startswith("scope|")]
            mr.scope_info = parse_scope_info(mr.scope)
            mr.excluded = self.excluded.get(n, "")
            mr.all_partitions = sorted(set(sa.parts) | set(sb.parts), key=pkey)
            mr.partitions_total = len(mr.all_partitions)
            results[n] = mr
            st: Dict[str, Any] = {"parts": [], "drill": []}
            state[n] = st
            if n not in raw_a or n not in raw_b:
                side = "A" if n in raw_a else "B"
                mr.verdict = "structure"
                mr.structure_diff = ["model present only on side %s" % side]
                continue
            if sa.struct != sb.struct:
                mr.verdict = "structure"
                mr.structure_diff = _struct_diff(sa.struct, sb.struct)
                continue
            if sa.table is None or sb.table is None:
                raise ParityError("model %s: no table| line in the pass 1 output" % n)
            st["parts"] = sorted(diff_keyed(sa.parts, sb.parts), key=lambda x: pkey(x[0]))
            if sa.table_raw != sb.table_raw or st["parts"]:
                mr.verdict = "diff"

        # ---- pass 2: buckets of the differing partitions (structure-equal models only)
        drill: Dict[str, List[str]] = {}
        for n in names:
            mr, st = results[n], state[n]
            if mr.verdict != "diff":
                continue
            sa, _ = secs[n]
            if st["parts"]:
                keys = [x[0] for x in st["parts"]]
                st["drill"] = spread_sample(keys, o.drill_limit)
                mr.truncated = len(keys) - len(st["drill"])
            elif not sa.has_parts:
                st["drill"] = ["*"]
            else:
                mr.comment = "table totals differ although no partition does; not drilled"
                continue
            drill[n] = st["drill"]
        p2: Dict[str, Tuple[Section, Section]] = {}
        if drill:
            args2 = self._base_args(2)
            args2["only"] = list(drill)
            args2["drill"] = {m: v for m, v in drill.items()}
            l2a, l2b = self._both("p2", "pass 2 (buckets of the differing partitions: %s)" % ", ".join(drill), args2)
            p2 = self._sections(drill, l2a, l2b, "pass 2")

        # ---- pass 3: columns of the buckets with equal rows but different hashes
        drill_b: Dict[str, List[List[str]]] = {}
        for n in drill:
            mr, st = results[n], state[n]
            sa, sb = secs[n]
            ba, bb = p2[n]
            st["buckets"] = sorted(diff_keyed(ba.buckets, bb.buckets), key=lambda x: (pkey(x[0][0]), _bkey(x[0][1])))
            if sa.pk == "none":
                pairs = [[k, "*"] for k, kind, _, _ in st["parts"] if kind == "hash" and k in st["drill"]]
                if not sa.has_parts and sa.table[0] == sb.table[0]:
                    pairs = [["*", "*"]]
            else:
                pairs = [[k[0], k[1]] for k, kind, _, _ in st["buckets"] if kind == "hash"]
            if pairs:
                drill_b[n] = pairs
        p3: Dict[str, Tuple[Section, Section]] = {}
        if drill_b:
            args3 = self._base_args(3)
            args3["only"] = list(drill_b)
            args3["drill_buckets"] = drill_b
            l3a, l3b = self._both("p3", "pass 3 (columns of the buckets with equal rows but different hashes: %s)" % ", ".join(drill_b), args3)
            p3 = self._sections(drill_b, l3a, l3b, "pass 3")

        # ---- assemble
        for n in names:
            mr, st = results[n], state[n]
            if mr.verdict != "diff":
                continue
            cols_by_p: Dict[str, set] = {}
            col_buckets: Dict[str, set] = {}
            if n in p3:
                ca, cb = p3[n]
                for (p, b, c), _k, _ra, _rb in diff_keyed(ca.cols, cb.cols):
                    cols_by_p.setdefault(p, set()).add(c)
                    col_buckets.setdefault(c, set()).add((p, b))
            buckets_by_p: Dict[str, List[str]] = {}
            for (p, b), _k, _ra, _rb in st.get("buckets", []):
                buckets_by_p.setdefault(p, []).append(b)
            drilled = set(st["drill"])
            for p, kind, ra, rb in st["parts"]:
                mr.parts.append(PartDiff(p, kind, ra, rb, p in drilled, buckets_by_p.get(p, []), sorted(cols_by_p.get(p, ()))))
            if not mr.partitioned:
                mr.bucket_diffs = [(b, k, ra, rb) for (p, b), k, ra, rb in st.get("buckets", [])]
                mr.table_columns = sorted(col_buckets)
                mr.column_buckets = {c: len(v) for c, v in col_buckets.items()}

        self.result.models = [results[n] for n in names]
        any_diff = any(m.is_diff for m in self.result.models)
        self.result.status = "differences" if any_diff else (
            "identical_scoped" if self.result.scope_limited else "identical")
        return self.result

    def _sections(self, wanted: Dict[str, Any], la: List[str], lb: List[str], what: str) -> Dict[str, Tuple[Section, Section]]:
        da, db = parse_sections(la), parse_sections(lb)
        missing = ["%s (side %s)" % (m, s) for m in wanted for s, d in (("A", da), ("B", db)) if m not in d]
        if missing:
            raise ParityError("%s: no output for %s" % (what, ", ".join(missing)))
        return {m: (parse_section(m, da[m]), parse_section(m, db[m])) for m in wanted}


def _struct_diff(a: List[str], b: List[str]) -> List[str]:
    out = []
    for ln in difflib.unified_diff(a, b, lineterm="", n=0):
        if ln.startswith(("---", "+++", "@@")):
            continue
        out.append(("A: " if ln[0] == "-" else "B: ") + ln[1:])
    return out


def compare(a: Side, b: Side, opts: Options, call: Caller = run_reconcile,
            progress: Progress = _noop, save: Saver = _noop) -> RunResult:
    """Run the three passes. Raises ParityError when no verdict can be reached (exit code 2)."""
    return Drill(a, b, opts, call, progress, save).run()


# ---------------------------------------------------------------------------------------------- conformance


@dataclass
class ConformanceResult:
    status: str  # identical | different | error
    rows: int = 0
    message: str = ""
    detail: List[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {"identical": 0, "different": 1}.get(self.status, 2)


def conformance(a: Side, b: Side, call: Callable[[Side], CallResult] = run_conformance,
                save: Saver = _noop) -> ConformanceResult:
    """Run the synthetic-rows macro on both sides; the outputs must be byte-identical."""
    res = [call(a), call(b)]
    for r in res:
        save("conformance_%s.txt" % r.side, "\n".join(r.lines) + "\n")
    bad = [(r, s) for r, s in zip(res, (a, b)) if r.failed]
    if bad:
        detail: List[str] = []
        msgs = []
        for r, s in bad:
            msgs.append("side %s (target %s): %s" % (r.side, s.target, r.reason()))
            detail.append("--- last log lines, side %s ---" % r.side)
            detail.extend(r.tail())
        return ConformanceResult("error", message="conformance run failed: " + "; ".join(msgs), detail=detail)
    na, nb = len(res[0].lines), len(res[1].lines)
    if na != nb:
        return ConformanceResult(
            "error", message="incomplete conformance output: %d rows on side A, %d on side B" % (na, nb))
    if res[0].lines == res[1].lines:
        return ConformanceResult("identical", rows=na)
    d = [x for x in difflib.unified_diff(res[0].lines, res[1].lines, "A", "B", lineterm="", n=0)]
    return ConformanceResult("different", rows=na, message="the hash is NOT identical", detail=d[:40])
