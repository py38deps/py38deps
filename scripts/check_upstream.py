#!/usr/bin/env python3
"""Check whether any repo/* submodule is behind a newly released upstream tag.

For every submodule registered in .gitmodules (paths under repo/):
  1. fetch the upstream remote's tags (unless --no-fetch)
  2. find the newest upstream tag (pre-release tags are ignored unless
     --include-pre is given)
  3. check whether the backport branch already contains that tag, using
     `git merge-base --is-ancestor <tag> <branch>` -- if the tag is not an
     ancestor of the backport branch, upstream released something we have
     not backported yet.

This is a reminder tool. It only fetches (read-only network access); it does
not modify the working tree, the index, or any ref.

Usage:
    python check_upstream.py                  # fetch upstream tags and check all
    python check_upstream.py --no-fetch       # use existing local refs only
    python check_upstream.py --only-outdated  # print only outdated libraries
    python check_upstream.py --include-pre    # count pre-release tags too
"""

import argparse
import re
import subprocess
import sys

from const import GITMODULES, PREFIX, ROOT
STABLE_TAG = re.compile(r"^v?\d+(\.\d+)*$")


def run_git(args, cwd, check=True):
    """Run a git command and return the CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
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
            print(f"?? skip {path}: not under {PREFIX}")
            continue
        paths.append(path)
    return paths


def has_remote(path, name):
    """Return True if the submodule has a remote named `name`."""
    proc = run_git(["remote"], path)
    return name in proc.stdout.split()


def fetch_upstream(path):
    """Fetch tags from the upstream remote, pruning locally deleted ones."""
    run_git(["fetch", "upstream", "--tags", "--prune-tags"], path)


def newest_tag(path, include_pre):
    """Return the newest upstream tag, ignoring pre-releases unless asked."""
    proc = run_git(["tag", "--sort=-v:refname"], path)
    for tag in proc.stdout.split():
        if include_pre or STABLE_TAG.match(tag):
            return tag
    return None


def backport_branch(path):
    """Return the local name of the backport branch (origin's default branch)."""
    proc = run_git(["rev-parse", "--symbolic-full-name", "origin/HEAD"], path, check=False)
    if proc.returncode == 0:
        return proc.stdout.strip().rsplit("/", 1)[-1]
    proc = run_git(["remote", "show", "origin"], path, check=False)
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("HEAD branch:"):
                return line.split(":", 1)[1].strip()
    return None


def resolve_ref(path, name):
    """Resolve a branch name to a local ref, falling back to origin/<name>."""
    for candidate in (name, f"origin/{name}"):
        if not candidate:
            continue
        proc = run_git(["rev-parse", "--verify", "--quiet", candidate], path, check=False)
        if proc.returncode == 0:
            return candidate
    return "HEAD"


def describe(path, sha):
    """Human-readable position of sha, e.g. v4.2.0-3-g1b8d4dd."""
    proc = run_git(["describe", "--tags", "--long", sha], path, check=False)
    if proc.returncode == 0:
        return proc.stdout.strip()
    return sha[:12]


def main():
    parser = argparse.ArgumentParser(
        description="Report repo/* submodules whose upstream released a new tag that we have not backported."
    )
    parser.add_argument("--no-fetch", action="store_true", help="do not fetch upstream tags, use existing local refs")
    parser.add_argument("--only-outdated", action="store_true", help="print only libraries that need backporting")
    parser.add_argument("--include-pre", action="store_true", help="also consider pre-release tags (rc/a/b/dev)")
    args = parser.parse_args()

    paths = list_submodule_paths()
    if not paths:
        print("No submodules found under repo/ in .gitmodules")
        return 0

    outdated = []
    for path in paths:
        sub = ROOT / path

        if not sub.is_dir():
            print(f"?? {path}: directory missing (uninitialized submodule?) - skipped")
            continue

        if not has_remote(sub, "upstream"):
            print(f"?? {path}: no upstream remote configured - skipped")
            continue

        if not args.no_fetch:
            try:
                fetch_upstream(sub)
            except subprocess.CalledProcessError as exc:
                print(f"!! {path}: fetch upstream failed - skipped")
                if exc.stdout:
                    print(f"    stdout: {exc.stdout.strip()}")
                if exc.stderr:
                    print(f"    stderr: {exc.stderr.strip()}")
                continue

        tag = newest_tag(sub, args.include_pre)
        if tag is None:
            print(f"?? {path}: no tags found on upstream - skipped")
            continue

        branch = backport_branch(sub)
        ref = resolve_ref(sub, branch)
        tag_sha = run_git(["rev-parse", f"{tag}^{{commit}}"], sub).stdout.strip()
        head_sha = run_git(["rev-parse", ref], sub).stdout.strip()
        ours = describe(sub, head_sha)

        proc = run_git(["merge-base", "--is-ancestor", tag_sha, head_sha], sub, check=False)
        up_to_date = proc.returncode == 0

        if up_to_date:
            print(f"== {path:<24} up to date (latest upstream tag {tag}, ours {ours})")
        else:
            print(f"!! {path:<24} OUTDATED: upstream released {tag}, we are on {ours}")
            outdated.append((path, tag, ours))

    print()
    if outdated:
        print(f"{len(outdated)} of {len(paths)} library/libraries need backporting:")
        for path, tag, ours in outdated:
            print(f"   - {path}: upstream {tag}, we are on {ours}")
        return 1
    print(f"All {len(paths)} submodule(s) are up to date with upstream.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
