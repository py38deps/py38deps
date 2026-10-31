#!/usr/bin/env python3
"""Keep one tracking issue per backported dependency in sync with upstream.

Both sides of the comparison already exist in this repository:

  * README.md "Maintained dependencies" - the row names the dependency, the
    official repository, our repository and the version recorded as backported
  * the release tags "<YYYYMMDD>-<dep>==<version>" - what has actually been
    published (AGENTS.md section 7.4); a release tag is what closes an issue,
    the README row is only used when the repository has no tag for the
    dependency at all

The upstream side is one `git ls-remote --tags --refs <official repo>` per
dependency. It needs no checkout, no submodule SSH key and no GitHub API quota,
and it sees exactly the release tags a backport merges from (AGENTS.md 1.4).

Comparing the two:

  * upstream ahead -> the dependency's tracking issue is created, or rewritten
    when the recorded versions changed. Issues are per dependency and are found
    again through the marker in their body, so a version bump never forks a
    second issue.
  * upstream caught up -> the tracking issue is closed and a comment names the
    release that closed it.

Usage:
    python -m devtool.track_backports --dry-run   # report only, no API writes
    python -m devtool.track_backports             # create/update/close issues
    python -m devtool.track_backports --dep PyAV  # a single dependency

Write path environment: GITHUB_TOKEN (or GH_TOKEN) is required;
GITHUB_REPOSITORY defaults to py38deps/py38deps and GITHUB_API_URL to
https://api.github.com.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from packaging.version import InvalidVersion, Version

from devtool.const import ROOT

README = ROOT / "README.md"
TABLE_TITLE = "## Maintained dependencies"

# Issues opened by this script carry this marker; it is the stable identity of a
# dependency across version bumps and title edits.
MARKER = "<!-- py38deps-tracker dep={name} -->"
MARKER_RE = re.compile(r"<!--\s*py38deps-tracker dep=(?P<name>[^\s>]+)\s*-->")

LABEL = "backport"
LABEL_COLOR = "0e8a16"
LABEL_DESCRIPTION = "Upstream released a version this repository has not backported yet"

DEFAULT_REPOSITORY = "py38deps/py38deps"
DEFAULT_API = "https://api.github.com"
WORKFLOW_PATH = ".github/workflows/upstream-tracker.yml"

LINK_RE = re.compile(r"\[[^\]]*\]\((https://github\.com/[^)\s]+)\)")
# Release tags in this repository: "<YYYYMMDD>-<dep>==<version>"
RELEASE_TAG_RE = re.compile(r"^(?P<date>\d{8})-(?P<dep>.+?)==(?P<version>.+)$")
# Upstream tags are free-form ("19.0.2", "v19.0.2", "4.1-retracted"); accept a
# PEP 440 version at the end of the name and ignore everything that is not one.
VERSION_SUFFIX_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)+(?:(?:a|b|rc|alpha|beta|pre|preview|dev|post)\d*)?)$")

# GitHub turns `owner/repo#123`, `#123`, `@name` and links into another repository's
# issues/pulls/commits/releases into cross-references: the referenced item gains a
# "referenced by" entry that points back at this repository. Upstream projects are
# only read here, so their timelines must stay free of our tracking issues. Version
# numbers, tag names and repository paths are therefore always written inside code
# spans (code spans are never autolinked) and the rendered text is checked again
# before anything is posted.
MENTION_RE = re.compile(r"(?<![\w`\\])@[A-Za-z0-9][-\w]*")
CROSS_REPO_RE = re.compile(r"(?<![\w`])[-.\w]+/[-.\w]+#\d+")
LOCAL_REF_RE = re.compile(r"(?<![\w`\\])#\d+")
GITHUB_LINK_RE = re.compile(
    r"github\.com/(?P<owner>[^/\s]+)/[^/\s]+/(?:issues|pull|pulls|commit|commits|releases|tags|compare|actions)/"
)
SHA_RE = re.compile(r"(?<![0-9A-Za-z])(?=[0-9a-f]{7,40}(?![0-9A-Za-z]))(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}")
# Cross-repository links are only tolerated inside our own organisation.
OWN_GITHUB_OWNER = "py38deps"

Dependency = namedtuple("Dependency", "name upstream our_repo our_dir readme_version")
Upstream = namedtuple("Upstream", "versions error")
LocalState = namedtuple("LocalState", "version tag")
# action: "behind" (issue needed), "stale" (issue can be closed), "error" (unchecked,
# fails the run), "skip" (not trackable, a warning only)
# body: the issue body when behind, the closing comment when stale
State = namedtuple("State", "dep upstream local action target missing title body note")


class Fatal(SystemExit):
    """Abort the run with a message the CI log makes obvious."""

    def __init__(self, message):
        super().__init__(f"FATAL: {message}")


def warn(message):
    """Print a warning, annotated in GitHub Actions, and keep going."""
    print(f"WARNING: {message}", file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        safe = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{safe}")


def write_summary(text):
    """Append the run result to the CI job summary (a no-op locally)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(text)


