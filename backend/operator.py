"""Windows operator provenance, obtained locally and never from HTTP/input."""

from __future__ import annotations

import csv
import io
import os
import subprocess
import sys

from contracts.events import OperatorProvenance


def current_operator_provenance() -> OperatorProvenance:
    if os.name != "nt":
        raise RuntimeError("WINDOWS_OPERATOR_REQUIRED")
    # Use the OS binary by absolute path rather than an attacker-selected PATH.
    executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "whoami.exe")
    result = subprocess.run(
        [executable, "/user", "/fo", "csv", "/nh"],
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if len(rows) != 1 or len(rows[0]) != 2 or not rows[0][1].startswith("S-1-"):
        raise RuntimeError("OPERATOR_PROVENANCE_UNAVAILABLE")
    return OperatorProvenance(
        windows_sid=rows[0][1], windows_account=rows[0][0],
        process_image=sys.executable, pid=os.getpid(),
    )
