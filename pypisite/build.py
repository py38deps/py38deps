"""Stage 3: turn the cached release pages into the static PyPI index.

Reads pypisite/cache/tags.json (stage 1) plus one record per tag (stage 2) and
writes pypisite/_site/ - nothing but HTML:

    index.html                 landing page, carries the tree digest
    simple/index.html          PEP 503 root index
    simple/<name>/index.html   one page per project, links to the release assets
    flat/index.html            one section per dependency/version, for --find-links
    .nojekyll

The pages are written by alasio.codegen.pypisite, the generator the pack server
uses: simple_site() writes the page of one project, flat_site() the page a
person reads, and the two pages that are not part of a pip index are written
with the generic generator of the same package. Only the link target is local to
this repository: a wheel lives on its GitHub release, so ReleaseLink answers the
release asset url instead of a path inside the served folder (see the class).

Every anchor carries the sha256 recorded in stage 2; no binary is copied or
proxied. The version declared by the tag is checked against the wheel filenames,
every released version is kept, and the tree is rebuilt from scratch each run so
a project that left the index cannot leave a stale page behind.

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

from alasio.codegen.html import Element, HtmlGen
from alasio.codegen.pypisite import WheelLink, simple_site
from alasio.codegen.pypisite.flat import flat_site
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
)

USER_AGENT = "py38deps-index-builder (+https://github.com/py38deps/py38deps)"
NETWORK_ATTEMPTS = 3
MAX_SKIP_RATIO = 0.5
WORKERS = 8

# Titles of the three pages of the site; the pages pip reads take their own
# heading from the generator ('Links for <name>')
SITE_TITLE = "py38deps wheel mirror"
ROOT_TITLE = "py38deps simple index"
FLAT_TITLE = "py38deps wheels"

# The version of the simple api a page speaks, PEP 629; the pages pip reads get
# it from the generator, the root index is the one written here
REPOSITORY_VERSION = "1.0"


class ReleaseLink(WheelLink):
    """One wheel of the index, linked at the release asset that holds it.

    WheelLink builds its link from the path of a file inside the folder the page
    is served from, because the pack server publishes the pages and the wheels
    together. This index publishes the pages alone: the pages are served by
    GitHub Pages and the wheels stay on the GitHub Releases of their tag, so the
    link of a page is an absolute url and the folder layout does not apply.

    Everything else is the check WheelLink makes: the name is a distribution
    name, the version and the file name are single url path segments, and the
    digest is a lowercase sha256 the link carries for pip to verify.

    Attributes:
        url (str): Absolute url of the file; href() appends the '#sha256=' pip
            reads, so the link and its digest stay one fact

    Raises:
        ValueError: If the url is not an absolute https url, or on every rule of
            WheelLink
    """

    url: str = ''

    def __post_init__(self):
        super().__post_init__()
        if not self.url.startswith('https://'):
            raise ValueError(f'The url of the wheel "{self.filename}" is not an absolute https url: {self.url!r}')

    def href(self, with_name=False):
        """
        The link of the wheel on a page.

        Args:
            with_name (bool): Unused, the url of the release asset is the same on
                every page. Kept for the signature of WheelLink.href()

        Returns:
            str: The absolute url, with '#sha256=' appended when the link has one
        """
        if self.sha256:
            return f'{self.url}#sha256={self.sha256}'
        return self.url


def release_url(repo, tag, filename):
    """
    The download url of one asset of a release.

    Args:
        repo (str): owner/name of the repository
        tag (str): Release tag, quoted as a single path segment
        filename (str): File name of the asset

    Returns:
        str: Absolute url pip downloads the wheel from
    """
    return "https://github.com/{}/releases/download/{}/{}".format(
        repo, urllib.parse.quote(tag, safe=""), urllib.parse.quote(filename)
    )


def release_page_url(repo, tag):
    """
    The page of one release, the source the flat page names under a version.

    Args:
        repo (str): owner/name of the repository
        tag (str): Release tag, quoted as a single path segment

    Returns:
        str: Absolute url of the release page
    """
    return f"https://github.com/{repo}/releases/tag/{urllib.parse.quote(tag, safe='')}"


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
        """Interpret one cache record; returns (links, skip_reason)."""
        tag = tag_record["tag"]
        declared = tag_record.get("version")

        rows = record.get("assets") or []
        wheels = [(name, digest) for name, digest in rows if name.endswith(".whl")]
        if not wheels:
            return [], "no uploaded assets"
        without_digest = [name for name, digest in wheels if not digest]
        if without_digest:
            self.warn(f"[{tag}] {len(without_digest)} wheel(s) without sha256 - served unhashed")

        links = []
        versions = set()
        for name, digest in wheels:
            try:
                project, version, _build, _tags = parse_wheel_filename(name)
            except Exception as exc:  # noqa: BLE001 - one bad filename must not kill the run
                self.warn(f"[{tag}] unparsable wheel filename {name!r} ({exc}) - ignored")
                continue
            versions.add(str(version))
            links.append(
                ReleaseLink(
                    str(canonicalize_name(project)),
                    str(version),
                    name,
                    digest or "",
                    url=release_url(self.repo, tag, name),
                )
            )
        if not links:
            return [], "no parsable wheel assets"
        if declared is not None and versions != {declared}:
            return [], f"wheel version {','.join(sorted(versions))} != tag version {declared}"
        return links, None

    def assemble(self, payload):
        """Walk the effective tags newest-first and collect the index entries."""
        accepted = {}
        skipped = []
        for tag_record in payload["tags"]:
            tag = tag_record["tag"]
            record = self.store.read_record(tag)
            if record is None:
                raise Fatal(f"no cache record for tag {tag} - run pypisite/fetch.py first")

            links, reason = self.entries_for_tag(tag_record, record)
            if reason is not None:
                skipped.append((tag, reason))
                if reason != "no uploaded assets":  # stage 2 already reported those
                    self.warn(f"[{tag}] skipped: {reason}")
                continue

            for link in links:
                key = (link.key, link.version)
                current = accepted.get(key)
                if current is None:
                    accepted[key] = {"tag": tag, "links": [link]}
                elif current["tag"] == tag:
                    current["links"].append(link)

        # Stage 1 dropped the older tag of a re-released version; if the newer
        # tag has no assets yet, that version is missing from the index. One
        # line, because the skipped table below already names the tags.
        missing = [
            f"{dropped['dep']} {dropped['version']}"
            for dropped in payload.get("superseded", [])
            if not (self.store.read_record(dropped["by"]) or {}).get("found")
        ]
        if missing:
            self.warn("missing from the index until their release assets appear: " + ", ".join(missing))

        if not accepted:
            raise Fatal("every tag was skipped - the release page layout probably changed")
        total = len(payload["tags"])
        if len(skipped) > total * MAX_SKIP_RATIO:
            raise Fatal(f"{len(skipped)}/{total} tags skipped, above the {MAX_SKIP_RATIO:.0%} guard")
        return accepted, skipped

    # ------------------------------------------------------------------- the site

    def _root_page(self, names):
        """Build the PEP 503 root index: one link per project, in name order."""
        gen = HtmlGen()
        head = [Element(gen, "meta", name="pypi:repository-version", content=REPOSITORY_VERSION)]
        with gen.Doc(ROOT_TITLE, head=head) as page, page.body():
            gen.H1(ROOT_TITLE)
            for name in names:
                with gen.Line():
                    gen.A(name, href=f"{name}/")
                    gen.Br()
        return gen

    def _landing_page(self, names, wheels, digest):
        """Build the landing page; the digest is a head meta so a run spots a live copy."""
        gen = HtmlGen()
        head = []
        if digest:
            head.append(Element(gen, "meta", name="py38deps:index-digest", content=digest))
        with gen.Doc(SITE_TITLE, head=head) as page, page.body():
            gen.H1(SITE_TITLE)
            gen.P(
                f"{len(names)} projects, {wheels} wheels. Binaries live on ",
                Element(gen, "a", "GitHub Releases", href=f"https://github.com/{self.repo}/releases"),
                ".",
            ).wrap("inline")
            gen.Pre(
                Element(gen, "code", f"pip install --extra-index-url {self.usage_index_url} <package>")
            ).wrap("inline")
            gen.P(
                "Simple index: ",
                Element(gen, "a", "simple/", href="simple/"),
                " · find-links page: ",
                Element(gen, "a", "flat/", href="flat/"),
            ).wrap("inline")
        return gen

    def write_site(self, accepted):
        """Write the index pages; returns (project names, wheel count, tree digest)."""
        projects = defaultdict(list)
        origins = defaultdict(dict)
        for (project, version), data in accepted.items():
            projects[project].extend(data["links"])
            # the flat page names where the wheels of a version come from; the
            # tag is the release the assets are attached to
            origins[project][version] = (data["tag"], release_page_url(self.repo, data["tag"]))

        # Rebuild from scratch: a project that is no longer indexed must not keep
        # a stale page around (pip would happily install from it).
        if self.out_dir.exists():
            shutil.rmtree(self.out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / ".nojekyll").write_text("", encoding="utf-8")
        simple = self.out_dir / "simple"
        simple.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "flat").mkdir(parents=True, exist_ok=True)

        names = sorted(projects)
        self._root_page(names).write(simple / "index.html")
        for name in names:
            simple_site(name, projects[name]).write(simple / name / "index.html")
        flat_site(
            {name: projects[name] for name in names},
            FLAT_TITLE,
            command=f"pip install --no-index --find-links {self.live_url}flat/ <package>",
            origins={name: origins[name] for name in names},
        ).write(self.out_dir / "flat" / "index.html")

        wheels = sum(len(links) for links in projects.values())
        # The digest covers every file plus the landing page with an empty digest,
        # so it does not depend on itself and still changes when the page does.
        digest = tree_digest(self.out_dir, self._landing_page(names, wheels, "").generate_str())
        self._landing_page(names, wheels, digest).write(self.out_dir / "index.html")
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
            f"stage 3: projects={len(names)} wheels={wheels} tags={len(payload['tags'])} skipped={len(skipped)} "
            f"changed={changed} digest={digest[:12]} live={live[:12] if live else 'unknown'}\n"
            f"stage 3: report={report}"
        )

        lines = [
            "### stage 3 - index",
            "",
            f"- **{len(names)}** projects, **{wheels}** wheels from **{len(payload['tags'])}** tags",
            f"- site **{'changed' if changed else 'unchanged (no deployment)'}**: "
            f"`{digest[:12]}` vs live `{live[:12] if live else 'unknown'}`",
            "",
        ]
        if skipped:
            lines += ["| skipped tag | reason |", "| --- | --- |"] + [f"| `{tag}` | {reason} |" for tag, reason in skipped] + [""]
        if self.warn.messages:
            lines += ["### stage 3 warnings", ""] + [f"- {message}" for message in self.warn.messages] + [""]
        write_summary("\n".join(lines))
        return 0


# ------------------------------------------------------------------------- output


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
        links, reason = builder.entries_for_tag({"tag": wanted, "version": declared}, record)
        print(f"verdict: skipped ({reason})" if reason else f"verdict: contributes {len(links)} wheel(s)")
        for link in links:
            print(f"  {link.filename}\n    {link.url}")
        return 0

    print(f"{len(payload['tags'])} effective tag(s), records in {store.directory}")
    for tag_record in payload["tags"]:
        tag = tag_record["tag"]
        record = store.read_record(tag)
        if record is None:
            print(f"  {tag:36s} no record yet - run pypisite/fetch.py")
            continue
        links, reason = builder.entries_for_tag(tag_record, record)
        verdict = f"skipped: {reason}" if reason else f"ok ({len(links)} wheels)"
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
    urls = sorted({link.url for data in accepted.values() for link in data["links"]})
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
