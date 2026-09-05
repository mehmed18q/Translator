from __future__ import annotations

import sys
from pathlib import Path


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def resolve_application_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return application_dir() / path
