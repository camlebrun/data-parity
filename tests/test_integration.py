"""Runs the real dbt against integration_tests/ (DuckDB). Skipped unless DBT_FUSION or DBT_CORE is set."""
import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from data_parity.cli import main

ROOT = Path(__file__).resolve().parent.parent
IT = ROOT / "integration_tests"
BIN = os.environ.get("DBT_FUSION") or os.environ.get("DBT_CORE")

HAS_MACRO = (ROOT / "dbt_package" / "macros" / "reconcile.sql").is_file()

pytestmark = [
    pytest.mark.skipif(not BIN, reason="set DBT_FUSION or DBT_CORE to run against real dbt"),
    pytest.mark.skipif(not HAS_MACRO, reason="dbt_package/macros/reconcile.sql is not in this checkout"),
]


def _cli(*argv):
    o, e = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
        rc = main(list(argv))
    return rc, o.getvalue(), e.getvalue()


@pytest.fixture(scope="module")
def built():
    for t in ("engine_a", "engine_b"):
        subprocess.run([BIN, "run", "--target", t], cwd=str(IT), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_expected_findings(built, tmp_path):
    js = tmp_path / "r.json"
    rc, out, err = _cli("run", "--project", str(IT), "--a", "engine_a", "--b", "engine_b", "--dbt-a", BIN, "--dbt-b", BIN,
                        "--out", str(tmp_path), "--json", str(js))
    assert rc == 1, err
    j = json.loads(js.read_text(encoding="utf-8"))
    m = {x["model"]: x for x in j["models"]}
    assert [p["partition"] for p in m["orders"]["differing_partitions"]] == [
        "2025-01-11", "2025-01-21", "2025-01-31", "2025-02-20", "2025-03-02"]
    assert m["orders"]["partitions_total"] == 120
    named = {c["column"] for x in j["models"] for c in x["columns"]}
    assert "meta_loaded_at" not in named and named == {"amount", "price_float", "status", "city", "value"}
    assert [c["column"] for c in m["customers"]["columns"]] == ["city"]
    assert [p["partition"] for p in m["events_nopk"]["differing_partitions"]] == ["2025-01-06"]
    assert m["events_nopk"]["differing_partitions"][0]["differing_columns"] == ["value"]


def test_engine_a_vs_itself_is_identical(built, tmp_path):
    rc, out, err = _cli("run", "--project", str(IT), "--a", "engine_a", "--b", "engine_a", "--dbt-a", BIN, "--dbt-b", BIN,
                        "--out", str(tmp_path))
    assert rc == 0, err


# ---------------------------------------------------------------------------------------------- single-engine projects
# Plain dbt projects: no target.type branch, no dispatch, no shared wiring. Generated in a temporary directory.

PKG = ROOT / "dbt_package"


def _project(root, name, models, targets, db):
    """Write a dbt project ``name`` under ``root`` (models = {file stem: sql}, targets = {target: schema})."""
    d = root / name
    (d / "models").mkdir(parents=True)
    (d / "dbt_project.yml").write_text(
        'name: %s\nprofile: %s\nversion: "0.1"\nconfig-version: 2\nmodel-paths: ["models"]\n'
        "models:\n  %s:\n    +materialized: table\n" % (name, name, name), encoding="utf-8")
    (d / "packages.yml").write_text("packages:\n  - local: %s\n" % PKG, encoding="utf-8")
    outputs = "".join("    %s:\n      type: duckdb\n      path: %s\n      schema: %s\n" % (t, db, s) for t, s in targets.items())
    (d / "profiles.yml").write_text("%s:\n  target: %s\n  outputs:\n%s" % (name, next(iter(targets)), outputs), encoding="utf-8")
    for stem, sql in models.items():
        (d / "models" / (stem + ".sql")).write_text(sql, encoding="utf-8")
    return d


def _dbt(project, *args):
    subprocess.run([BIN, *args, "--profiles-dir", str(project)], cwd=str(project), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _compare(tmp_path, *argv):
    js = tmp_path / "r.json"
    rc, out, err = _cli("run", *argv, "--dbt-a", BIN, "--dbt-b", BIN, "--out", str(tmp_path / "out"), "--json", str(js))
    return rc, out, err, json.loads(js.read_text(encoding="utf-8")) if js.is_file() else None


TAGGED = "{{ config(tags=['data_parity']) }}\n"
ORDERS_REPO = TAGGED + "select 1 as order_id, cast(10.50 as decimal(10,2)) as amount, 'paid' as status\nunion all select 2, 20.00, 'open'\n"
CUSTOMERS_REPO = TAGGED + "select 1 as customer_id, 'Paris' as city\n"
# the legacy table: separator `/` instead of `_`, one meta_ column (excluded by default), one extra column
ORDERS_LEGACY = ('select 1 as "order/id", cast(10.50 as decimal(10,2)) as amount, \'paid\' as status, 1 as loaded_at, 9 as meta_run\n'
                 "union all select 2, 20.00, 'open', 1, 9\n")


@pytest.fixture(scope="module")
def one_repo(tmp_path_factory):
    """ONE repo run on two targets: `dev` reads its own tables, `legacy` reads the tables of another schema."""
    root = tmp_path_factory.mktemp("single")
    db = str(root / "shared.duckdb")
    legacy = _project(root, "legacy_sim", {"orders": ORDERS_LEGACY}, {"legacy": "legacy_sch"}, db)
    repo = _project(root, "repo", {"orders": ORDERS_REPO, "customers": CUSTOMERS_REPO}, {"dev": "repo_sch", "legacy": "unused_sch"}, db)
    for p in (legacy, repo):
        _dbt(p, "deps")
    _dbt(legacy, "run", "--target", "legacy")
    _dbt(repo, "run", "--target", "dev")
    return repo


def _one_repo_args(repo, *extra):
    return ["--project", str(repo), "--a", "dev", "--b", "legacy", "--profiles-dir-a", str(repo), "--profiles-dir-b", str(repo),
            "--schema-b", "legacy_sch", *extra]


def test_one_repo_other_schema_reports_the_findings(one_repo, tmp_path):
    rc, out, err, j = _compare(tmp_path, *_one_repo_args(one_repo, "--label-a", "bq", "--label-b", "trino"))
    assert rc == 1, err
    assert j["only_in_a"] == ["customers"] and j["only_in_b"] == []
    o = {m["model"]: m for m in j["models"]}["orders"]
    # `order/id` is `order_id`, `meta_run` is excluded by default; only the extra `loaded_at` makes the schemas differ
    assert j["schema_mismatch"] == ["orders"] and o["columns_only_b"] == ["loaded_at"] and o["columns_only_a"] == []
    assert "only in the target side bq (no legacy counterpart)" in out


def test_one_repo_excluded_column_makes_it_identical_and_unpartitioned(one_repo, tmp_path):
    rc, out, err, j = _compare(tmp_path, *_one_repo_args(one_repo, "orders", "--exclude-column", "orders.loaded_at"))
    assert rc == 0, err
    o = j["models"][0]
    assert o["model"] == "orders" and o["verdict"] == "iso" and o["partitioned"] is False and o["rows_a"] == o["rows_b"] == 2


def test_one_repo_difference_in_a_value_is_found(one_repo, tmp_path):
    legacy = one_repo.parent / "legacy_sim"
    (legacy / "models" / "orders.sql").write_text(ORDERS_LEGACY.replace("'paid'", "'PAID'"), encoding="utf-8")
    try:
        _dbt(legacy, "run", "--target", "legacy")
        rc, out, err, j = _compare(tmp_path, *_one_repo_args(one_repo, "orders", "--exclude-column", "loaded_at"))
        assert rc == 1, err
        o = j["models"][0]
        assert o["verdict"] == "diff" and o["rows_a"] == o["rows_b"] and [c["column"] for c in o["columns"]] == ["status"]
    finally:
        (legacy / "models" / "orders.sql").write_text(ORDERS_LEGACY, encoding="utf-8")
        _dbt(legacy, "run", "--target", "legacy")


def test_one_repo_mapping_file_wins_over_the_namespace(one_repo, tmp_path):
    mp = tmp_path / "b.yml"
    mp.write_text("orders: legacy_sch.orders\n", encoding="utf-8")
    argv = _one_repo_args(one_repo, "orders", "--exclude-column", "loaded_at")
    argv[argv.index("--schema-b") + 1] = "nowhere"           # wrong namespace, the mapping entry is used instead
    rc, out, err, j = _compare(tmp_path, *argv, "--mapping-b", str(mp))
    assert rc == 0, err


def test_one_repo_unknown_schema_is_a_clear_error(one_repo, tmp_path):
    argv = _one_repo_args(one_repo, "orders")
    argv[argv.index("--schema-b") + 1] = "nowhere"
    rc, out, err, j = _compare(tmp_path, *argv)
    assert rc == 2 and "no table found on side B" in err and "nowhere" in err and "--schema-b" in err


@pytest.fixture(scope="module")
def two_projects(tmp_path_factory):
    """Two independent projects, no engine-specific Jinja: renamed model (compare_as), one model on each side only."""
    root = tmp_path_factory.mktemp("two")
    a = _project(root, "proj_a", {"orders": ORDERS_REPO, "customers": CUSTOMERS_REPO}, {"dev": "main"}, str(root / "a.duckdb"))
    renamed = ("{{ config(tags=['data_parity'], meta={'data_parity': {'compare_as': 'orders'}}) }}\n"
               "select 1 as order_id, cast(10.50 as decimal(10,2)) as amount, 'paid' as status\nunion all select 2, 20.00, 'open'\n")
    b = _project(root, "proj_b", {"orders_dpv": renamed, "refunds": TAGGED + "select 1 as refund_id\n"}, {"dev": "main"}, str(root / "b.duckdb"))
    dup = _project(root, "proj_dup", {
        "x": "{{ config(tags=['data_parity'], meta={'data_parity': {'compare_as': 'k'}}) }}\nselect 1 as id\n",
        "y": "{{ config(tags=['data_parity'], meta={'data_parity': {'compare_as': 'k'}}) }}\nselect 1 as id\n"}, {"dev": "main"}, str(root / "d.duckdb"))
    for p in (a, b, dup):
        _dbt(p, "deps")
        _dbt(p, "run")
    return a, b, dup


def test_two_independent_projects_match_by_compare_as(two_projects, tmp_path):
    a, b, _ = two_projects
    rc, out, err, j = _compare(tmp_path, "--project", str(a), "--project-b", str(b), "--a", "dev", "--b", "dev",
                               "--profiles-dir-a", str(a), "--profiles-dir-b", str(b), "--label-a", "bq", "--label-b", "trino")
    assert rc == 1, err
    m = {x["model"]: x for x in j["models"]}
    assert m["orders"]["verdict"] == "iso" and "orders_dpv" not in m
    assert j["only_in_a"] == ["customers"] and j["only_in_b"] == ["refunds"]
    assert "only in the legacy side trino (not migrated yet)" in out


def test_duplicate_match_key_on_one_side_names_both_models(two_projects, tmp_path):
    a, _, dup = two_projects
    rc, out, err, j = _compare(tmp_path, "--project", str(a), "--project-b", str(dup), "--a", "dev", "--b", "dev",
                               "--profiles-dir-a", str(a), "--profiles-dir-b", str(dup))
    assert rc == 2 and "side B" in err and "'x'" in err and "'y'" in err and "'k'" in err


def test_project_without_the_package_names_the_side_and_the_fix(tmp_path_factory, tmp_path):
    bare = tmp_path_factory.mktemp("bare") / "bare"
    (bare / "models").mkdir(parents=True)
    (bare / "dbt_project.yml").write_text('name: bare\nprofile: bare\nversion: "0.1"\nconfig-version: 2\nmodel-paths: ["models"]\n', encoding="utf-8")
    (bare / "profiles.yml").write_text("bare:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n      path: %s\n" % (bare / "x.duckdb"), encoding="utf-8")
    (bare / "models" / "m.sql").write_text("select 1 as id\n", encoding="utf-8")
    rc, out, err, j = _compare(tmp_path, "--project", str(bare), "--a", "dev", "--b", "dev",
                               "--profiles-dir-a", str(bare), "--profiles-dir-b", str(bare))
    assert rc == 2 and "dbt failed on side A" in err and "data_parity package is not installed" in err and "dbt deps" in err


def test_one_repo_no_default_exclude_compares_the_meta_columns(one_repo, tmp_path):
    rc, out, err, j = _compare(tmp_path, *_one_repo_args(one_repo, "orders", "--exclude-column", "loaded_at", "--no-default-exclude"))
    assert rc == 1, err
    o = j["models"][0]
    assert o["structure_kind"] == "schema" and o["columns_only_b"] == ["meta_run"]


def test_columns_that_collide_once_normalized_are_an_error(tmp_path_factory, tmp_path):
    root = tmp_path_factory.mktemp("collide")
    sql = TAGGED + 'select 1 as "a/b", 2 as a_b\n'
    p = _project(root, "collide", {"t": sql}, {"dev": "main"}, str(root / "c.duckdb"))
    _dbt(p, "deps")
    _dbt(p, "run")
    rc, out, err, j = _compare(tmp_path, "--project", str(p), "--a", "dev", "--b", "dev",
                               "--profiles-dir-a", str(p), "--profiles-dir-b", str(p))
    assert rc == 2 and "a/b" in err and "a_b" in err and "same column once normalized" in err
