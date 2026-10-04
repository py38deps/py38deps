"""Stage 3: turn the cached release pages into the static PyPI index.

Reads pypisite/cache/tags.json (stage 1) plus one record per tag (stage 2) and
writes pypisite/_site/ - nothing but HTML:

    index.html                 landing page, carries the tree digest
    simple/index.html          PEP 503 root index
    simple/<name>/index.html   one page per project, links to the release assets
    flat/index.html            every wheel on one page, for --find-links
    .nojekyll

Every anchor points at the wheel's own GitHub release download URL and carries
the sha256 recorded in stage 2; no binary is copied or proxied. The version
declared by the tag is checked against the wheel filenames, every released
version is kept, and the tree is rebuilt from scratch each run so a project that
left the index cannot leave a stale page behind.

Also written (never published): pypisite/cache/report.json, site.digest and
site.changed - the last one tells the workflow whether a deployment is needed.

    python pypisite/build.py
    python pypisite/build.py --inspect            # one line per tag: facts + verdict
    python pypisite/build.py --inspect TAG        # the whole record of one tag
    python pypisite/build.py --verify-urls        # HEAD-check every wheel URL
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

from common import (
    LIVE_URL,
    REPO,
    SITE_DIR,
    USAGE_INDEX_URL,
    CacheStore,
    Fatal,
    WarningLog,
    write_summary,
    write_text,
)

USER_AGENT = "py38deps-index-builder (+https://github.com/py38deps/py38deps)"
NETWORK_ATTEMPTS = 3
MAX_SKIP_RATIO = 0.5
WORKERS = 8


class SiteBuilder:
    """Assembles the index entries and writes the published tree."""

    def __init__(self, store=None, warn=None, out_dir=None, repo=None, usage_index_url=None, live_url=None):
        self.store = store if store is not None else CacheStore()
        self.warn = warn if warn is not None else WarningLog()
        self.out_dir = Path(out_dir) if out_dir is not None else SITE_DIR
        self.repo = repo if repo is not None else REPO
        self.usage_index_url = usage_index_url if usage_index_url is not None else USAGE_INDEX_URL
        self.live_url = live_url if live_url is not None else LIVE_URL

    # ------------------------------------------------------------ interpretation

    def entries_for_tag(self, tag_record, record):
        """Interpret one cache record; returns (records, skip_reason)."""
        tag = tag_record["tag"]
        declared = tag_record.get("version")

        rows = record.get("assets") or []
        wheels = [(name, digest) for name, digest in rows if name.endswith(".whl")]
        if not wheels:
            return [], "no uploaded assets"
        without_digest = [name for name, digest in wheels if not digest]
        if without_digest:
            self.warn(f"[{tag}] {len(without_digest)} wheel(s) have no sha256 on the release page - served without a hash")

        entries = []
        versions = set()
        for name, digest in wheels:
            try:
                project, version, _build, _tags = parse_wheel_filename(name)
            except Exception as exc:  # noqa: BLE001 - one bad filename must not kill the run
                self.warn(f"[{tag}] unparsable wheel filename {name!r} ({exc}) - file ignored")
                continue
            versions.add(str(version))
            entries.append(
                {
                    "tag": tag,
                    "project": str(canonicalize_name(project)),
                    "version": str(version),
                    "filename": name,
                    "sha256": digest,
                    "url": "https://github.com/{}/releases/download/{}/{}".format(
                        self.repo, urllib.parse.quote(tag, safe=""), urllib.parse.quote(name)
                    ),
                }
            )
        if not entries:
            return [], "no parsable wheel assets"
        if declared is not None and versions != {declared}:
            return [], f"wheel version {sorted(versions)} does not match version {declared} declared by the tag"
        return entries, None

    def assemble(self, payload):
        """Walk the effective tags newest-first and collect the index entries."""
        accepted = {}
        skipped = []
        for tag_record in payload["tags"]:
            tag = tag_record["tag"]
            record = self.store.read_record(tag)
            if record is None:
                raise Fatal(f"no cache record for tag {tag} - run pypisite/fetch.py first")

            entries, reason = self.entries_for_tag(tag_record, record)
            if reason is not None:
                skipped.append((tag, reason))
                if reason != "no uploaded assets":  # stage 2 already reported those
                    self.warn(f"[{tag}] {reason} - tag skipped")
                continue

            for entry in entries:
                key = (entry["project"], entry["version"])
                current = accepted.get(key)
                if current is None:
                    accepted[key] = {"tag": tag, "records": [entry]}
                elif current["tag"] == tag:
                    current["records"].append(entry)

        # Stage 1 dropped an older tag for a version that was released twice; if
        # the newer tag has no assets yet, that version is missing from the index
        # - say it out loud.
        for dropped in payload.get("superseded", []):
            kept = self.store.read_record(dropped["by"])
            if kept is None or not kept.get("found"):
                self.warn(
                    f"{dropped['dep']} {dropped['version']}: the older tag {dropped['tag']} was dropped as "
                    f"superseded and the newer tag {dropped['by']} has no usable assets yet - this version is "
                    f"not in the index (publish its release assets, or re-run stage 2 with --refetch)"
                )

        if not accepted:
            raise Fatal("every tag was skipped - the release page layout probably changed")
        total = len(payload["tags"])
        if len(skipped) > total * MAX_SKIP_RATIO:
            raise Fatal(f"{len(skipped)}/{total} tags skipped, above the {MAX_SKIP_RATIO:.0%} guard")
        return accepted, skipped

    # ------------------------------------------------------------------- the site

    def _landing_page(self, names, wheels, digest):
        return "\n".join(
            [
                "<!DOCTYPE html>",
                "<html>",
                "  <head>",
                "    <title>py38deps wheel mirror</title>",
                f'    <meta name="py38deps:index-digest" content="{digest}">' if digest else "    <!-- index digest -->",
                "  </head>",
                "  <body>",
                "    <h1>py38deps wheel mirror</h1>",
                f"    <p>{names} projects, {wheels} wheels. "
                f'Binaries live on <a href="https://github.com/{self.repo}/releases">GitHub Releases</a>.</p>',
                f'    <pre>pip install --extra-index-url {html.escape(self.usage_index_url)} &lt;package&gt;</pre>',
                '    <p>Simple index: <a href="simple/">simple/</a> &middot; '
                'find-links page: <a href="flat/">flat/</a></p>',
                "  </body>",
                "</html>",
                "",
            ]
        )

    def write_site(self, accepted):
        """Write the index pages; returns (project names, wheel count, tree digest)."""
        per_project = defaultdict(list)
        for entry in accepted.values():
            per_project[entry["records"][0]["project"]].extend(entry["records"])

        # Rebuild from scratch: a project that is no longer indexed must not keep
        # a stale page around (pip would happily install from it).
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / ".nojekyll").write_text("", encoding="utf-8")
        simple = self.out_dir / "simple"
        simple.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "flat").mkdir(parents=True, exist_ok=True)

        names = sorted(per_project)
        write_text(
            simple / "index.html",
            render_page(
                "py38deps simple index", [f'<a href="{html.escape(name)}/">{html.escape(name)}</a><br>' for name in names]
            ),
        )

        flat_anchors = []
        for name in names:
            records = sorted(per_project[name], key=lambda record: record["filename"])
            (simple / name).mkdir(parents=True, exist_ok=True)
            anchors = [
                '<a href="{}{}">{}</a><br>'.format(
                    html.escape(record["url"], quote=True),
                    f'#sha256={record["sha256"]}' if record["sha256"] else "",
                    html.escape(record["filename"]),
                )
                for record in records
            ]
            write_text(simple / name / "index.html", render_page(f"Links for {name}", anchors))
            flat_anchors.extend(anchors)
        write_text(self.out_dir / "flat" / "index.html", render_page("py38deps wheels", flat_anchors))

        wheels = sum(len(v) for v in per_project.values())
        # The digest covers every file plus the landing page with an empty digest,
        # so it does not depend on itself and still changes when the page does.
        digest = tree_digest(self.out_dir, self._landing_page(names, wheels, ""))
        write_text(self.out_dir / "index.html", self._landing_page(names, wheels, digest))
        return names, wheels, digest

    # ------------------------------------------------------------------ deploy

    def live_digest(self):
        """Digest published by the current deployment, or None when it cannot be read."""
        if not self.live_url:
            return None
        request = urllib.request.Request(self.live_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - an unreadable site just means "deploy anyway"
            return None
        match = re.search(r'name="py38deps:index-digest" content="([0-9a-f]{64})"', body)
        return match.group(1) if match else None

    def run(self, payload):
        """Build the site, the report and the deploy state."""
        accepted, skipped = self.assemble(payload)
        names, wheels, digest = self.write_site(accepted)
        live = self.live_digest()
        changed = self.store.write_deploy_state(digest, live != digest)
        report = self.store.write_report(
            {
                "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "effective_tags": len(payload["tags"]),
                "superseded": payload.get("superseded", []),
                "projects": len(names),
                "wheels": wheels,
                "skipped": [{"tag": tag, "reason": reason} for tag, reason in skipped],
                "warnings": self.warn.messages,
                "site_digest": digest,
                "live_digest": live,
                "site_changed": changed,
            }
        )
        print(
            f"projects={len(names)} wheels={wheels} tags={len(payload['tags'])} skipped={len(skipped)} out={self.out_dir}\n"
            f"site digest={digest[:12]} live={live[:12] if live else 'unknown'} changed={changed}\n"
            f"report={report}"
        )

        lines = [
            "### stage 3 - index",
            "",
            f"- effective tags: **{len(payload['tags'])}**, superseded tags: **{len(payload.get('superseded', []))}**",
            f"- projects: **{len(names)}**, wheels: **{wheels}**",
            f"- skipped tags: **{len(skipped)}**",
            f"- site changed: **{'yes' if changed else 'no (deployment skipped)'}**",
            f"- site digest: `{digest[:12]}`, live: `{live[:12] if live else 'unknown'}`",
            "",
        ]
        if skipped:
            lines += ["| tag | reason |", "| --- | --- |"] + [f"| `{tag}` | {reason} |" for tag, reason in skipped] + [""]
        if self.warn.messages:
            lines += ["### stage 3 warnings", ""] + [f"- {message}" for message in self.warn.messages] + [""]
        write_summary("\n".join(lines))
        return 0


# ------------------------------------------------------------------------- output


def render_page(title, anchors):
    lines = [
        "<!DOCTYPE html>",
        "<html>",
        "  <head>",
        '    <meta name="pypi:repository-version" content="1.0">',
        f"    <title>{html.escape(title)}</title>",
        "  </head>",
        "  <body>",
        f"    <h1>{html.escape(title)}</h1>",
        *[f"    {line}" for line in anchors],
        "  </body>",
        "</html>",
        "",
    ]
    return "\n".join(lines)


def tree_digest(out_dir, landing):
    """Deterministic digest of the site contents (used to skip needless deployments)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in out_dir.rglob("*") if p.is_file() and p != out_dir / "index.html"):
        digest.update(path.relative_to(out_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    digest.update(b"index.html\0")
    digest.update(hashlib.sha256(landing.encode("utf-8")).digest())
    return digest.hexdigest()


# -------------------------------------------------------------------------- debug


def inspect_cache(store, payload, wanted):
    """Print what is recorded: one tag in full, or a one-line verdict per tag."""
    builder = SiteBuilder(store, warn=lambda message: None)
    if wanted:
        record = store.read_record(wanted)
        if record is None:
            print(f"{wanted}: no record yet - run pypisite/fetch.py to read its release page")
            return 0
        print(json.dumps(record, ensure_ascii=False, indent=1))
        declared = wanted.split("==", 1)[-1]
        entries, reason = builder.entries_for_tag({"tag": wanted, "version": declared}, record)
        print(f"verdict: skipped ({reason})" if reason else f"verdict: contributes {len(entries)} wheel(s)")
        for entry in entries:
            print(f"  {entry['filename']}\n    {entry['url']}")
        return 0

    print(f"{len(payload['tags'])} effective tag(s), records in {store.directory}")
    for tag_record in payload["tags"]:
        tag = tag_record["tag"]
        record = store.read_record(tag)
        if record is None:
            print(f"  {tag:36s} no record yet - run pypisite/fetch.py")
            continue
        entries, reason = builder.entries_for_tag(tag_record, record)
        verdict = f"skipped: {reason}" if reason else f"ok ({len(entries)} wheels)"
        print(f"  {tag:36s} found={str(record['found']):5s} assets={len(record['assets']):4d}  {verdict}")
    return 0


def url_state(url, attempts=NETWORK_ATTEMPTS):
    """HEAD one download URL: True live, False gone, None unknown (network problems)."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return 200 <= response.status < 400
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                return False
        except Exception:  # noqa: BLE001 - reported as "unknown"
            pass
        if attempt + 1 < attempts:
            time.sleep(2 * (attempt + 1))
    return None


def verify_urls(accepted):
    """HEAD-check every wheel URL of the assembled index."""
    urls = sorted({entry["url"] for data in accepted.values() for entry in data["records"]})
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        states = list(pool.map(url_state, urls))
    dead = [url for url, state in zip(urls, states) if state is False]
    unknown = [url for url, state in zip(urls, states) if state is None]
    return urls, dead, unknown


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", nargs="?", const="", default=None, help="show records (all tags, or one) and the verdict")
    parser.add_argument("--verify-urls", action="store_true", help="HEAD-check every wheel URL of the assembled index")
    args = parser.parse_args(argv)

    store = CacheStore()
    payload = store.read_tags()
    builder = SiteBuilder(store, WarningLog())

    if args.inspect is not None:
        return inspect_cache(store, payload, args.inspect)

    if args.verify_urls:
        accepted, _skipped = builder.assemble(payload)
        urls, dead, unknown = verify_urls(accepted)
        print(f"checked {len(urls)} URL(s): {len(dead)} gone, {len(unknown)} unknown")
        for url in dead:
            print(f"  GONE    {url}")
        for url in unknown[:20]:
            print(f"  UNKNOWN {url}")
        print("use --refetch <tag> (stage 2) to re-read a tag whose assets went away")
        return 1 if dead else 0

    return builder.run(payload)


if __name__ == "__main__":
    raise SystemExit(main())
