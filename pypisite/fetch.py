"""Stage 2: turn tags into release pages and record what each page says.

Reads the effective tags from pypisite/cache/tags.json (stage 1) and, for every
tag without a record, reads

    https://github.com/<repo>/releases/expanded_assets/<tag>

writing pypisite/cache/<tag>.json with the page facts: uploaded asset names and
their sha256 digests. A tag that is already recorded is never read again, so a
steady-state run makes no request at all; --refetch <tag> re-reads one tag (the
workflow passes the tag of a release event) and --no-cache re-reads everything.

Assets are looked up per tag - release lists are never paged through. When a
page shows no uploaded assets, the raw HTML is kept under pypisite/cache/debug/
and a recorded release is re-read as a control, so "GitHub changed its HTML"
cannot be mistaken for "this release has no files".

    python pypisite/fetch.py [--refetch TAG] [--no-cache]
"""

from __future__ import annotations

import argparse
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import REPO, CacheStore, Fatal, WarningLog, tag_sort_key, write_summary

USER_AGENT = "py38deps-index-builder (+https://github.com/py38deps/py38deps)"
NETWORK_ATTEMPTS = 3


class ReleasePage:
    """One tag's public release page, and how to read it.

    GitHub answers 200 even for a tag that has no release at all - such a page
    only lists the automatic source archives and contains no /releases/download/
    link, so "found" means "has uploaded assets".
    """

    def __init__(self, tag, repo=None, attempts=NETWORK_ATTEMPTS):
        self.repo = repo if repo is not None else REPO
        self.tag = tag
        self.attempts = attempts

    @property
    def url(self):
        return "https://github.com/{}/releases/expanded_assets/{}".format(
            self.repo, urllib.parse.quote(self.tag, safe="")
        )

    def fetch(self):
        """Return (assets, found, body); raise Fatal after persistent failures."""
        request = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
        last_error = None
        for attempt in range(self.attempts):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read().decode("utf-8", "replace")
                assets = self.parse_assets(body)
                return assets, bool(assets), body
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return [], False, ""
                last_error = f"HTTP {exc.code}"
            except Exception as exc:  # noqa: BLE001 - network layer, report as-is
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < self.attempts:
                time.sleep(2 * (attempt + 1))
        # A hiccup must never silently shrink the index.
        raise Fatal(f"{self.url} failed after {self.attempts} attempts: {last_error}")

    @staticmethod
    def parse_assets(body):
        """Extract (filename, sha256) pairs from a release page's asset fragment.

        Layout per asset: an <a href="/<repo>/releases/download/<tag>/<file>">
        followed by a <span>sha256:<64 hex></span>. Splitting on the download
        path keeps each digest next to its own file.
        """
        rows = []
        for part in body.split("/releases/download/")[1:]:
            match = re.match(r"[^\"/]+/(?P<name>[^\"/?]+)", part)
            if match is None:
                continue
            name = urllib.parse.unquote(match.group("name"))
            digest = re.search(r"sha256:([0-9a-f]{64})", part)
            rows.append((name, digest.group(1) if digest else None))
        return rows

    def has_assets(self):
        """Re-read the page and report whether it still lists assets (layout control)."""
        _assets, found, _body = self.fetch()
        return found


class FetchStage:
    """Reads the release page of every effective tag that has no record yet."""

    def __init__(self, store=None, warn=None, repo=None, refetch=(), no_cache=False, workers=8):
        self.repo = repo if repo is not None else REPO
        self.store = store if store is not None else CacheStore()
        self.warn = warn if warn is not None else WarningLog()
        self.refetch = set(refetch)
        self.no_cache = no_cache
        self.workers = workers

    def pending(self, tags):
        """Split the effective tags into "read now" and "already recorded"."""
        records = {tag: self.store.read_record(tag) for tag in tags}
        pending = [tag for tag in tags if self.no_cache or tag in self.refetch or records[tag] is None]
        return pending, records

    def _fetch_all(self, tags):
        if not tags:
            return {}
        with ThreadPoolExecutor(max_workers=min(self.workers, len(tags))) as pool:
            return dict(pool.map(lambda tag: (tag, ReleasePage(tag, repo=self.repo).fetch()), tags))

    def run(self, tags):
        """Read what is missing; returns (records, pages_read)."""
        self.store.directory.mkdir(parents=True, exist_ok=True)
        removed = self.store.drop_orphans(tags)
        if removed:
            print(f"stage 2: dropped {removed} record(s) for tags that stage 1 did not keep")

        pending, records = self.pending(tags)
        print(f"stage 2: {len(pending)} release page(s) to read, {len(tags) - len(pending)} already recorded")
        if not pending:
            return records, 0

        results = self._fetch_all(pending)
        empty_tags = []
        for tag, (assets, found, body) in results.items():
            records[tag] = self.store.write_record(tag, assets, found)
            if not found:
                empty_tags.append(tag)
                self.store.write_debug_page(tag, body)
        if empty_tags:
            print(
                f"stage 2: raw page(s) for {len(empty_tags)} asset-less tag(s) saved under "
                f"{self.store.directory / 'debug'}"
            )
            self._control_check(records)
            for tag in empty_tags:
                self.warn(f"[{tag}] no uploaded assets on the release page - tag skipped")
        return records, len(results)

    def _control_check(self, records):
        """Tell "this release has no assets" apart from "GitHub changed the page layout".

        Re-reads one already-recorded tag: if that page lost its assets too, the
        parse (or the release) is broken and the run must stop rather than
        publish an index that silently lost content.
        """
        control = sorted(
            (tag for tag, record in records.items() if record and record.get("found")),
            key=lambda name: tag_sort_key({"tag": name}),
        )
        if control and not ReleasePage(control[-1], repo=self.repo).has_assets():
            raise Fatal(
                "a recorded release no longer lists assets (control tag "
                f"{control[-1]}) - release page layout changed or assets were removed"
            )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refetch", nargs="?", const="", default="", help="re-read this tag even though it is recorded")
    parser.add_argument("--no-cache", action="store_true", help="re-read every release page")
    args = parser.parse_args(argv)

    store = CacheStore()
    payload = store.read_tags()
    tags = [record["tag"] for record in payload["tags"]]

    warn = WarningLog()
    refetch = {args.refetch} if args.refetch else set()
    records, read = FetchStage(store, warn, refetch=refetch, no_cache=args.no_cache).run(tags)
    recorded = sum(1 for tag in tags if records.get(tag) is not None)

    print(f"stage 2: {recorded} tag(s) recorded, {len(tags) - recorded} still missing")
    lines = [
        "### stage 2 - release pages",
        "",
        f"- effective tags: **{len(tags)}**",
        f"- release pages read this run: **{read}**",
        f"- tags with a record: **{recorded}**",
        "",
    ]
    if warn.messages:
        lines += ["### stage 2 warnings", ""] + [f"- {message}" for message in warn.messages] + [""]
    write_summary("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