# --------------------------------------------------------------------- README


def _is_separator(cell):
    """True for the "| --- | --- |" row of a markdown table."""
    return bool(cell) and set(cell) <= set("-: ")


def read_dependencies(path):
    """Return one Dependency per row of the README "Maintained dependencies" table."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Fatal(f"cannot read {path}: {exc}") from None

    deps = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_table = stripped == TABLE_TITLE
            continue
        if not in_table or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or cells[0].casefold() == "dep name" or _is_separator(cells[0]):
            continue

        upstream = LINK_RE.search(cells[1])
        ours = LINK_RE.search(cells[2])
        if upstream is None or ours is None:
            warn(f"README row {cells[0]!r}: no GitHub link in the repo column - skipped")
            continue

        version = None
        if cells[3]:
            try:
                version = Version(cells[3])
            except InvalidVersion:
                warn(f"README row {cells[0]!r}: {cells[3]!r} is not a PEP 440 version - ignored")

        our_url = ours.group(1).rstrip("/")
        deps.append(
            Dependency(
                name=cells[0],
                upstream=upstream.group(1).rstrip("/"),
                our_repo=our_url,
                our_dir=our_url.rsplit("/", 1)[-1],
                readme_version=version,
            )
        )

    if not deps:
        raise Fatal(f"no dependency rows found under {TABLE_TITLE!r} in {path}")
    return deps


# ------------------------------------------------------------- local releases


def git(args, cwd=ROOT):
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


def local_releases(repo_dir=ROOT):
    """Map casefolded dependency name -> {Version: release tag} from this repository's tags.

    The tag's dependency part is the repo/<dep> name, which is not always the
    distribution name (python-zstandard vs zstandard, PyAV vs pyav), so lookups
    try the README name and the repository name.
    """
    releases = {}
    for tag in git(["tag", "--list"], repo_dir).stdout.split():
        match = RELEASE_TAG_RE.match(tag)
        if match is None:
            continue
        try:
            version = Version(match.group("version"))
        except InvalidVersion:
            continue
        bucket = releases.setdefault(match.group("dep").casefold(), {})
        if tag > bucket.get(version, ""):  # a re-release uses a newer date prefix
            bucket[version] = tag
    return releases


def local_state(dep, releases):
    """Newest version this repository has released for `dep`, and the tag proving it.

    Release tags are the source of truth: a tag is created together with the
    wheels (AGENTS.md 7.4), so a version counts as backported only once its tag
    exists. The README row is the fallback for a dependency without any tag,
    which keeps a freshly added row from looking like nothing was ever released.
    """
    versions = {}
    for key in {dep.name.casefold(), dep.our_dir.casefold()}:
        versions.update(releases.get(key, {}))
    if versions:
        newest = max(versions)
        return LocalState(newest, versions[newest])
    return LocalState(dep.readme_version, "")


# ---------------------------------------------------------- upstream releases


def version_from_tag(tag):
    """Parse a repository tag into a PEP 440 version, or None if it is not one."""
    candidate = tag[1:] if tag[:1] in ("v", "V") and tag[1:2].isdigit() else tag
    try:
        return Version(candidate)
    except InvalidVersion:
        pass
    match = VERSION_SUFFIX_RE.search(tag)
    if match is None:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion:
        return None


def stable_versions(tags):
    """Map Version -> tag name, dropping pre-releases and non-version tags."""
    versions = {}
    for tag in tags:
        version = version_from_tag(tag)
        if version is None or version.is_prerelease:
            continue
        if tag > versions.get(version, ""):  # "v1.2.3" and "1.2.3" can both exist
            versions[version] = tag
    return versions


def remote_tags(url, retries=4, timeout=60):
    """Return (tag names, None) for a remote repository, or (None, error)."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    error = "unknown error"
    for attempt in range(retries):
        try:
            proc = subprocess.run(
                ["git", "-c", "credential.helper=", "ls-remote", "--tags", "--refs", url],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"{type(exc).__name__}: {exc}"
        else:
            if proc.returncode == 0:
                tags = []
                for line in proc.stdout.splitlines():
                    ref = line.partition("\t")[2]
                    if ref.startswith("refs/tags/"):
                        tags.append(ref[len("refs/tags/") :])
                return tags, None
            lines = (proc.stderr or proc.stdout).strip().splitlines()
            error = lines[-1] if lines else f"git ls-remote exited with {proc.returncode}"
        if attempt + 1 < retries:
            time.sleep(2 * (attempt + 1))
    return None, error


def poll_upstreams(deps, jobs):
    """Fetch every upstream's tags in parallel; returns {dep.name: Upstream}."""
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(remote_tags, dep.upstream): dep for dep in deps}
        for future in as_completed(futures):
            dep = futures[future]
            try:
                tags, error = future.result()
            except Exception as exc:  # noqa: BLE001 - one broken upstream must not abort the run
                tags, error = None, f"{type(exc).__name__}: {exc}"
            results[dep.name] = Upstream(None if tags is None else stable_versions(tags), error)
    return results


