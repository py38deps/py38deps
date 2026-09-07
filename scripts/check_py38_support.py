#!/usr/bin/env python3
"""Check the latest version of a hardcoded list of libraries on PyPI and
whether that version still supports Python 3.8.

For every package the script queries https://pypi.org/pypi/{package_name}/json
through a local proxy and reports, one dependency at a time:
  - the latest version and the declared requires-python
  - whether Python 3.8 is allowed by that constraint (plus a wheel-tag
    fallback when no requires-python metadata is declared)
  - when the latest version does NOT support Python 3.8, the newest release
    that still does (the highest py38-installable version)

The package list is starlette itself plus its transitive runtime dependency
closure (including the functional "full" extra), minus the libraries already
backported in this repo (repo/* submodules, see the "Maintained dependencies"
table in README.md) and the emscripten-only dependency httpx2-jsfetch.

mdurl is additionally listed as a backport candidate under watch: its latest
release still supports Python 3.8, and once a future release drops py38 support
the check will flag it for backporting.

Usage:
    python check_py38_support.py [--proxy http://host:port]

Exit code is 1 when at least one package's latest version does not support
Python 3.8 (or support is unknown), 0 otherwise.
"""

import argparse
import json
import re
import sys
import urllib.request

try:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version as _Pep440Version
except ImportError:  # pragma: no cover - fallback path for bare environments
    SpecifierSet = None
    _Pep440Version = None

PYPI = "https://pypi.org/pypi/{}/json"
DEFAULT_PROXY = "http://127.0.0.1:7890"

# starlette's dependency closure minus backported libraries,
# plus backport candidates under watch (see docstring)
PACKAGES = [
    "starlette",
    "hypercorn",
    "certifi",
    "exceptiongroup",
    "h11",
    "httpcore",
    "httpx",
    "itsdangerous",
    "jinja2",
    "markupsafe",
    "mdurl",
    "outcome",
    "pyyaml",
    "sniffio",
    "colorama",
    "taskgroup",
    "tomli",
    "sortedcontainers",
    "typing-extensions",
]

# Candidate versions probed against a specifier: "3.8" itself and "3.8.99",
# so that a lower bound like ">=3.8.1" still counts as py38-compatible.
PROBE_VERSIONS = ("3.8", "3.8.99")


def _parse_version(text):
    """Parse a PEP 440-ish version into a tuple of ints, dropping trailing
    zeros after the second component (3.8.0 == 3.8). Returns None on failure."""
    text = text.strip().lstrip("vV")
    if text.endswith(".*"):
        text = text[:-2]
    parts = []
    for piece in re.split(r"[._-]", text):
        if not piece.isdigit():
            return None
        parts.append(int(piece))
    while len(parts) > 2 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _cmp(a, b):
    """Compare two version tuples, padding the shorter one with zeros."""
    width = max(len(a), len(b))
    a = a + (0,) * (width - len(a))
    b = b + (0,) * (width - len(b))
    return (a > b) - (a < b)


def _legacy_clause_allows(clause, version):
    """Evaluate one comma-separated clause of a specifier against a version
    tuple, using only the operators seen in practice on PyPI. Returns None
    when the clause cannot be parsed."""
    match = re.match(r"^\s*(==|!=|<=|>=|<|>|~=)?\s*([vV]?[0-9][0-9._*]*)", clause)
    if not match:
        return None
    op, spec_text = match.group(1) or "==", match.group(2).lstrip("vV")
    spec = _parse_version(spec_text)
    if spec is None:
        return None
    wildcard = spec_text.endswith(".*")

    if op == "~=":
        # ~= 3.8    -> >=3.8, ==3.8.*
        # ~= 3.8.1  -> >=3.8.1, ==3.8.*
        upper = spec[:-1] + (spec[-1] + 1,)
        return _cmp(version, spec) >= 0 and _cmp(version, upper) < 0

    if wildcard:
        upper = spec[:-1] + (spec[-1] + 1,)
        in_range = _cmp(version, spec) >= 0 and _cmp(version, upper) < 0
        if op in ("==", "!="):
            return in_range if op == "==" else not in_range
        return None  # wildcard with other operators: too rare to guess

    if op == "==":
        return _cmp(version, spec) == 0
    if op == "!=":
        return _cmp(version, spec) != 0
    if op == ">=":
        return _cmp(version, spec) >= 0
    if op == "<=":
        return _cmp(version, spec) <= 0
    if op == ">":
        return _cmp(version, spec) > 0
    if op == "<":
        return _cmp(version, spec) < 0
    return None


