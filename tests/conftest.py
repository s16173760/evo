"""Shared pytest config for the entire tests/ tree.

Adds tests/ to sys.path so subdirectory tests (tests/e2e/, tests/live/)
can import the leading-underscore helpers (`_sandbox_agent_fixture`,
`_sshd_fixture`) that live at tests/ root.

Also cleans up any leftover evo dashboard processes that squat on the
8080-8099 port range. evo init bumps to the next free port if 8080 is
busy, so a few leaked dashboards are tolerated -- but interactive
smoke tests that don't clean up can fill the whole range and break
every test that calls `evo init`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

DASHBOARD_PORT_RANGE = range(8080, 8100)


def _kill_dashboards_on_ports(ports: range) -> None:
    """SIGTERM anything LISTEN-ing on the given local ports."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{ports.start}-{ports.stop - 1}",
             "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return  # lsof unavailable (e.g. minimal CI image); nothing to do
    pids = [int(p) for p in result.stdout.split() if p.isdigit()]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if pids:
        # Give the OS a beat to actually release the sockets so the next
        # `evo init` doesn't race on bind().
        time.sleep(0.5)


@pytest.fixture(scope="session", autouse=True)
def _release_dashboard_ports():
    """Kill any squatting dashboards before and after the suite runs.

    Without this, a previous interactive run that didn't clean up will
    cause every test that calls `evo init` to fail with
    `no free port in range 8080..8099`.
    """
    _kill_dashboards_on_ports(DASHBOARD_PORT_RANGE)
    yield
    _kill_dashboards_on_ports(DASHBOARD_PORT_RANGE)
