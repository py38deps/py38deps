"""Shared pieces for the three pypisite stages.

Pipeline:

    pypisite/tags.py    stage 1  git tags                -> pypisite/cache/tags.json
    pypisite/fetch.py   stage 2  tags.json + release pages -> pypisite/cache/<tag>.json
    pypisite/build.py   stage 3  cache records           -> pypisite/_site/

Every path is derived from this file's location, so the stages take no path
options at all: pypisite/cache/ is the private working area (never deployed) and
pypisite/_site/ is the published tree.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent  # pypisite/
CACHE_DIR = HERE / "cache"
SITE_DIR = HERE / "_site"
TAGS_NAME = "tags.json"
REPORT_NAME = "report.json"
CACHE_VERSION = 1

# This mirror exists for one repository and one site; nothing here is meant to be
# reused for another repository, so the identity and the addresses are constants.
REPO = "py38deps/py38deps"
REPO_DIR = HERE.parent  # the checkout that owns these tags
SITE_URL = "https://py38deps.github.io/py38deps"
USAGE_INDEX_URL = f"{SITE_URL}/simple/"
LIVE_URL = f"{SITE_URL}/"

# Tags look like "20261002-msgspec==0.22.0"; anything else is kept as-is.
TAG_RE = re.compile(r"^(?P<date>\d{8})-(?P<dep>.+?)==(?P<version>.+)$")


class Fatal(SystemExit):
    """Abort the stage with a message the CI log makes obvious."""

    def __init__(self, message):
        super().__init__(f"FATAL: {message}")


class WarningLog:
    """Collect warnings; emit GitHub Actions annotations when running in CI."""

    def __init__(self, annotate=None):
        if annotate is None:
            annotate = os.environ.get("GITHUB_ACTIONS") == "true"
        self.messages = []
        self.annotate = annotate

    def __call__(self, message):
        self.messages.append(message)
        print(f"WARNING: {message}", file=sys.stderr)
        if self.annotate:
            safe = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::warning::{safe}")


def write_text(path, text):
    # py3.8: Path.write_text() has no newline kwarg before 3.10; keep the output LF-only
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_summary(text):
    """Append one stage's result to the CI job summary, or print it locally."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8", newline="") as handle:
            handle.write(text)
    else:
        print(text)


def git_tags(repo_dir):
    """Return [(tag, creatordate), ...] from the local clone."""
    out = subprocess.run(
        ["git", "-C", repo_dir, "for-each-ref", "--format=%(refname:short)\t%(creatordate:iso-strict)", "refs/tags"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tags = []
    for line in out.splitlines():
        name, _, created = line.partition("\t")
        if name.strip():
            tags.append((name.strip(), created.strip()))
    return tags


def parse_tag(tag, created=""):
    """Split a tag into its parts; date/dep/version are None for foreign tags."""
    parsed = TAG_RE.match(tag)
    return {
        "tag": tag,
        "created": created,
        "date": parsed.group("date") if parsed else None,
        "dep": parsed.group("dep") if parsed else None,
        "version": parsed.group("version") if parsed else None,
    }


def tag_sort_key(record):
    """Sort key with the newest tag last: date prefix, then tag creation date, then name."""
    return (record.get("date") or "", record.get("created") or "", record["tag"])


class CacheStore:
    """The private working area: one JSON record per tag plus small state files.

    `directory` can be given for tests; the stages always use the default, which
    is `<pypisite>/cache`.
    """

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else CACHE_DIR

    # ------------------------------------------------------------ per-tag records

    @staticmethod
    def safe_name(tag):
        """Filesystem-safe file name for a tag (tags contain '==' but no path separators)."""
        return re.sub(r"[^A-Za-z0-9._=+-]", "_", tag)

    def record_path(self, tag):
        """One JSON file per tag; the tag name is sanitised for filesystem safety."""
        return self.directory / f"{self.safe_name(tag)}.json"

    def read_record(self, tag):
        path = self.record_path(tag)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            return None
        if record.get("cache_version") != CACHE_VERSION or record.get("tag") != tag:
            return None
        return record

    def write_record(self, tag, assets, found):
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {
            "cache_version": CACHE_VERSION,
            "tag": tag,
            "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "found": found,
            "assets": [[name, digest] for name, digest in assets],
        }
        with open(self.record_path(tag), "w", encoding="utf-8", newline="") as handle:
            json.dump(record, handle, indent=1)
            handle.write("\n")
        return record

    def write_debug_page(self, tag, body):
        """Keep the raw page of an asset-less tag for later inspection."""
        debug_dir = self.directory / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        write_text(debug_dir / f"{self.safe_name(tag)}.html", body)

    def drop_orphans(self, tags):
        """Prune records and debug pages that no longer belong to the tag list.

        A debug page is kept only while its tag still exists and still has no
        uploaded assets: once the release is published (or the tag is dropped)
        the raw page has served its purpose, and a stale one would suggest a
        problem that no longer exists.

        tags.json, report.json and the site.* state files are never touched.
        Returns (records_removed, debug_pages_removed).
        """
        keep = {self.record_path(tag).name for tag in tags}
        reserved = (TAGS_NAME, REPORT_NAME, "site.digest", "site.changed")
        removed = 0
        for path in self.directory.glob("*.json"):
            if path.name in keep or path.name in reserved:
                continue
            path.unlink()
            removed += 1

        wanted = {
            f"{self.safe_name(tag)}.html" for tag in tags if not (self.read_record(tag) or {}).get("found")
        }
        dropped_debug = 0
        for path in (self.directory / "debug").glob("*.html"):
            if path.name not in wanted:
                path.unlink()
                dropped_debug += 1
        return removed, dropped_debug

    # ----------------------------------------------------------- stage 1 / 3 files

    def write_tags(self, payload):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / TAGS_NAME
        with open(path, "w", encoding="utf-8", newline="") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write("\n")
        return path

    def read_tags(self):
        """Read the stage 1 output; abort with a pointer when it is missing."""
        path = self.directory / TAGS_NAME
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except OSError:
            raise Fatal(f"{path} not found - run pypisite/tags.py first") from None
        except ValueError as exc:
            raise Fatal(f"{path} is not readable JSON ({exc}) - re-run pypisite/tags.py") from None
        if not payload.get("tags"):
            raise Fatal(f"{path} lists no tags - re-run pypisite/tags.py")
        return payload

    def write_report(self, report):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / REPORT_NAME
        with open(path, "w", encoding="utf-8", newline="") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1, sort_keys=True)
            handle.write("\n")
        return path

    def write_deploy_state(self, digest, changed):
        """Record the site digest and the changed flag the workflow reads."""
        self.directory.mkdir(parents=True, exist_ok=True)
        write_text(self.directory / "site.digest", digest + "\n")
        write_text(self.directory / "site.changed", ("true" if changed else "false") + "\n")
        return changed
