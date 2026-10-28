"""Command line: ``data-parity run | report | conformance | help``.

Exit codes: 0 identical, 1 differences, 2 error (any dbt failure, unknown model, usage error...).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path
from typing import List, Optional

from . import __version__
from .drill import Options, ParityError, RunResult, compare, conformance, parse_section, parse_sections, pkey
from .envfile import load_env_file
from .report import (ascii_safe, join_command, render_json, render_markdown,
                     terminal_summary)
from .runner import Side

ENV_FILE = ".data-parity.env"

TOP_HELP = """\
usage: data-parity <command> [options]

Check that dbt models are identical on two engines, without moving data.

commands:
  run [MODEL ...]   compare the models (every model tagged data_parity if none is given)
  report RUN_DIR    re-render parity.md from a saved run directory (no dbt call)
  conformance       check that the row hash is portable between the two engines
  help              show this help (also: data-parity <command> --help)

common options:
  --a TARGET --b TARGET     dbt targets of side A and side B
  --dbt-a BIN --dbt-b BIN   dbt binary per side (default: dbt)
  --project DIR             dbt project directory (default: current directory)
  --project-b DIR           project directory of side B, if different
  --profiles-dir-a/-b DIR   dbt profiles directory per side

run options:
  --as-of YYYY-MM-DD        exclude partitions on or after this date (freshness gap is not a content gap)
  --last N                  only the N latest partitions of each model (bounds the scan cost)
  --buckets N               hash buckets per partition (default 256)
  --drill-limit N           max partitions drilled per model (default 200); above it, the drilled
                            partitions are spread evenly from the first to the last differing one
  --detail N                partitions listed inline in the report (default and maximum 15; the
                            complete list is in parity.json)
  --out DIR                 work directory; a fresh run-<timestamp> sub-directory is created inside
  --report FILE             Markdown report path (default: <out>/run-<timestamp>/parity.md);
                            parity.json stays in the run directory
  --json FILE               also write a machine-readable result

report options:
  data-parity report RUN_DIR [--out FILE]   FILE defaults to RUN_DIR/parity.md

Defaults can be stored in ./.data-parity.env (keys A, B and PROJECT_B only):
  A=dev
  B=local

