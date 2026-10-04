"""Stage 1: read the repository's tags and decide which releases to mirror.

git history only - no network, no GitHub API. Tags are named
"<YYYYMMDD>-<dep>==<version>"; when the same version was released twice (a
re-release after a bad build), only the newest tag survives. That is normal
bookkeeping, not a problem, so superseded tags are listed in the summary but do
not raise warnings - stage 3 warns only if the surviving tag has no assets and a
version therefore ends up missing from the index.

Output: pypisite/cache/tags.json (effective tags + superseded pairs).

Superseded tags are neither warned about nor listed in the CI summary: dropping
an older tag of a re-released version is normal bookkeeping, and only stage 3
speaks up if that leaves a version missing from the index.

    python pypisite/tags.py
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from common import REPO_DIR, CacheStore, Fatal, WarningLog, git_tags, parse_tag, tag_sort_key, write_summary


class TagResolver:
    """Turns the repository's tags into the effective release list."""

    def __init__(self, repo_dir=None, store=None, warn=None):
        self.repo_dir = str(repo_dir if repo_dir is not None else REPO_DIR)
        self.store = store if store is not None else CacheStore()
        self.warn = warn if warn is not None else WarningLog()

    def read(self):
        """Read and parse every tag in the repository."""
        tags = git_tags(self.repo_dir)
        if not tags:
            raise Fatal("no git tags found (in CI use actions/checkout with fetch-depth: 0)")
        return [parse_tag(tag, created) for tag, created in tags]

    @staticmethod
    def resolve(records):
        """One tag per (dep, version): the newest wins, the older ones are superseded.

        Returns (effective, superseded) where superseded holds
        {"tag": dropped, "by": kept, "dep": ..., "version": ...}.
        """
        newest = {}
        for record in records:
            if record["dep"] is None:
                continue  # foreign tag name: nothing to compare it with
            key = (record["dep"], record["version"])
            if key not in newest or tag_sort_key(record) > tag_sort_key(newest[key]):
                newest[key] = record
        kept = {record["tag"] for record in newest.values()}

        effective = []
        superseded = []
        for record in records:
            if record["dep"] is None or record["tag"] in kept:
                effective.append(record)
            else:
                winner = newest[(record["dep"], record["version"])]
                superseded.append(
                    {"tag": record["tag"], "by": winner["tag"], "dep": record["dep"], "version": record["version"]}
                )
        effective.sort(key=tag_sort_key, reverse=True)
        return effective, superseded

    def run(self):
        """Write tags.json and report what the tags mean."""
        records = self.read()
        effective, superseded = self.resolve(records)
        foreign = [record for record in effective if record["dep"] is None]
        for record in foreign:
            self.warn(f"[{record['tag']}] tag name is not <date>-<dep>==<version> - kept as-is")

        path = self.store.write_tags(
            {
                "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "repo_dir": self.repo_dir,
                "tags": effective,
                "superseded": superseded,
            }
        )
        print(f"stage 1: {len(records)} tag(s) -> {len(effective)} effective")
        print(f"stage 1: wrote {path}")

        write_summary(f"### stage 1 - tags\n\n- tags: **{len(records)}** -> **{len(effective)}** effective\n")
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    return TagResolver().run()


if __name__ == "__main__":
    raise SystemExit(main())
