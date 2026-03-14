"""Helpers for locating runtime executables shipped with the active environment."""

from pathlib import Path
import shutil
import sys


def resolve_executable(name: str) -> str:
    """Resolve a command from PATH or the active Python environment."""
    direct = shutil.which(name)
    if direct:
        return direct

    candidates = [
        Path(sys.executable).with_name(name),
        Path(sys.prefix) / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    return name
