"""Stage 2: turn tags into release pages and record what each page says.

Reads the effective tags from pypisite/cache/tags.json (stage 1) and, for every
tag without a record, reads

    https://github.com/<repo>/releases/expanded_assets/<tag>

writing pypisite/cache/<tag>.json with the page facts: uploaded asset names and
their sha256 digests. Tags that have assets are never read again, so a
steady-state run reads nothing but the few tags still recorded as asset-less
(their release may be published later - see pending() below); --refetch <tag>
re-reads one tag and --no-cache re-reads everything.

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
        """Tags to read: never seen, recorded as asset-less, or explicitly asked for.

        Asset-less records are re-read on every run. A release can be published
        long after its tag was first checked, and the release event cannot always
        reach this workflow (a tag may point at a commit that predates it), so
        re-reading those few tiny pages is the only dependable way for such a
        release to appear in the index. Tags that have assets are still never
        read again.
        """
        records = {tag: self.store.read_record(tag) for tag in tags}
        pending = [
            tag
            for tag in tags
            if self.no_cache
            or tag in self.refetch
            or records[tag] is None
            or not records[tag].get("found")
        ]
        return pending, records

    def _fetch_all(self, tags):
        if not tags:
            return {}
        with ThreadPoolExecutor(max_workers=min(self.workers, len(tags))) as pool:
            return dict(pool.map(lambda tag: (tag, ReleasePage(tag, repo=self.repo).fetch()), tags))

    def run(self, tags):
        """Read what is missing; returns (records, pages_read)."""
        self.store.directory.mkdir(parents=True, exist_ok=True)
        removed, dropped_debug = self.store.drop_orphans(tags)
        if removed or dropped_debug:
            print(
                f"stage 2: dropped {removed} record(s) and {dropped_debug} debug page(s) "
                "that no longer apply"
            )

        pending, records = self.pending(tags)
        if not pending:
            print(f"stage 2: {len(tags)} record(s) on file, nothing to read")
            return records, 0

        previous = dict(records)  # before the records are overwritten
        results = self._fetch_all(pending)
        empty_tags, newly_empty, recovered = [], [], []
        for tag, (assets, found, body) in results.items():
            was_empty = (previous.get(tag) or {}).get("found") is False
            records[tag] = self.store.write_record(tag, assets, found)
            if found:
                if was_empty:
                    recovered.append(tag)
                continue
            empty_tags.append(tag)
            if not was_empty:
                newly_empty.append(tag)
            self.store.write_debug_page(tag, body)
        print(f"stage 2: read {len(results)} page(s), {len(tags)} tag(s) recorded")
        for tag in recovered:
            print(f"stage 2: {tag} now has assets")

        if empty_tags:
            print(f"stage 2: raw page(s) saved under {self.store.directory / 'debug'}")
            self._control_check(records)
            if newly_empty:
                # One line instead of one warning per tag. Tags already known to
                # be asset-less stay in stage 3's skipped table only, so the same
                # warning does not repeat on every run.
                self.warn(f"{len(newly_empty)} tag(s) without uploaded assets: " + ", ".join(sorted(newly_empty)))
            known = len(empty_tags) - len(newly_empty)
            if known:
                print(f"stage 2: {known} tag(s) still without uploaded assets")
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

    print(f"stage 2: {recorded}/{len(tags)} tag(s) have a record")
    lines = [
        "### stage 2 - release pages",
        "",
        f"- pages read: **{read}**, records: **{recorded}/{len(tags)}**",
        "",
    ]
    if warn.messages:
        lines += ["### stage 2 warnings", ""] + [f"- {message}" for message in warn.messages] + [""]
    write_summary("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