# -------------------------------------------------------------------- issues


def issue_marker(name):
    """Body marker that makes an issue the tracking issue of `name`."""
    return MARKER.format(name=name)


def missing_versions(versions, current):
    """Upstream versions newer than `current`, oldest first."""
    return sorted(version for version in versions if current is None or version > current)


def issue_body(dep, upstream, local, target, missing):
    """Issue body: what is missing, what we have, and which release closes it.

    Every version, tag and repository path sits inside a code span on purpose:
    GitHub neither autolinks nor mentions text inside code spans, so nothing here
    can turn into a reference that would show up in the upstream project.
    """
    released = "nothing released yet" if local.version is None else f"`{local.version}`"
    if local.tag:
        released += f" (tag `{local.tag}`)"
    missing_text = ", ".join(f"`{version}`" for version in missing[:20])
    if len(missing) > 20:
        missing_text += f" and {len(missing) - 20} more"
    return f"""{issue_marker(dep.name)}

A newer upstream release is not backported in this repository yet.

- backported here: {released}
- upstream latest stable: `{target}` (tag `{upstream.versions[target]}`)
- missing releases: {missing_text}
- official repository: {dep.upstream}

Publishing `YYYYMMDD-{dep.name}=={target}` closes this issue automatically.

<!-- maintained by {WORKFLOW_PATH} - the marker above must stay -->
"""


def reference_hazards(text):
    """Return (kind, snippet) for every fragment GitHub would turn into a reference.

    `owner/repo#123`, `#123` and `@name` become cross-references or mentions, and a
    link into another repository's issues, commits or releases can be recorded as a
    reference as well. Code spans suppress autolinking, so a hit means the template
    put something outside one.
    """
    hazards = []
    for kind, pattern in (
        ("mention", MENTION_RE),
        ("cross-repository reference", CROSS_REPO_RE),
        ("issue reference", LOCAL_REF_RE),
        ("bare commit sha", SHA_RE),
    ):
        for match in pattern.finditer(text):
            hazard = (kind, match.group(0))
            if hazard not in hazards:
                hazards.append(hazard)
    for match in GITHUB_LINK_RE.finditer(text):
        if match.group("owner").casefold() != OWN_GITHUB_OWNER:
            hazards.append(("link into another repository", match.group(0)))
    return hazards


def guard_references(text):
    """None when `text` is safe to post, otherwise the reason to refuse it."""
    hazards = reference_hazards(text)
    if not hazards:
        return None
    detail = ", ".join(f"{kind} {snippet!r}" for kind, snippet in hazards[:3])
    return f"refusing to post text that references another repository ({detail})"


