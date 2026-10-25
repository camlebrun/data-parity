"""End-to-end behaviour of the CLI against the fake dbt: exit codes, failure paths, scopes, files."""
import json

from conftest import base_models, mutate

RUN = ["run", "--a", "ta", "--b", "tb"]


def two_sides(env, b_models=None, a_models=None, **b_extra):
    env.world("ta", a_models)
    env.world("tb", b_models if b_models is not None else a_models, **b_extra)


def corrupted():
    def f(m):
        o = m["orders"]["rows"]
        del o[3]                      # 2025-01-02: one row missing
        o[6][2] = "99.99"             # 2025-01-03: amount changed
        o[13][3] = "zzlate"             # 2025-01-05: status changed
        m["customers"]["rows"][2][2] = "zzother"
        m["events"]["rows"][4][2] = 999
    return mutate(base_models(), f)


# ---------------------------------------------------------------------------------------------- happy paths


def test_identical_exit_0(env):
    two_sides(env)
    r = env.run(*RUN)
    assert r.rc == 0, r.err
    assert "RESULT: ISO: all 3 models identical" in r.out
    j = env.last_json()
    assert j["verdict"] == "identical" and j["scope_limited"] is False and j["exit_code"] == 0
    assert "✅" in env.last_report()
    assert len(env.call_args()) == 2  # pass 1 only, once per side
    assert env.call_args()[0]["level"] == 1


def test_differences_exit_1_and_findings(env):
    two_sides(env, corrupted())
    r = env.run(*RUN)
    assert r.rc == 1, r.err
    j = env.last_json()
    assert j["verdict"] == "differences" and j["models_differing"] == 3
    models = {m["model"]: m for m in j["models"]}
    o = models["orders"]
    assert [p["partition"] for p in o["differing_partitions"]] == ["2025-01-02", "2025-01-03", "2025-01-05"]
    kinds = {p["partition"]: p["kind"] for p in o["differing_partitions"]}
    assert kinds == {"2025-01-02": "rows", "2025-01-03": "hash", "2025-01-05": "hash"}
    cols = {p["partition"]: p["differing_columns"] for p in o["differing_partitions"]}
    assert cols["2025-01-03"] == ["amount"] and cols["2025-01-05"] == ["status"]
    assert o["partitions_total"] == 6 and o["delta_rows"] == -1
    assert [c["column"] for c in models["customers"]["columns"]] == ["city"]
    assert models["events"]["differing_partitions"][0]["differing_columns"] == ["value"]
    assert "not drilled" not in env.last_report()
    # terminal summary: short, ASCII, one block per model
    assert "orders" in r.out and "3 of 6 differ" in r.out and "amount (1), status (1)" in r.out
    r.out.encode("ascii")
    r.err.encode("ascii")


def test_calls_use_json_args_cwd_and_no_shell(env, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "dbt_project.yml").write_text("name: y\n")
    two_sides(env, corrupted())
    prof = tmp_path / "prof dir"
    prof.mkdir()
    r = env.run(*RUN, "--project", str(other), "--profiles-dir-a", str(prof))
    assert r.rc == 1, r.err
    calls = env.calls()
    assert calls and all(c["cwd"] == str(other.resolve()) or c["cwd"].endswith("other") for c in calls)
    first = calls[0]["argv"]
    assert first[:2] == ["run-operation", "data_parity_reconcile"]
    assert first.count("--args") == 1 and "--profiles-dir" in first
    json.loads(first[first.index("--args") + 1])  # one element, valid JSON
    assert str(prof.resolve()) in first


def test_models_interleaved_with_options_and_comma_lists(env):
    two_sides(env)
    r = env.run("run", "orders", "--a", "ta", "customers,events", "--b", "tb")
    assert r.rc == 0, r.err
    assert env.call_args()[0]["only"] == ["orders", "customers", "events"]


# ---------------------------------------------------------------------------------------------- H1: dbt failures


def test_bad_target_both_sides_is_exit_2(env):
    r = env.run("run", "--a", "nope1", "--b", "nope2")
    assert r.rc == 2
    assert "ISO" not in r.out.replace("RESULT: ERROR", "")
    assert "side A" in r.err and "side B" in r.err
    assert env.last_json()["verdict"] == "error"
    assert "\U0001f4a5" in env.last_report()


