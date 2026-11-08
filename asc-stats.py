#!/usr/bin/env python3
"""Compatibility launcher; use --cli for the original table workflow."""

import importlib.util
import os
import sys
from pathlib import Path

if sys.version_info < (3, 11) or importlib.util.find_spec("textual") is None:
    local_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
    if local_python.exists() and str(local_python) != sys.executable:
        os.execv(str(local_python), [str(local_python), __file__, *sys.argv[1:]])
    print(
        "Pomme requires Python 3.11+ and its dependencies. Run: uv sync --python 3.13",
        file=sys.stderr,
    )
    raise SystemExit(1)

from pomme.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