def close_comment(local, target):
    """Comment left on the issue when a release caught up with upstream."""
    released = f"`{local.version}`" if local.version else "A new release"
    if local.tag:
        released += f" (tag `{local.tag}`)"
    return f"{released} is published, which covers the `{target}` tracked here. Closing automatically."


def state_for(dep, upstream, local):
    """Decide what this dependency needs: nothing, an issue update, or a close."""
    if upstream.error is not None:
        # A failed lookup may hide a release, so it fails the run instead of
        # being taken as "nothing new".
        return State(
            dep, upstream, local, "error", None, [], "", "", f"could not read upstream tags ({upstream.error})"
        )
    if not upstream.versions:
        return State(dep, upstream, local, "skip", None, [], "", "", "upstream has no PEP 440 stable tag")

    target = max(upstream.versions)
    missing = missing_versions(upstream.versions, local.version)
    if not missing:
        comment = close_comment(local, target)
        hazard = guard_references(comment)
        if hazard is not None:
            return State(dep, upstream, local, "error", target, [], "", "", hazard)
        return State(dep, upstream, local, "stale", target, [], "", comment, "")

    title = f"[backport] {dep.name}: upstream {target} is not backported yet"
    body = issue_body(dep, upstream, local, target, missing)
    hazard = guard_references(f"{title}\n{body}")
    if hazard is not None:
        return State(dep, upstream, local, "error", target, missing, "", "", hazard)

    return State(dep, upstream, local, "behind", target, missing, title, body, "")


# ------------------------------------------------------------------- GitHub


class ApiError(Exception):
    """A GitHub API call failed; the message carries the response body."""

    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status