def test_same_dbt_failure_on_both_sides_is_not_identical(env):
    two_sides(env, fail_rc=1)
    env.world("ta", fail_rc=1)
    r = env.run(*RUN)
    assert r.rc == 2 and "identical" not in r.out.lower()


def test_empty_output_both_sides_is_exit_2(env):
    env.world("ta", empty=True)
    env.world("tb", empty=True)
    r = env.run(*RUN)
    assert r.rc == 2
    assert "no PARITY output" in r.err and "identical" not in r.out.lower()


def test_one_side_empty_is_exit_2(env):
    env.world("ta")
    env.world("tb", empty=True)
    r = env.run(*RUN)
    assert r.rc == 2 and "dbt failed on side B" in r.err and "dbt failed on side A" not in r.err


def test_one_side_bad_target(env):
    env.world("ta")
    r = env.run("run", "--a", "ta", "--b", "ghost")
    assert r.rc == 2 and "side B" in r.err


def test_dbt_binary_not_found(env):
    env.world("ta")
    env.world("tb")
    r = env.run(*RUN, "--dbt-a", "definitely-not-a-binary-xyz", "--dbt-b", "definitely-not-a-binary-xyz", fake=False)
    assert r.rc == 2 and "not found" in r.err


def test_dbt_crash_midway_is_exit_2(env):
    two_sides(env, corrupted(), crash_on="drill")
    r = env.run(*RUN)
    assert r.rc == 2
    assert "pass 2" in r.err and "boom" in r.err
    assert env.last_json()["verdict"] == "error"


def test_crash_in_pass_3(env):
    two_sides(env, corrupted(), crash_on="drill_buckets")
    r = env.run(*RUN)
    assert r.rc == 2 and "pass 3" in r.err


# ---------------------------------------------------------------------------------------------- H2: unknown models


def test_typo_model_exit_2(env):
    two_sides(env)
    r = env.run("run", "ordres", *RUN[1:])
    assert r.rc == 2 and "ordres" in r.err


def test_typo_model_silently_dropped_by_dbt_is_still_exit_2(env):
    two_sides(env, ignore_unknown=True)
    env.world("ta", ignore_unknown=True)
    r = env.run("run", "orders", "ordres", *RUN[1:])
    assert r.rc == 2 and "ordres" in r.err and "missing on side A and B" in r.err
    assert "orders" not in [m["model"] for m in env.last_json()["models"]]


def test_model_absent_from_one_side_only(env):
    b = base_models()
    del b["events"]
    two_sides(env, b)
    r = env.run(*RUN)
    assert r.rc == 1
    ev = {m["model"]: m for m in env.last_json()["models"]}["events"]
    assert ev["verdict"] == "structure" and "only on side A" in ev["structure_diff"][0]
    r2 = env.run("run", "events", *RUN[1:])
    assert r2.rc == 2 and "events" in r2.err


# ---------------------------------------------------------------------------------------------- structure


def test_structure_difference_skips_deeper_passes(env):
    def f(m):
        m["orders"]["cols"].append("extra")
        for r in m["orders"]["rows"]:
            r.append("x")
    two_sides(env, mutate(base_models(), f))
    r = env.run("run", "orders", *RUN[1:])
    assert r.rc == 1
    assert [a.get("level") for a in env.call_args()] == [1, 1]
    o = env.last_json()["models"][0]
    assert o["verdict"] == "structure" and any("extra" in x for x in o["structure_diff"])
    assert "structure" in r.out


# ---------------------------------------------------------------------------------------------- M1 / M2 scopes


def many_diffs():
    def f(m):
        for i in range(0, 15, 3):          # a change in 5 of 6 days
            m["orders"]["rows"][i][2] = "7.77"
    return mutate(base_models(), f)


def test_drill_limit_spreads_the_sample(env):
    two_sides(env, many_diffs())
    r = env.run("run", "orders", *RUN[1:], "--drill-limit", "2")
    assert r.rc == 1
    j = env.last_json()
    o = j["models"][0]
    assert len(o["differing_partitions"]) == 5 and o["partitions_not_drilled"] == 3
    assert [p["drilled"] for p in o["differing_partitions"]] == [True, False, False, False, True]
    assert j["scope_limited"] is True
    md = env.last_report()
    assert "column attribution is based on a sample of 2 of 5 differing days, spread evenly from 2025-01-01 to 2025-01-05" in md
    assert "not sampled" in md and "--drill-limit 2" in md and "sampled" in r.out
    assert [a for a in env.call_args() if a and "drill" in a][0]["drill"] == {"orders": ["2025-01-01", "2025-01-05"]}


