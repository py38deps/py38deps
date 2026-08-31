#!/usr/bin/env python3
"""Update py38deps submodule references to the latest local commit.

Scans every submodule registered in .gitmodules (paths under repo/), reads
the current HEAD commit of each submodule working tree, and stages the
gitlink change in the parent repository's index (`git add <path>`), so that
`git status` / `git diff --cached` show the new references.

The script only stages the index. It never commits and never pushes: per
project policy, the user reviews the CI result and commits the submodule
reference update themselves.

Usage:
    python update_submodules.py            # stage changed submodule references
    python update_submodules.py --dry-run  # report only, change nothing
"""

import argparse
import subprocess
import sys

from const import GITMODULES, PREFIX, ROOT


def run_git(args, cwd):
    """Run a git command and return the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def list_submodule_paths():
    """Return the submodule paths registered in .gitmodules (repo/ only)."""
    if not GITMODULES.exists():
        sys.exit(f"error: {GITMODULES} not found (run from the py38deps checkout root)")
    if not (ROOT / ".git").exists():
        sys.exit(f"error: {ROOT} is not a git working tree")
    proc = run_git(["config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"], ROOT)
    paths = []
    for line in proc.stdout.splitlines():
        _, path = line.split(None, 1)
        if not path.startswith(PREFIX):
            print(f"!! skip {path}: not under {PREFIX}")
            continue
        paths.append(path)
    return paths


def submodule_head(path):
    """Return the HEAD commit sha of the submodule working tree."""
    proc = run_git(["rev-parse", "HEAD"], ROOT / path)
    return proc.stdout.strip()


def index_gitlink(path):
    """Return the gitlink sha recorded for the submodule in the parent index.

    Returns None when the path has no gitlink entry in the index (e.g. a
    submodule registered in .gitmodules but not yet `git add`-ed).
    """
    proc = run_git(["ls-files", "-s", "--", path], ROOT)
    if not proc.stdout.strip():
        return None
    # output format: "160000 <sha> 0\t<path>"
    return proc.stdout.split("\t", 1)[0].split()[1]


def main():
    parser = argparse.ArgumentParser(
        description="Stage the latest local commit of each repo/* submodule into the parent index."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without staging them",
    )
    args = parser.parse_args()

    paths = list_submodule_paths()
    if not paths:
        print("No submodules found under repo/ in .gitmodules")
        return 0

    changed = 0
    for path in paths:
        try:
            head = submodule_head(path)
        except subprocess.CalledProcessError:
            print(f"!! {path}: not a git repository (uninitialized submodule?) - skipped")
            continue
        try:
            gitlink = index_gitlink(path)
        except subprocess.CalledProcessError:
            print(f"!! {path}: not registered in the index - skipped")
            continue
        if gitlink is None:
            print(f"!! {path}: not registered in the index - skipped")
            continue

        if head == gitlink:
            print(f"== {path}: up to date ({head[:12]})")
            continue

        print(f"-> {path}: {gitlink[:12]} -> {head[:12]}")
        changed += 1
        if not args.dry_run:
            run_git(["add", "--", path], ROOT)

    print()
    if changed:
        if args.dry_run:
            print(f"{changed} submodule reference(s) would be updated (dry run, nothing staged)")
        else:
            print(
                f"{changed} submodule reference(s) staged. "
                "Review with `git status` / `git diff --cached`, then commit manually."
            )
    else:
        print("All submodule references are up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
