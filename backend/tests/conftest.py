"""Test-only instrumentation: report real successful handler executions, not just registry size."""
import functools

from backend import commands
from contracts.actions import ACTION_NAMES

_original = commands.execute
_applied = set()
_attempted = set()


def pytest_sessionstart(session):
    @functools.wraps(_original)
    def measured(db, parsed, *args, **kwargs):
        action = parsed.envelope.action
        _attempted.add(action)
        result = _original(db, parsed, *args, **kwargs)
        if result["status"] == "applied":
            _applied.add(action)
        return result
    commands.execute = measured


def pytest_sessionfinish(session, exitstatus):
    commands.execute = _original


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    terminalreporter.write_sep("-", f"Handler execution only (not journey/branch coverage): {len(_applied)}/{len(ACTION_NAMES)} applied, {len(_attempted)}/{len(ACTION_NAMES)} attempted")
    terminalreporter.write_line("Applied: " + ", ".join(sorted(_applied)))
    terminalreporter.write_line("Not applied in this run: " + ", ".join(sorted(set(commands.HANDLERS) - _applied)))