def allows_py38(specifier):
    """Return True/False/None (unknown) for whether `specifier` permits 3.8."""
    if not specifier:
        return True  # no constraint declared
    if SpecifierSet is not None:
        try:
            spec_set = SpecifierSet(specifier)
        except Exception:  # noqa: BLE001 - fall back to the legacy parser
            spec_set = None
        if spec_set is not None:
            return any(spec_set.contains(v, prereleases=True) for v in PROBE_VERSIONS)
    results = []
    for probe in ((3, 8), (3, 8, 99)):
        verdicts = [_legacy_clause_allows(c, probe) for c in specifier.split(",")]
        if verdicts and all(v is True for v in verdicts):
            results.append(True)
        elif any(v is False for v in verdicts):
            results.append(False)
        else:
            results.append(None)
    if any(v is True for v in results):
        return True
    if any(v is False for v in results):
        return False
    return None


def _release_py38_support(files, declared_rp):
    """Determine whether one release's metadata allows Python 3.8.

    Priority: declared requires-python, per-file requires-python, then wheel
    tags of the release (py3-none-any or cp38). Returns True/False/None
    (unknown).
    """
    specs = [declared_rp]
    specs.extend(f.get("requires_python") for f in files)
    specs = [s for s in specs if s]
    if specs:
        verdicts = [allows_py38(s) for s in specs]
        if any(v is True for v in verdicts):
            return True
        if any(v is False for v in verdicts):
            return False
        return None

    for f in files:
        name = f.get("filename", "")
        if name.endswith(".whl") and (
            "py3-none-any" in name or re.search(r"cp38[._-]cp38", name)
        ):
            return True
    return None


def py38_support(data):
    """Determine py38 support of the latest release (see _release_py38_support)."""
    info = data["info"]
    files = data["releases"].get(info["version"], [])
    return _release_py38_support(files, info.get("requires_python"))


def _version_sort_key(version):
    """PEP 440 sort key for release version strings; unparseable versions
    are sorted after parseable ones."""
    if _Pep440Version is not None:
        try:
            return (0, _Pep440Version(version))
        except Exception:  # noqa: BLE001 - try the legacy parser next
            pass
    parsed = _parse_version(version)
    if parsed is not None:
        return (1, parsed)
    return (2, version)


def last_py38_supported_version(data):
    """Return (version, requires_python) of the newest release whose metadata
    still allows Python 3.8, or (None, None) when no release is confirmed to
    support 3.8. Yanked releases are skipped (pip would not install them).
    """
    latest = data["info"]["version"]
    candidates = [v for v, files in data["releases"].items() if files]
    for version in sorted(candidates, key=_version_sort_key, reverse=True):
        files = data["releases"][version]
        if all(f.get("yanked") for f in files):
            continue
        declared = data["info"].get("requires_python") if version == latest else None
        if _release_py38_support(files, declared) is True:
            rp = next(
                (f["requires_python"] for f in files if f.get("requires_python")),
                None,
            )
            return version, rp
    return None, None


def fetch_package(name, proxy):
    """Download https://pypi.org/pypi/{name}/json through the proxy."""
    request = urllib.request.Request(
        PYPI.format(name), headers={"User-Agent": "py38deps-check/1.0"}
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    )
    with opener.open(request, timeout=30) as resp:
        return json.load(resp)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help="proxy URL (default: %(default)s)")
    args = parser.parse_args()

    print(f"Checking {len(PACKAGES)} packages via {args.proxy}\n")
    errors = []
    not_supported = []
    unknown = []
    for name in PACKAGES:
        try:
            data = fetch_package(name, args.proxy)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            errors.append((name, str(exc)))
            print(f"{name}\n  !! failed to fetch: {exc}")
            sys.stdout.flush()
            continue
        info = data["info"]
        version = info["version"]
        requires_python = info.get("requires_python") or ""
        support = py38_support(data)
        label = {True: "YES", False: "NO", None: "unknown"}[support]
        print(f"{name}")
        print(f"  latest: {version}   requires-python: {requires_python or 'unset'}   py38: {label}")
        if support is False:
            last_version, last_rp = last_py38_supported_version(data)
            if last_version:
                last_rp_text = f" (requires-python {last_rp})" if last_rp else ""
                print(f"  last py38 version: {last_version}{last_rp_text}")
            else:
                print("  last py38 version: (none found / unknown)")
            not_supported.append((name, version, last_version))
        elif support is None:
            unknown.append(name)
        sys.stdout.flush()

    if errors:
        print("\nErrors:")
        for name, message in errors:
            print(f"  !! {name}: {message}")

    total = len(PACKAGES)
    supported = total - len(not_supported) - len(unknown) - len(errors)
    print(f"\n{total} checked, {supported} support py38, "
          f"{len(not_supported)} do not, {len(unknown)} unknown")
    if not_supported or unknown:
        print("Latest versions that do not support (or may not support) py38:")
        for name, version, last_version in not_supported:
            last = last_version if last_version else "-"
            print(f"  - {name} {version}: py38 NO, last py38 version {last}")
        for name in unknown:
            print(f"  - {name}: py38 unknown")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
