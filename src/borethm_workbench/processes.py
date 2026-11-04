"""Launch the actual interpreter while preserving the selected environment.

Windows venv python.exe is a redirector: its PID/memory describe the launcher,
not the interpreter. CPython's launcher environment preserves venv identity
when starting the base executable directly. Linux uses the normal executable.
"""

import os
import sys


def python_command(*arguments):
    executable = (
        getattr(sys, "_base_executable", sys.executable)
        if os.name == "nt"
        else sys.executable
    )
    return [executable, *arguments]


def python_environment():
    environment = os.environ.copy()
    if os.name == "nt":
        environment["__PYVENV_LAUNCHER__"] = sys.executable
    return environment
