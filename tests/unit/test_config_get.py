"""Tests for the new readers added alongside `evo config set`:

- `evo config get <field>`
- `evo config backend show`
- `evo infra event` / `evo infra log`
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from evo.cli import (
    cmd_config_backend,
    cmd_config_get,
    cmd_config_set,
    cmd_infra,
)
from evo.core import init_workspace


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@evo"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)
    (root / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)


def _capture(fn, args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(args)
    return buf.getvalue()


class TestConfigGet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _init_git_repo(self.root)
        init_workspace(
            self.root,
            target="t.py",
            benchmark="python bench.py",
            metric="max",
            gate=None,
        )
        self._old_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_get_returns_set_value_text(self):
        cmd_config_set(argparse.Namespace(field="metric", value="min"))
        out = _capture(cmd_config_get, argparse.Namespace(field="metric", json=False))
        self.assertEqual(out.strip(), "min")

    def test_get_returns_int_for_max_attempts(self):
        cmd_config_set(argparse.Namespace(field="max-attempts", value="9"))
        out = _capture(cmd_config_get, argparse.Namespace(field="max-attempts", json=False))
        self.assertEqual(out.strip(), "9")

    def test_get_emits_dict_for_frontier_strategy(self):
        cmd_config_set(argparse.Namespace(field="frontier-strategy", value="epsilon_greedy"))
        out = _capture(cmd_config_get, argparse.Namespace(field="frontier-strategy", json=True))
        parsed = json.loads(out)
        self.assertEqual(parsed["kind"], "epsilon_greedy")
        self.assertIn("epsilon", parsed["params"])

    def test_get_returns_empty_for_unset_gate(self):
        out = _capture(cmd_config_get, argparse.Namespace(field="gate", json=False))
        self.assertEqual(out.strip(), "")

    def test_get_returns_null_in_json_for_unset(self):
        out = _capture(cmd_config_get, argparse.Namespace(field="gate", json=True))
        self.assertEqual(out.strip(), "null")


class TestConfigBackendShow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _init_git_repo(self.root)
        init_workspace(
            self.root,
            target="t.py",
            benchmark="python bench.py",
            metric="max",
            gate=None,
        )
        self._old_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_backend_show_default_worktree(self):
        out = _capture(cmd_config_backend, argparse.Namespace(
            backend="show", json=False,
            workspaces=None, provider=None, provider_config=None, remote=None,
        ))
        self.assertIn("execution_backend: worktree", out)

    def test_backend_show_json(self):
        out = _capture(cmd_config_backend, argparse.Namespace(
            backend="show", json=True,
            workspaces=None, provider=None, provider_config=None, remote=None,
        ))
        parsed = json.loads(out)
        self.assertEqual(parsed["execution_backend"], "worktree")


class TestInfraLog(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _init_git_repo(self.root)
        init_workspace(
            self.root,
            target="t.py",
            benchmark="python bench.py",
            metric="max",
            gate=None,
        )
        self._old_cwd = Path.cwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._tmp.cleanup()

    def test_log_empty_initially(self):
        out = _capture(cmd_infra, argparse.Namespace(infra_action="log", limit=None))
        self.assertEqual(json.loads(out), [])

    def test_log_returns_recent_first(self):
        for i in range(3):
            cmd_infra(argparse.Namespace(
                infra_action="event", message=f"e{i}", breaking=False,
            ))
        out = _capture(cmd_infra, argparse.Namespace(infra_action="log", limit=None))
        events = json.loads(out)
        self.assertEqual([e["message"] for e in events], ["e2", "e1", "e0"])

    def test_log_respects_limit(self):
        for i in range(5):
            cmd_infra(argparse.Namespace(
                infra_action="event", message=f"e{i}", breaking=False,
            ))
        out = _capture(cmd_infra, argparse.Namespace(infra_action="log", limit=2))
        events = json.loads(out)
        self.assertEqual([e["message"] for e in events], ["e4", "e3"])


if __name__ == "__main__":
    unittest.main()