Exit code: 0 identical, 1 differences, 2 error.
"""


def _pos_int(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a positive integer, got %r" % text)
    if v < 1:
        raise argparse.ArgumentTypeError("expected a positive integer, got %r" % text)
    return v


def _detail(text: str) -> Optional[int]:
    return None if text.lower() == "all" else _pos_int(text)


def _as_of(text: str) -> str:
    if not text or "'" in text or '"' in text or "\\" in text:
        raise argparse.ArgumentTypeError("invalid date %r (expected YYYY-MM-DD)" % text)
    return text


def _namespace(text: str) -> str:
    # same rule as the macros (data_parity_rc_check_part): [database.]schema, no quote / backslash / space
    parts = text.strip().split(".")
    if not 1 <= len(parts) <= 2 or not all(re.match(r"^[A-Za-z0-9_-]+$", x) for x in parts):
        raise argparse.ArgumentTypeError("expected [database.]schema (letters, digits, _ and -), got %r" % text)
    return text.strip()


def _exclude_column(text: str) -> str:
    v = text.strip()
    if not v or v.startswith(".") or v.endswith(".") or "'" in v or '"' in v or "\\" in v:
        raise argparse.ArgumentTypeError("expected COLUMN or MODEL.COLUMN, got %r" % text)
    return v


def _exclude_prefix(text: str) -> str:
    v = text.strip()
    if not v or "'" in v or '"' in v or "\\" in v:
        raise argparse.ArgumentTypeError("expected a non-empty column prefix, got %r" % text)
    return v


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--a", metavar="TARGET", help="dbt target of side A")
    p.add_argument("--b", metavar="TARGET", help="dbt target of side B")
    p.add_argument("--dbt-a", metavar="BIN", default="dbt", help="dbt binary of side A (default: dbt)")
    p.add_argument("--dbt-b", metavar="BIN", default="dbt", help="dbt binary of side B (default: dbt)")
    p.add_argument("--project", metavar="DIR", help="dbt project directory (default: current directory)")
    p.add_argument("--project-b", metavar="DIR", help="dbt project directory of side B (default: same as --project)")
    p.add_argument("--profiles-dir-a", metavar="DIR", help="dbt profiles directory of side A")
    p.add_argument("--profiles-dir-b", metavar="DIR", help="dbt profiles directory of side B")
    p.add_argument("--schema-a", type=_namespace, metavar="[DB.]SCHEMA",
                   help="read the tables of side A in this schema instead of the project's own ref() (a mapping entry still wins)")
    p.add_argument("--schema-b", type=_namespace, metavar="[DB.]SCHEMA",
                   help="same for side B (e.g. the legacy Trino schema of a BigQuery repo)")
    p.add_argument("--out", metavar="DIR", help="work directory; a fresh run-<timestamp> sub-directory is created inside")


def build_parser(cmd: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="data-parity %s" % cmd, add_help=True, allow_abbrev=False,
                                description="Exit code: 0 identical, 1 differences, 2 error.")
    _common(p)
    if cmd == "run":
        p.add_argument("models", nargs="*", metavar="MODEL", help="models to compare (default: every tagged model)")
        p.add_argument("--as-of", type=_as_of, metavar="YYYY-MM-DD", help="exclude partitions on or after this date")
        p.add_argument("--last", type=_pos_int, metavar="N", help="only the N latest partitions of each model")
        p.add_argument("--buckets", type=_pos_int, default=256, metavar="N", help="hash buckets per partition (default 256)")
        p.add_argument("--drill-limit", type=_pos_int, default=200, metavar="N", help="max partitions drilled per model (default 200)")
        p.add_argument("--detail", type=_detail, default=15, metavar="N|all", help="partitions listed inline in the report (default and maximum 15)")
        p.add_argument("--exclude-column", type=_exclude_column, action="append", default=[], metavar="COL|MODEL.COL",
                       help="column left out of the comparison, in every model or only in MODEL (repeatable)")
        p.add_argument("--exclude-prefix", type=_exclude_prefix, action="append", default=[], metavar="PREFIX",
                       help="columns starting with PREFIX are left out (repeatable; `meta_` is always excluded unless --no-default-exclude)")
        p.add_argument("--no-default-exclude", action="store_true", help="also compare the `meta_` columns")
        p.add_argument("--report", metavar="FILE", help="Markdown report path")
        p.add_argument("--json", metavar="FILE", help="machine-readable result path")
    return p


def make_run_dir(base: Path) -> Path:
    """Create a fresh, empty sub-directory of ``base`` (never touches anything that already exists there)."""
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("run-%Y%m%d-%H%M%S")
    for i in range(1, 1000):
        d = base / (stamp if i == 1 else "%s-%d" % (stamp, i))
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise OSError("cannot create a run directory in %s" % base)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_report(report_path: Path, result: RunResult) -> None:
    """Write the Markdown report."""
    write_text(report_path, render_markdown(result))


def eprint(msg: str = "") -> None:
    sys.stderr.write(ascii_safe(msg) + "\n")


def oprint(msg: str = "") -> None:
    sys.stdout.write(ascii_safe(msg) + "\n")


def _fail(msg: str, detail: Optional[List[str]] = None) -> int:
    eprint("data-parity: error: " + msg)
    for ln in detail or []:
        eprint("  " + ln)
    return 2


def _resolve(args: argparse.Namespace, parser: argparse.ArgumentParser, cmd: str):
    env, ignored = load_env_file(Path.cwd() / ENV_FILE)
    for k in ignored:
        eprint("data-parity: %s: key %s ignored (only A, B and PROJECT_B are read)" % (ENV_FILE, k))
    a = args.a or env.get("A")
    b = args.b or env.get("B")
    if not a or not b:
        parser.error("give --a and --b (dbt targets), or put A= and B= in ./%s" % ENV_FILE)
    project = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    pb_raw = args.project_b or env.get("PROJECT_B")
    project_b = Path(pb_raw).resolve() if pb_raw else project
    for d in (project, project_b):
        if not (d / "dbt_project.yml").is_file():
            raise ParityError("no dbt_project.yml in %s (run from your dbt project or use --project)" % d)
    prof_a = Path(args.profiles_dir_a).resolve() if args.profiles_dir_a else None
    prof_b = Path(args.profiles_dir_b).resolve() if args.profiles_dir_b else None
    sa = Side("A", a, a, args.dbt_a, project, prof_a, args.schema_a)
    sb = Side("B", b, b, args.dbt_b, project_b, prof_b, args.schema_b)
    base = Path(args.out).resolve() if args.out else Path(tempfile.gettempdir()) / "data-parity" / project.name
    return sa, sb, base


def _command_line(cmd: str, args: argparse.Namespace, models: List[str]) -> str:
    argv = ["data-parity", cmd] + models + ["--a", args.a or "", "--b", args.b or ""]
    pairs = [("--dbt-a", args.dbt_a, "dbt"), ("--dbt-b", args.dbt_b, "dbt"), ("--project", args.project, None),
             ("--project-b", args.project_b, None), ("--profiles-dir-a", args.profiles_dir_a, None),
             ("--profiles-dir-b", args.profiles_dir_b, None), ("--schema-a", args.schema_a, None),
             ("--schema-b", args.schema_b, None)]
    if cmd == "run":
        pairs += [("--as-of", args.as_of, None), ("--last", args.last, None), ("--buckets", args.buckets, 256),
                  ("--drill-limit", args.drill_limit, 200), ("--detail", "all" if args.detail is None else args.detail, 15)]
    for flag, val, default in pairs:
        if val is not None and val != default:
            argv += [flag, str(val)]
    if cmd == "run":
        for col in args.exclude_column:
            argv += ["--exclude-column", col]
        for pre in args.exclude_prefix:
            argv += ["--exclude-prefix", pre]
        if args.no_default_exclude:
            argv.append("--no-default-exclude")
    return join_command(argv)


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        sa, sb, base = _resolve(args, parser, "run")
    except ParityError as exc:
        return _fail(exc.message)
    models: List[str] = []
    for m in args.models:
        for piece in m.split(","):
            piece = piece.strip()
            if piece and piece not in models:
                models.append(piece)
    args.a, args.b = sa.target, sb.target
    opts = Options(models=models, buckets=args.buckets, as_of=args.as_of, last=args.last,
                   drill_limit=args.drill_limit, detail=args.detail, exclude_columns=list(args.exclude_column),
                   exclude_prefixes=list(args.exclude_prefix), no_default_exclude=args.no_default_exclude)
    try:
        run_dir = make_run_dir(base)
    except OSError as exc:
        return _fail("cannot create a work directory in %s: %s" % (base, exc))

    def save(name: str, text: str) -> None:
        write_text(run_dir / name, text)

    def progress(msg: str) -> None:
        eprint("data-parity: " + msg)

    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    command = _command_line("run", args, models)
    try:
        result = compare(sa, sb, opts, progress=progress, save=save)
    except ParityError as exc:
        result = RunResult(status="error", label_a=sa.label, label_b=sb.label, target_a=sa.target,
                           target_b=sb.target, options=opts, errors=[exc.message], error_detail=exc.detail,
                           requested=models)
    result.started = started
    result.command = command

    report_path = Path(args.report).resolve() if args.report else run_dir / "parity.md"
    try:
        write_report(report_path, result)
        if args.json:
            write_text(Path(args.json).resolve(), render_json(result))
        else:
            write_text(run_dir / "parity.json", render_json(result))
    except OSError as exc:
        return _fail("cannot write the report: %s" % exc)

    if result.status == "error":
        eprint("data-parity: error: " + result.errors[0])
        for ln in result.error_detail:
            eprint("  " + ln)
        eprint("data-parity: work files: %s" % run_dir)
        oprint("RESULT: " + result.verdict_text)
        oprint("report: %s" % report_path)
        return 2

    for ln in terminal_summary(result):
        oprint(ln)
    oprint("")
    oprint("RESULT: " + result.verdict_text)
    if result.status == "identical_scoped":
        oprint("scope_limited: the verdict covers the checked scope only (%s)" % opts.scope_text)
    oprint("report: %s" % report_path)
    return result.exit_code


def _report(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="data-parity report", allow_abbrev=False,
                                description="Re-render parity.md from a saved run directory, without dbt.")
    p.add_argument("run_dir", metavar="RUN_DIR", help="a run directory (parity.json, p1_A.txt, p1_B.txt...)")
    p.add_argument("--out", metavar="FILE", help="Markdown report path (default: RUN_DIR/parity.md)")
    args = p.parse_args(argv)
    d = Path(args.run_dir)
    need = ["parity.json", "p1_A.txt", "p1_B.txt"]
    missing = [n for n in need if not (d / n).is_file()]
    if not d.is_dir() or missing:
        return _fail("%s is not a usable run directory (missing: %s)" % (d, ", ".join(missing) if d.is_dir() else "the directory itself"))
    try:
        data = json.loads((d / "parity.json").read_text(encoding="utf-8"))
        raw = [parse_sections((d / ("p1_%s.txt" % s)).read_text(encoding="utf-8").splitlines()) for s in "AB"]
        parts = {}
        for name in set(raw[0]) | set(raw[1]):
            keys = set()
            for r in raw:
                if name in r:
                    keys |= set(parse_section(name, r[name]).parts)
            parts[name] = sorted(keys, key=pkey)
        result = RunResult.from_dict(data, parts)
    except (ValueError, KeyError, ParityError) as exc:
        return _fail("cannot read the run directory %s: %s" % (d, getattr(exc, "message", exc)))
    out = Path(args.out).resolve() if args.out else d.resolve() / "parity.md"
    try:
        write_report(out, result)
    except OSError as exc:
        return _fail("cannot write the report: %s" % exc)
    oprint("report: %s" % out)
    return 0


def _conformance(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        sa, sb, base = _resolve(args, parser, "conformance")
        run_dir = make_run_dir(base)
    except ParityError as exc:
        return _fail(exc.message)
    except OSError as exc:
        return _fail("cannot create a work directory: %s" % exc)
    res = conformance(sa, sb, save=lambda name, text: write_text(run_dir / name, text))
    if res.status == "identical":
        oprint("IDENTICAL: the hash is portable between %s and %s (%d rows)" % (sa.label, sb.label, res.rows))
    elif res.status == "different":
        oprint("DIFFERENT: the hash is NOT identical between %s and %s" % (sa.label, sb.label))
        for ln in res.detail:
            oprint(ln)
        oprint("work files: %s" % run_dir)
    else:
        _fail(res.message, res.detail)
        eprint("data-parity: work files: %s" % run_dir)
    return res.exit_code


def _main(argv: List[str]) -> int:
    if not argv:
        sys.stderr.write(TOP_HELP)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd in ("help", "-h", "--help"):
        sys.stdout.write(TOP_HELP)
        return 0
    if cmd in ("--version", "-V", "version"):
        oprint("data-parity %s" % __version__)
        return 0
    if cmd == "report":
        return _report(rest)
    if cmd not in ("run", "conformance"):
        return _fail("unknown command %r (run | report | conformance | help)" % cmd)
    parser = build_parser(cmd)
    # models and options may be interleaved (data-parity run orders --a x customers --b y)
    args = parser.parse_intermixed_args(rest) if cmd == "run" else parser.parse_args(rest)
    return _run(args, parser) if cmd == "run" else _conformance(args, parser)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        return _main(argv)
    except SystemExit as exc:  # argparse: usage errors exit 2, --help exits 0
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    except KeyboardInterrupt:
        eprint("data-parity: interrupted")
        return 2
    except Exception as exc:  # never a bare traceback for the user
        eprint("data-parity: internal error: %s: %s" % (type(exc).__name__, exc))
        if os.environ.get("DATA_PARITY_DEBUG"):
            traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
