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

pytestmark = pytest.mark.skipif(not BIN, reason="set DBT_FUSION or DBT_CORE to run against real dbt")


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
