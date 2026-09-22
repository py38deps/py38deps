"""Shared constants for py38deps scripts."""

from pathlib import Path

# Repository root (parent of the scripts/ directory)
ROOT = Path(__file__).resolve().parent.parent

# Path to the .gitmodules file at the repository root
GITMODULES = ROOT / ".gitmodules"

# Submodule prefix used to filter .gitmodules entries
PREFIX = "repo/"
