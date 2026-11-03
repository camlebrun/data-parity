"""Golden files for the non-TTY output of `run`, `conformance` and `report` (stdout, stderr, exit code).

They pin the plain-text contract: the terminal redesign (spec: cli-experience) is a TTY-only layer, so redirected
output must stay byte for byte what these files hold. To change them on purpose, run
``UPDATE_GOLDEN=1 pytest tests/test_golden_output.py`` and review the diff.
"""
import os
import re
from pathlib import Path

import pytest

from conftest import base_models, mutate
from test_cli import RUN, corrupted, two_sides

GOLDEN = Path(__file__).resolve().parent / "golden"


def normalise(text, env):
    """Replace the parts that change from one run to the next (temporary paths, run directory stamp)."""
    for root in {str(env.tmp), str(env.tmp.resolve())}:
        text = text.replace(root, "<TMP>")
    text = re.sub(r"<TMP>\S*", lambda m: m.group(0).replace("\\", "/"), text)       # Windows paths use backslashes
    return re.sub(r"run-\d{8}-\d{6}(-\d+)?", "run-<STAMP>", text)


def snapshot(name, res, env):
    got = "exit: %d\n--- stdout ---\n%s--- stderr ---\n%s" % (res.rc, normalise(res.out, env), normalise(res.err, env))
    path = GOLDEN / (name + ".txt")
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.mkdir(exist_ok=True)
        path.write_text(got, encoding="utf-8", newline="\n")
    assert path.is_file(), "missing golden file %s (create it with UPDATE_GOLDEN=1)" % path.name
    assert got == path.read_text(encoding="utf-8")


def test_run_identical(env):
    two_sides(env)
    snapshot("run_identical", env.run(*RUN), env)


def test_run_differences(env):
    two_sides(env, corrupted())
    snapshot("run_differences", env.run(*RUN), env)


def test_run_identical_on_a_limited_scope(env):
    two_sides(env)
    snapshot("run_scoped", env.run(*RUN, "--last", "2"), env)


def test_run_error(env):
    env.world("ta")
    snapshot("run_error", env.run("run", "--a", "ta", "--b", "ghost"), env)


def test_run_structure_difference(env):
    models = base_models()
    other = mutate(models, lambda m: m.pop("events"))
    two_sides(env, other, models)
    snapshot("run_only_in_a", env.run(*RUN), env)


def test_conformance_identical(env):
    env.world("ta")
    env.world("tb")
    snapshot("conformance_identical", env.run("conformance", "--a", "ta", "--b", "tb"), env)


def test_conformance_different(env):
    env.world("ta")
    env.world("tb", conformance=["0|s=a|h1=1|h2=2", "1|s=b|h1=3|h2=999"])
    snapshot("conformance_different", env.run("conformance", "--a", "ta", "--b", "tb"), env)


def test_conformance_error(env):
    env.world("ta")
    env.world("tb", empty=True)
    snapshot("conformance_error", env.run("conformance", "--a", "ta", "--b", "tb"), env)


def test_report(env):
    two_sides(env, corrupted())
    assert env.run(*RUN).rc == 1
    d = env.run_dirs()[-1]
    snapshot("report", env.run("report", str(d), out=False, fake=False), env)


@pytest.mark.parametrize("argv", [[], ["help"], ["--version"], ["nope"]])
def test_top_level(env, argv):
    name = "top_" + (argv[0].strip("-") if argv else "noargs")
    snapshot(name, env.run(*argv, out=False, fake=False), env)
