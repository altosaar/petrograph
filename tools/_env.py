"""
_env — where the repo is, and where the API keys are.

Not a script. Every tool anchors its paths off the repository rather than the working
directory, because they are run from anywhere — from the justfile at the repo root, from a
session folder, from cron. And three of them read an API key, which had drifted into three
shapes: one looked in .env, one did not, and one looked in the wrong .env.

A key is read from the environment first and a local .env second. The .env is gitignored and
never leaves the machine; nothing here writes one, and no key is ever printed.

No dependencies, so importing this leaves a tool's `dependencies = []` intact.
"""

from __future__ import annotations
import os
import sys
from pathlib import Path

# tools/ sits directly under the repository, so the repo is this file's grandparent. Anchored
# off __file__ rather than the cwd, so every tool works when invoked from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
DOTENV = REPO_ROOT / ".env"  # the repo's .env, not the cwd's


def dotenv_key(var: str) -> str | None:
    """Read one KEY=value out of the repo's .env, if there is one."""
    if not DOTENV.exists():
        return None
    for line in DOTENV.read_text().splitlines():
        if line.startswith(f"{var}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return None


def api_key(var: str) -> str:
    """The named key, from the environment or the repo's .env, or exit saying which is missing."""
    key = os.environ.get(var) or dotenv_key(var)
    if not key:
        sys.exit(f"{var} is not set (environment or .env).")
    return key