def test_last_scope_limited_identical(env):
    def f(m):
        m["orders"]["rows"][0][2] = "5.55"      # difference in the OLDEST day only
    two_sides(env, mutate(base_models(), f))
    full = env.run(*RUN)
    assert full.rc == 1
    env.log.unlink()
    r = env.run(*RUN, "--last", "2")
    assert r.rc == 0, r.err
    assert "ISO on the checked scope (last 2 partitions)" in r.out
    assert "RESULT: ISO:" not in r.out
    j = env.last_json()
    assert j["verdict"] == "identical_scoped" and j["scope_limited"] is True and j["exit_code"] == 0
    md = env.last_report()
    assert "⚠️" in md and "ISO on the checked scope (last 2 partitions)" in md.replace("**Identical on a limited scope** (last 2 partitions)", "ISO on the checked scope (last 2 partitions)")
    assert "Older partitions" in md
    assert env.call_args()[0]["partitions"] == {"last": 2}


def test_as_of_scope(env):
    def f(m):
        m["orders"]["rows"][-1][2] = "5.55"     # difference in the LATEST day (2025-01-06)
    two_sides(env, mutate(base_models(), f))
    assert env.run(*RUN).rc == 1
    env.log.unlink()
    r = env.run(*RUN, "--as-of", "2025-01-06")
    assert r.rc == 0
    assert "ISO on the checked scope (before 2025-01-06)" in r.out
    assert env.last_json()["scope_limited"] is True
    assert env.call_args()[0]["as_of"] == "2025-01-06"


def test_last_and_as_of_text(env):
    two_sides(env)
    r = env.run(*RUN, "--last", "3", "--as-of", "2025-01-06")
    assert "(last 3 partitions / before 2025-01-06)" in r.out


def test_scope_difference_between_sides_is_structure(env):
    b = mutate(base_models(), lambda m: m["orders"]["rows"].extend([["2025-01-07", 900, "1.00", "paid"]]))
    two_sides(env, b)
    r = env.run("run", "orders", *RUN[1:], "--last", "2")
    assert r.rc == 1
    assert env.last_json()["models"][0]["verdict"] == "structure"


# ---------------------------------------------------------------------------------------------- L8 quotes


def test_partition_values_with_quotes_survive(env):
    nasty = "o'brien \"x\" \\ é"

    def mk(shift):
        m = base_models()
        m["orders"]["rows"] = [[nasty, 1, "1.00", "paid" if not shift else "late"], [nasty, 2, "2.00", "paid"]]
        return m
    env.world("ta", mk(False))
    env.world("tb", mk(True))
    r = env.run("run", "orders", *RUN[1:])
    assert r.rc == 1, r.err
    sent = [a for a in env.call_args() if a and "drill" in a]
    assert sent and sent[0]["drill"]["orders"] == [nasty]
    r.out.encode("ascii")
    assert env.last_json()["models"][0]["differing_partitions"][0]["partition"] == nasty


# ---------------------------------------------------------------------------------------------- H3 out dir


def test_out_dir_is_never_cleaned(env):
    two_sides(env)
    env.out_dir.mkdir()
    keep = env.out_dir / "important.txt"
    keep.write_text("mine")
    sub = env.out_dir / "mydir"
    sub.mkdir()
    (sub / "x.md").write_text("mine too")
    old = env.out_dir / "parity.md"
    old.write_text("an old report of the user")
    assert env.run(*RUN).rc == 0
    assert env.run(*RUN).rc == 0
    assert keep.read_text() == "mine" and (sub / "x.md").read_text() == "mine too"
    assert old.read_text() == "an old report of the user"
    dirs = [d for d in env.run_dirs() if d.name.startswith("run-")]
    assert len(dirs) == 2 and dirs[0] != dirs[1]
    assert (dirs[0] / "parity.md").exists() and (dirs[0] / "p1_A.txt").exists()


def test_explicit_report_and_json_paths(env, tmp_path):
    two_sides(env, corrupted())
    rep, js = tmp_path / "x" / "r.md", tmp_path / "x" / "r.json"
    r = env.run(*RUN, "--report", str(rep), "--json", str(js))
    assert r.rc == 1
    assert rep.read_text(encoding="utf-8").startswith("# Parity report") and json.loads(js.read_text(encoding="utf-8"))
    assert str(rep.resolve()) in r.out


