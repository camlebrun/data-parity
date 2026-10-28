import pytest

from data_parity.drill import _cols_of, parse_section, parse_sections, ParityError, pkey
from data_parity.envfile import parse_env_text
from data_parity.report import bar, join_command, pct, quote_arg
from data_parity.mapping import MappingError, parse_mapping_text
from data_parity.runner import CallResult, classify_failure, extract_excluded, extract_parity


def test_extract_parity_tolerates_noise():
    text = (
        "\x1b[0m10:01:02  Running with dbt=1.9\r\n"
        "\x1b[32m10:01:03  PARITY|## orders\x1b[0m\r\n"
        "10:01:03  PARITY|table|rows=3|h1=1|h2=2\r\n"
        "NOTE|orders|excluded=meta_a,meta_b\n"
        "PARITY|part|2025-01-01|rows=3|h1=1|h2=2"  # no trailing newline
    )
    lines = extract_parity(text)
    assert lines[0] == "## orders"
    assert lines[1] == "table|rows=3|h1=1|h2=2"
    assert lines[2] == "part|2025-01-01|rows=3|h1=1|h2=2"
    assert extract_excluded(text) == {"orders": "meta_a,meta_b"}


def test_extract_parity_nothing():
    assert extract_parity("Encountered an error\n") == []


def test_env_file_robust():
    text = "\ufeff# comment\r\nA=dev\r\nB = 'local db'  # trailing\r\nexport PROJECT_B=\"../b\"\r\nDBT_A=/evil\r\nC=1"
    vals, ignored = parse_env_text(text)
    assert vals == {"A": "dev", "B": "local db", "PROJECT_B": "../b"}
    assert sorted(ignored) == ["C", "DBT_A"]


def test_env_file_no_trailing_newline_and_unquoted_comment():
    vals, _ = parse_env_text("A=x # note\nB=y")
    assert vals == {"A": "x", "B": "y"}


def test_quote_arg_both_platforms():
    assert quote_arg("abc", windows=False) == "abc"
    assert quote_arg("a b", windows=False) == "'a b'"
    assert quote_arg("a b", windows=True) == "'a b'"
    assert quote_arg("it's", windows=True) == "'it''s'"
    assert quote_arg(r"C:\x\dbt.exe", windows=True) == r"C:\x\dbt.exe"
    assert join_command(["data-parity", "run", "--a", "x y"], windows=False) == "data-parity run --a 'x y'"


def test_pkey_numeric_order():
    assert sorted(["10", "9", "2"], key=pkey) == ["2", "9", "10"]


def test_parse_sections_and_values_with_pipes():
    lines = ["junk", "## m", "cols|a:int", "pk|a", "table|rows=2|h1=3|h2=4", "part|a|b|rows=2|h1=3|h2=4",
             "bucket|a|b|7|rows=1|h1=1|h2=1", "col|p|3|c|rows=1|h1=1|h2=1"]
    secs = parse_sections(lines)
    s = parse_section("m", secs["m"])
    assert s.parts == {"a|b": (2, "3", "4")}
    assert ("a|b", "7") in s.buckets and ("p", "3", "c") in s.cols


def test_malformed_line_is_an_error():
    with pytest.raises(ParityError):
        parse_section("m", ["table|rows=x|h1=1|h2=2"])


def test_pct_and_bar():
    assert pct(1, 3) == "33%" and pct(1, 100) == "1.0%" and pct(0, 0) == "-"
    assert bar(0.0) == "\u2591" * 10 and bar(1.0) == "\u2588" * 10 and bar(0.01).startswith("\u2588")


# ---- dbt failures (outputs recorded from dbt Fusion 2.0.9)

def test_classify_package_not_installed():
    out = "[error] [JinjaError (dbt1501)]: Template not found for macro 'data_parity_reconcile'\n"
    assert "data_parity package is not installed" in classify_failure(out) and "dbt deps" in classify_failure(out)
    assert "dbt deps" in classify_failure("Compilation Error: 'data_parity_reconcile' is undefined")


def test_classify_unresolvable_ref_names_the_model_and_file():
    out = "[error] [DependencyNotFound (dbt1048)]: Ref 'nope' not found in project. Searched for 'p.nope, nope'\n  --> models/bad.sql:1:15\n"
    msg = classify_failure(out)
    assert "'nope'" in msg and "models/bad.sql:1:15" in msg
    assert "'ghost'" in classify_failure("Model 'model.p.a' depends on a node named 'ghost' which was not found")


def test_classify_parse_error_names_the_file():
    out = "[error] [MacroSyntaxInvalid (dbt1502)]: Failed to render SQL syntax error: unexpected end of variable block\n(in models/bad.sql:1:15)\n"
    msg = classify_failure(out)
    assert "does not parse" in msg and "models/bad.sql:1:15" in msg


def test_classify_profile_and_own_macro_errors():
    assert "cannot be used" in classify_failure("[error] [InvalidConfig (dbt1005)]: target 'nope' not found in profile 'p'")
    own = "[error] [JinjaError (dbt1501)]: Failed to run operation Compilation Error: data_parity_reconcile: orders: relation x not found"
    assert classify_failure(own).startswith("data_parity_reconcile: orders: relation x not found")


def test_classify_unknown_falls_back_to_the_generic_reason():
    assert classify_failure("Database Error: connection refused") is None
    assert "exited with code 3" in CallResult("B", 3, [], {}, "Database Error: connection refused").reason()
    assert "no PARITY output" in CallResult("B", 0, [], {}, "nothing").reason()
    assert "dbt deps" in CallResult("B", 1, [], {}, "Template not found for macro 'data_parity_reconcile'").reason()


# ---- mapping file

def test_mapping_parses_comments_quotes_and_crlf():
    text = "\ufeff# head\r\norders: cat.sch.orders_v2  # why\r\n\r\ncustomers: 'sch.cust'\r\n"
    assert parse_mapping_text(text) == {"orders": "cat.sch.orders_v2", "customers": "sch.cust"}


@pytest.mark.parametrize("text", ["orders", ": s.t", "orders:", "orders: t", "orders: a.b.c.d", "orders: a.b'c", "orders: a b.c", "orders: s.t\norders: s.u"])
def test_mapping_rejects_bad_entries(text):
    with pytest.raises(MappingError):
        parse_mapping_text(text)


# ---- columns of the structure line

def test_cols_of_reads_normalized_names_and_classes():
    assert _cols_of(["cols|a_b:str,id:int", "pk|id"]) == {"a_b": "str", "id": "int"}
    assert _cols_of(["pk|id"]) == {}
