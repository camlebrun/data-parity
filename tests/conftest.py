"""Shared fixtures: a fake dbt executable (generated in a tmp dir) driven by JSON "world" files."""
import contextlib
import copy
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from data_parity.cli import main

HERE = Path(__file__).resolve().parent


def make_fake_dbt(directory: Path) -> str:
    """Write a fake `dbt` (a Python script) in ``directory`` and return its path."""
    directory.mkdir(parents=True, exist_ok=True)
    impl = (HERE / "fake_dbt_impl.py").read_text(encoding="utf-8")
    if os.name == "nt":
        (directory / "fakedbt_impl.py").write_text(impl, encoding="utf-8")
        exe = directory / "fakedbt.cmd"
        exe.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, directory / "fakedbt_impl.py"), encoding="ascii")
    else:
        exe = directory / "fakedbt"
        exe.write_text("#!%s\n%s" % (sys.executable, impl), encoding="utf-8")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(exe)


def base_models():
    days = ["2025-01-0%d" % d for d in range(1, 7)]
    orders = {
        "pk": ["id"], "partitioned": True, "cols": ["id", "amount", "status"], "excluded": "meta_loaded_at",
        "rows": [[d, i * 10 + n, "%d.00" % (i * 10 + n), "paid"] for i, d in enumerate(days, 1) for n in range(3)],
    }
    customers = {
        "pk": ["cid"], "partitioned": False, "cols": ["cid", "city"],
        "rows": [[None, n, "city%d" % n] for n in range(4)],
    }
    events = {
        "pk": [], "partitioned": True, "cols": ["ev", "value"],
        "rows": [[d, "e%d" % n, n] for d in days[:2] for n in range(3)],
    }
    return {"orders": orders, "customers": customers, "events": events}


class Res:
    def __init__(self, rc, out, err):
        self.rc, self.out, self.err = rc, out, err


class Env:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.worlds = tmp / "worlds"
        self.worlds.mkdir()
        self.fake = make_fake_dbt(tmp / "bin")
        self.project = tmp / "proj"
        self.project.mkdir()
        (self.project / "dbt_project.yml").write_text("name: x\n")
        self.out_dir = tmp / "out"
        self.log = tmp / "calls.log"

    def world(self, target, models=None, **extra):
        data = {"models": copy.deepcopy(models if models is not None else base_models())}
        data.update(extra)
        (self.worlds / ("%s.json" % target)).write_text(json.dumps(data), encoding="utf-8")

    def run(self, *argv, out=True, fake=True):
        args = list(argv)
        if fake:
            args += ["--dbt-a", self.fake, "--dbt-b", self.fake]
        if out:
            args += ["--out", str(self.out_dir)]
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = main(args)
        return Res(rc, o.getvalue(), e.getvalue())

    def run_dirs(self):
        return sorted(p for p in self.out_dir.iterdir() if p.is_dir())

    def last_json(self):
        return json.loads((self.run_dirs()[-1] / "parity.json").read_text(encoding="utf-8"))

    def last_report(self):
        return (self.run_dirs()[-1] / "parity.md").read_text(encoding="utf-8")

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x]

    def call_args(self):
        out = []
        for c in self.calls():
            a = c["argv"]
            out.append(json.loads(a[a.index("--args") + 1]) if "--args" in a else None)
        return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path)
    monkeypatch.setenv("FAKE_DBT_WORLDS", str(e.worlds))
    monkeypatch.setenv("FAKE_DBT_LOG", str(e.log))
    monkeypatch.chdir(e.project)
    return e


def mutate(models, fn):
    m = copy.deepcopy(models)
    fn(m)
    return m