# ---------------------------------------------------------------------------------------------- L6 usage


def test_missing_option_value_is_a_usage_error(env, capsys):
    from data_parity.cli import main
    assert main(["run", "--a"]) == 2
    err = capsys.readouterr().err
    assert "expected one argument" in err and "Traceback" not in err


def test_usage_errors(env, capsys):
    from data_parity.cli import main
    assert main([]) == 2
    assert main(["bogus"]) == 2
    assert main(["run", "--a", "x"]) == 2                 # no --b
    assert main(["run", "--a", "x", "--b", "y", "--last", "0"]) == 2
    assert main(["run", "--a", "x", "--b", "y", "--wat"]) == 2
    assert main(["--help"]) == 0 and main(["help"]) == 0
    assert "Traceback" not in capsys.readouterr().err


def test_no_dbt_project(env, tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    r = env.run(*RUN)
    assert r.rc == 2 and "dbt_project.yml" in r.err


# ---------------------------------------------------------------------------------------------- env file


def test_env_file_defaults_and_binaries_not_read(env):
    two_sides(env)
    (env.project / ".data-parity.env").write_bytes(b"A=ta\r\nB=tb\r\nDBT_A=/bin/false\r\nDBT_B=/bin/false")
    r = env.run("run")
    assert r.rc == 0, r.err
    assert "DBT_A ignored" in r.err
    cmd = env.last_json()["command"]
    assert "--a ta" in cmd and "--b tb" in cmd


# ---------------------------------------------------------------------------------------------- conformance


def test_conformance_identical(env):
    env.world("ta")
    env.world("tb")
    r = env.run("conformance", "--a", "ta", "--b", "tb")
    assert r.rc == 0 and "IDENTICAL" in r.out and "(2 rows)" in r.out


def test_conformance_different(env):
    env.world("ta")
    env.world("tb", conformance=["0|s=a|h1=1|h2=2", "1|s=b|h1=3|h2=999"])
    r = env.run("conformance", "--a", "ta", "--b", "tb")
    assert r.rc == 1 and "DIFFERENT" in r.out and "999" in r.out


def test_conformance_failures_are_exit_2(env):
    env.world("ta")
    env.world("tb", empty=True)
    assert env.run("conformance", "--a", "ta", "--b", "tb").rc == 2
    env.world("tb", fail_rc=1)
    assert env.run("conformance", "--a", "ta", "--b", "tb").rc == 2
    assert env.run("conformance", "--a", "ta", "--b", "ghost").rc == 2
    env.world("tb", conformance=["0|s=a|h1=1|h2=2"])      # truncated output on one side
    r = env.run("conformance", "--a", "ta", "--b", "tb")
    assert r.rc == 2 and "incomplete" in r.err


# ---------------------------------------------------------------------------------------------- report


def test_report_structure_and_privacy(env):
    two_sides(env, corrupted())
    env.run(*RUN, "--last", "6")
    md = env.last_report()
    for needle in ("# Parity report", "## Not checked", "## Overview", "## Differences", "### ❌ `orders`",
                   "**Differing columns**", "**Timeline**", "## How to reproduce", "data-parity run",
                   "meta_loaded_at", "❌", "| model | verdict |"):
        assert needle in md, needle
    assert "alues changed in 2 of 6 days" in md
    assert "<details>" not in md                      # only 3 differing partitions: nothing to collapse
    for forbidden in ("99.99", "zzlate", "city2", "zzother"):   # row values must never appear
        assert forbidden not in md
    md.encode("utf-8")


def test_report_inline_list_is_bounded(env):
    two_sides(env, many_diffs())
    env.run("run", "orders", *RUN[1:], "--detail", "2")
    md = env.last_report()
    assert "<details>" not in md and "2 of 5 differing days, spread evenly from first to last" in md
    assert "parity.json" not in md      # 5 differing days <= 15: nothing is left out, no pointer needed
    env.run("run", "orders", *RUN[1:], "--detail", "all")
    md = env.last_report()
    assert "<details>" not in md and "**Differing days**" in md      # only 5 differing days: all listed


def test_ascii_terminal_with_unicode_names(env):
    def f(m):
        m["café"] = m.pop("events")
    two_sides(env, mutate(base_models(), f))
    r = env.run(*RUN)
    r.out.encode("ascii")
    r.err.encode("ascii")
