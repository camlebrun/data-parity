"""A fake dbt used by the tests: it answers `run-operation data_parity_reconcile|data_parity_conformance`.

World files: $FAKE_DBT_WORLDS/<target>.json (a missing file = unknown target = dbt failure). Format:
  {"models": {name: {"pk": [..], "partitioned": true, "cols": [..], "excluded": "meta_x",
                     "rows": [[partition, v1, v2, ...], ...]}},
   "fail_rc": N, "empty": true, "crash_on": "drill"|"drill_buckets", "ignore_unknown": true,
   "conformance": [line, ...]}
Every call is appended to $FAKE_DBT_LOG (one JSON object per line) when set.
Output lines carry ANSI codes, a timestamp prefix and CRLF endings on purpose.
"""
import json
import os
import sys
import zlib


def h(values):
    s = repr(list(values))
    return zlib.crc32(s.encode()) & 0xFFFFFFFF, zlib.crc32((s + "#").encode()) & 0xFFFFFFFF


def emit(lines):
    out = sys.stdout.buffer
    for i, ln in enumerate(lines):
        out.write(("\x1b[0m12:00:%02d  " % (i % 60) + ln + "\x1b[0m\r\n").encode("utf-8"))
    out.flush()


def main(argv):
    log = os.environ.get("FAKE_DBT_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cwd": os.getcwd(), "argv": argv}) + "\n")
    if len(argv) < 2 or argv[0] != "run-operation":
        print("fake dbt: unsupported command", argv)
        return 2
    macro = argv[1]
    target = argv[argv.index("--target") + 1] if "--target" in argv else None
    args = json.loads(argv[argv.index("--args") + 1]) if "--args" in argv else {}
    path = os.path.join(os.environ["FAKE_DBT_WORLDS"], "%s.json" % target)
    if not os.path.exists(path):
        print("Runtime Error: Could not find target '%s' in profiles.yml" % target)
        return 2
    with open(path, encoding="utf-8") as fh:
        world = json.load(fh)
    if world.get("fail_rc"):
        print("Database Error: connection refused")
        return world["fail_rc"]
    if world.get("empty"):
        print("Nothing to see; the macro printed nothing")
        return 0
    if macro == "data_parity_conformance":
        emit(["PARITY|" + x for x in world.get("conformance", ["0|s=a|h1=1|h2=2", "1|s=b|h1=3|h2=4"])])
        return 0
    if macro != "data_parity_reconcile":
        print("fake dbt: unknown macro", macro)
        return 2

    models = world["models"]
    only = args.get("only")
    if only:
        unknown = [m for m in only if m not in models]
        if unknown and not world.get("ignore_unknown"):
            print("Compilation Error: data_parity_reconcile: unknown model(s) %s" % unknown)
            return 1
        names = [m for m in sorted(models) if m in only]
    else:
        names = sorted(models)
    nb = args.get("buckets", 256)
    drill = args.get("drill")
    drill_b = args.get("drill_buckets")
    if drill and world.get("crash_on") == "drill":
        print("Database Error: boom during pass 2")
        return 1
    if drill_b and world.get("crash_on") == "drill_buckets":
        print("Database Error: boom during pass 3")
        return 1
    as_of = args.get("as_of")
    last = (args.get("partitions") or {}).get("last")

    lines = []
    notes = []
    for name in names:
        m = models[name]
        cols, pk, part = m["cols"], m.get("pk", []), m.get("partitioned", True)
        rows = list(m["rows"])
        if part and as_of:
            rows = [r for r in rows if r[0] is not None and r[0] < as_of]
        allp = sorted({r[0] for r in rows if r[0] is not None}) if part else []
        chosen = allp
        if part and last:
            chosen = allp[-last:]
            rows = [r for r in rows if r[0] in chosen]

        def rowhash(r):
            return h(r[1:])

        def bucket(r, cols=cols, pk=pk):
            pkv = [r[1 + cols.index(c)] for c in pk] if pk else r[1:]
            return h(pkv)[0] % nb

        lines.append("## " + name)
        if not drill and not drill_b:
            lines.append("cols|" + ",".join("%s:str" % c for c in cols))
            lines.append("pk|" + (",".join(pk) if pk else "none"))
            if as_of and part:
                lines.append("as_of|" + as_of)
            if part and last:
                lines.append("scope|last=%d|checked=%d|total=%d|from=%s|to=%s" % (last, len(chosen), len(allp), chosen[0], chosen[-1]))
            if not part:
                lines.append("note|no partition column: table line only, buckets are taken table-wide (partition value *)")
            hs = [rowhash(r) for r in rows]
            lines.append("table|rows=%d|h1=%d|h2=%d" % (len(rows), sum(x[0] for x in hs), sum(x[1] for x in hs)))
            if part:
                for p in chosen:
                    pr = [rowhash(r) for r in rows if r[0] == p]
                    lines.append("part|%s|rows=%d|h1=%d|h2=%d" % (p, len(pr), sum(x[0] for x in pr), sum(x[1] for x in pr)))
            if m.get("excluded"):
                notes.append("NOTE|%s|excluded=%s" % (name, m["excluded"]))
        if drill and name in drill:
            lines.append("buckets|%d" % nb)
            for p in drill[name]:
                grp = {}
                for r in rows:
                    if p == "*" or r[0] == p:
                        grp.setdefault(bucket(r), []).append(rowhash(r))
                for b in sorted(grp):
                    lines.append("bucket|%s|%d|rows=%d|h1=%d|h2=%d" % (p, b, len(grp[b]), sum(x[0] for x in grp[b]), sum(x[1] for x in grp[b])))
        if drill_b and name in drill_b:
            lines.append("buckets|%d" % nb)
            for p, b in drill_b[name]:
                sel = [r for r in rows if (p == "*" or r[0] == p) and (b == "*" or str(bucket(r)) == str(b))]
                for c in cols:
                    if c in pk:
                        continue
                    ci = 1 + cols.index(c)
                    hs = [h([r[1 + cols.index(k)] for k in pk] + [r[ci]]) for r in sel]
                    lines.append("col|%s|%s|%s|rows=%d|h1=%d|h2=%d" % (p, b, c, len(sel), sum(x[0] for x in hs), sum(x[1] for x in hs)))
    emit(["PARITY|" + x for x in lines])
    for n in notes:
        sys.stdout.write(n + "\r\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