class GitHub:
    """Minimal issue API client (stdlib only, retries on server-side errors)."""

    def __init__(self, repository, token, api=DEFAULT_API, timeout=30):
        self.repository = repository
        self.token = token
        self.api = api.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, payload=None, retries=3):
        url = f"{self.api}/repos/{self.repository}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "py38deps-backport-tracker",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        error = "unknown error"
        for attempt in range(retries):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    return json.loads(body) if body else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:500]
                error = f"HTTP {exc.code}: {detail}"
                if exc.code < 500 and exc.code != 429:
                    raise ApiError(exc.code, detail) from None
            except urllib.error.URLError as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
        raise ApiError(0, error)

    def ensure_label(self):
        """Create the tracking label when the repository does not have it yet."""
        try:
            self.request("GET", f"/labels/{LABEL}")
        except ApiError as exc:
            if exc.status != 404:
                raise
            self.request("POST", "/labels", {"name": LABEL, "color": LABEL_COLOR, "description": LABEL_DESCRIPTION})

    def open_issues(self):
        """Every open issue carrying the tracking label (pull requests excluded)."""
        issues = []
        page = 1
        while True:
            batch = self.request("GET", f"/issues?state=open&labels={LABEL}&per_page=100&page={page}") or []
            issues.extend(issue for issue in batch if "pull_request" not in issue)
            if len(batch) < 100:
                return issues
            page += 1

    def create_issue(self, title, body):
        return self.request("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]})

    def update_issue(self, number, title, body):
        return self.request("PATCH", f"/issues/{number}", {"title": title, "body": body})

    def close_issue(self, number, comment):
        self.request("POST", f"/issues/{number}/comments", {"body": comment})
        return self.request("PATCH", f"/issues/{number}", {"state": "closed"})


def find_issue(issues, name):
    """The tracking issue of `name`, found by body marker (or by title as a fallback)."""
    for issue in issues:
        if issue_marker(name) in (issue.get("body") or ""):
            return issue
    prefix = f"[backport] {name}:"
    for issue in issues:
        if (issue.get("title") or "").startswith(prefix):
            return issue
    return None


# --------------------------------------------------------------------- main


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Track upstream releases that this repository has not backported yet.")
    parser.add_argument("--dry-run", action="store_true", help="report the plan, never create/update/close an issue")
    parser.add_argument("--dep", action="append", metavar="NAME", help="only this dependency (repeatable)")
    parser.add_argument("--jobs", type=int, default=6, help="parallel upstream lookups (default: 6)")
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPOSITORY,
        help=f"owner/repo holding the issues (default: {DEFAULT_REPOSITORY})",
    )
    parser.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL") or DEFAULT_API, help="GitHub API base URL"
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    all_deps = read_dependencies(README)
    deps = all_deps
    if args.dep:
        wanted = {name.casefold() for name in args.dep}
        deps = [dep for dep in all_deps if dep.name.casefold() in wanted]
        if not deps:
            raise Fatal(f"no README row matches {', '.join(args.dep)}")

    releases = local_releases()
    upstreams = poll_upstreams(deps, args.jobs)
    states = [state_for(dep, upstreams[dep.name], local_state(dep, releases)) for dep in deps]

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token is None and not args.dry_run:
        raise Fatal("GITHUB_TOKEN is not set (use --dry-run to only report the plan)")

    client = None if token is None else GitHub(args.repository, token, args.api_url)
    issues = []
    if client is not None:
        try:
            client.ensure_label()
            issues = client.open_issues()
        except ApiError as exc:
            raise Fatal(f"cannot use the issues of {args.repository}: {exc}") from None

    write = client is not None and not args.dry_run
    created = updated = closed = 0
    failures = []

    print(f"{'dependency':<22} {'released':<14} {'upstream':<14} action")
    print("-" * 68)

    for state in states:
        dep = state.dep
        released = str(state.local.version) if state.local.version is not None else "-"
        target = str(state.target) if state.target is not None else "-"
        label = f"{released:<14} {target:<14}"

        if state.action in ("skip", "error"):
            print(f"{dep.name:<22} {label} {state.action} ({state.note})")
            warn(f"{dep.name}: {state.note}")
            if state.action == "error":
                failures.append(f"{dep.name}: {state.note}")
            continue

        issue = find_issue(issues, dep.name) if client is not None else None

        if state.action == "stale":
            if issue is None:
                print(f"{dep.name:<22} {label} up to date")
                continue
            print(f"{dep.name:<22} {label} close #{issue['number']}")
            if write:
                try:
                    client.close_issue(issue["number"], state.body)
                except ApiError as exc:
                    warn(f"{dep.name}: closing issue #{issue['number']} failed ({exc})")
                    failures.append(f"{dep.name}: close failed")
                    continue
            closed += 1
            continue

        if issue is None:
            print(f"{dep.name:<22} {label} create issue")
            if write:
                try:
                    client.create_issue(state.title, state.body)
                except ApiError as exc:
                    warn(f"{dep.name}: creating the issue failed ({exc})")
                    failures.append(f"{dep.name}: create failed")
                    continue
            created += 1
            continue

        same = (issue.get("title") or "") == state.title and (issue.get("body") or "").strip() == state.body.strip()
        if same:
            print(f"{dep.name:<22} {label} issue #{issue['number']} current")
            continue

        print(f"{dep.name:<22} {label} update #{issue['number']}")
        if write:
            try:
                client.update_issue(issue["number"], state.title, state.body)
            except ApiError as exc:
                warn(f"{dep.name}: updating issue #{issue['number']} failed ({exc})")
                failures.append(f"{dep.name}: update failed")
                continue
        updated += 1

    tracked = {dep.name for dep in all_deps}
    for issue in issues:
        match = MARKER_RE.search(issue.get("body") or "")
        if match is not None and match.group("name") not in tracked:
            warn(
                f"issue #{issue['number']} tracks {match.group('name')!r}, "
                "which is not in the README table - left open"
            )

    print()
    mode = "" if write else " (dry run, no issue was written)"
    print(f"{len(deps)} dependency/dependencies: {created} created, {updated} updated, {closed} closed{mode}")
    summary = (
        "### upstream tracker\n\n"
        f"- dependencies checked: **{len(deps)}**\n"
        f"- issues created: **{created}**, updated: **{updated}**, closed: **{closed}**\n"
        f"- mode: **{'live' if write else 'dry run'}**\n"
    )
    behind = [state for state in states if state.action == "behind"]
    if behind:
        summary += "\n**Behind upstream:**\n\n" + "\n".join(
            f"- `{state.dep.name}`: released `{state.local.version or 'nothing'}`, upstream `{state.target}`"
            for state in behind
        )
        summary += "\n"
    if failures:
        summary += "\n".join(["", "**Not checked:**", *[f"- {line}" for line in failures]]) + "\n"
    write_summary(summary)

    if failures:
        print(f"{len(failures)} dependency/dependencies could not be checked", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
