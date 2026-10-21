"""Render the human-readable --find-links page.

The page is produced as a stream of HTML chunks: FlatPageBuilder.render()
yields lines/sections and build() joins them once at the end. pip only reads
the anchors, so the page is free to arrange the wheels for humans.
"""

from __future__ import annotations

import html
import urllib.parse
from collections import defaultdict

from packaging.utils import parse_wheel_filename
from packaging.version import Version

FLAT_FAMILY_ORDER = {"manylinux": 0, "musllinux": 1, "macosx": 2, "win": 3, "ios": 4, "other": 5}

FLAT_STYLE = """
:root { --fg:#1f2328; --muted:#59636e; --faint:#8c959f; --line:#d1d9e0; --soft:#e6eaef;
        --bg:#fff; --head:#f6f8fa; --accent:#0969da; --pure:#dafbe1; --pure-line:#aceebb;
        --pure-fg:#116329; --compiled:#ddf4ff; --compiled-line:#b6e3ff; --compiled-fg:#0550ae;
        --abi3:#8250df; }
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
       font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
main, header, nav, footer { max-width:1180px; margin:0 auto; }
h1 { font-size:2rem; margin:0 0 .25rem; letter-spacing:-.02em; }
h2 { font-size:1.35rem; margin:2.5rem 0 .4rem; padding-bottom:.3rem; border-bottom:1px solid var(--line); }
h3 { font-size:1.05rem; margin:1.6rem 0 .2rem; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
code, pre, .plat, .pure-file { font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
pre { background:var(--head); border:1px solid var(--line); border-radius:6px; padding:.6rem .8rem;
      overflow-x:auto; font-size:.85rem; }
.lede { color:var(--muted); margin:.2rem 0 1rem; }
.badge { display:inline-block; font-size:.7rem; font-weight:600; padding:.05rem .4rem;
         border:1px solid var(--line); border-radius:999px; color:var(--muted); vertical-align:.12em; }
.badge.pure { background:var(--pure); border-color:var(--pure-line); color:var(--pure-fg); }
.badge.compiled { background:var(--compiled); border-color:var(--compiled-line); color:var(--compiled-fg); }
.meta { color:var(--muted); font-size:.85rem; margin:.1rem 0 .65rem; }
.anchor { color:var(--faint); opacity:.45; font-weight:400; margin-left:.25rem; }
.anchor:hover { opacity:1; }
.toc { list-style:none; padding:0; margin:.4rem 0 0; }
.toc li { display:block; margin:.25rem 0; }
.toc .versions { color:var(--muted); font-size:.82rem; margin-left:.45rem; }
.toc .versions a { color:var(--muted); }
.toc .versions a:hover { color:var(--accent); }
.release-tag { font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace;
               font-size:.78rem; font-weight:400; color:var(--muted); margin-left:.4rem; }
.release-tag:hover { color:var(--accent); }
.table-wrap { width:fit-content; max-width:100%; overflow-x:auto;
              border:1px solid var(--line); border-radius:6px; }
table.matrix { border-collapse:collapse; table-layout:auto; width:max-content; font-size:.8rem; }
table.matrix th, table.matrix td { border-right:1px solid var(--soft); border-bottom:1px solid var(--soft);
                                   padding:.3rem .5rem; text-align:center; white-space:nowrap; }
table.matrix th:first-child { width:320px; padding:0; }
table.matrix th:last-child, table.matrix td:last-child { border-right:0; }
table.matrix tbody tr:last-child th, table.matrix tbody tr:last-child td { border-bottom:0; }
table.matrix thead th { background:var(--head); font-weight:600; font-size:.78rem; }
table.matrix tbody th { background:#fbfcfd; text-align:left; font-weight:500; white-space:normal;
                        overflow-wrap:anywhere; }
table.matrix td.na { background:#fafbfc; }
.platform-head { display:block; box-sizing:border-box; width:320px; padding:.3rem .5rem; font-size:.78rem; }
.plat { display:block; box-sizing:border-box; width:320px; padding:.3rem .5rem;
        font-size:.75rem; line-height:1.3; overflow-wrap:anywhere; word-break:break-word; }
.abi3 { font-size:.64rem; color:var(--abi3); margin-left:.2rem; }
.note { color:var(--muted); font-size:.8rem; margin:.45rem 0 0; }
.pure-file { font-size:.85rem; margin:.25rem 0; }
.tag { display:inline-block; font-size:.72rem; color:var(--muted); background:var(--head);
       border:1px solid var(--line); border-radius:4px; padding:0 .3rem; margin-left:.25rem; }
.hint { color:var(--faint); font-size:.8rem; margin-left:.35rem; }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line); color:var(--faint); font-size:.8rem; }
@media (max-width:640px) { body { padding:1.25rem .75rem 3rem; } }
"""


def _esc(text):
    return html.escape(str(text), quote=True)


def _wheel_href(record):
    href = _esc(record["url"])
    if record["sha256"]:
        href += f"#sha256={record['sha256']}"
    return href


def _wheel_title(record):
    title = _esc(record["filename"])
    if record["sha256"]:
        title += f"&#10;sha256: {record['sha256']}"
    return title


def _platform_row(tags):
    platforms = tuple(sorted({tag.platform for tag in tags}))
    return platforms, ".".join(platforms)


def _column_key(tag):
    if tag.interpreter.startswith("pp"):
        return tag.interpreter
    # Free-threaded builds share the interpreter tag with the GIL build and are
    # distinguished by the ABI (cp314 + cp314t), so the ABI becomes the column.
    if tag.abi != "abi3" and tag.abi != "none" and tag.abi.endswith("t"):
        return tag.abi
    return tag.interpreter


def _column_sort_key(column):
    free_threaded = int(column.endswith("t") and not column.startswith("pp"))
    interpreter = column[:-1] if free_threaded else column
    if interpreter.startswith("cp"):
        family = 0
    elif interpreter.startswith("pp"):
        family = 1
    else:
        return (2, Version("0"), 0)
    digits = interpreter[2:]
    if len(digits) < 2:
        return (2, Version("0"), 0)
    return (family, Version(f"{digits[0]}.{digits[1:]}"), free_threaded)


def _platform_sort_key(platform):
    """Split a platform tag into comparable text/number tokens.

    Numbers are emitted as (0, int) and text as (1, str), so macOS 10.9
    sorts before 10.13 and musllinux 1.2 before 1.10 without any per-family
    version table.
    """
    tokens = []
    text = []
    number = []
    for char in platform:
        if char.isdigit():
            if text:
                tokens.append((1, "".join(text)))
                text = []
            number.append(char)
        else:
            if number:
                tokens.append((0, int("".join(number))))
                number = []
            text.append(char)
    if number:
        tokens.append((0, int("".join(number))))
    if text:
        tokens.append((1, "".join(text)))
    return tuple(tokens)


def _row_sort_key(row):
    display = ".".join(row)
    rank = FLAT_FAMILY_ORDER["other"]
    for family, family_rank in FLAT_FAMILY_ORDER.items():
        if display.startswith(family):
            rank = family_rank
            break
    return (rank, _platform_sort_key(row[0]), display)


def _analyze_version(records):
    """Split one version into a pure-Python list or a compiled matrix."""
    pure = []
    rows = {}
    cells = defaultdict(dict)
    columns = set()
    for record in records:
        _project, _version, _build, tags = parse_wheel_filename(record["filename"])
        if all(tag.platform == "any" and tag.abi == "none" for tag in tags):
            pure.append(record)
            continue
        row, display = _platform_row(tags)
        rows[row] = display
        for tag in tags:
            column = _column_key(tag)
            columns.add(column)
            cells[(row, column)][record["filename"]] = {
                "record": record,
                "interpreter": tag.interpreter,
                "abi": tag.abi,
            }
    if not cells:
        return {"kind": "pure", "files": pure}
    return {
        "kind": "matrix",
        "columns": sorted(columns, key=_column_sort_key),
        "rows": [(row, rows[row]) for row in sorted(rows, key=_row_sort_key)],
        "cells": cells,
        "pure_files": pure,
    }


def _render_cell(cell, column):
    if not cell:
        return '<td class="na"></td>'
    links = []
    for entry in cell.values():
        record = entry["record"]
        badge = '<span class="abi3">abi3</span>' if entry["abi"] == "abi3" else ""
        links.append(
            f'<a class="dl" href="{_wheel_href(record)}" title="{_wheel_title(record)}">'
            f"{_esc(column)}{badge}</a>"
        )
    return '<td class="ok">' + "<br>".join(links) + "</td>"


def _wheel_tag_label(filename):
    tags = parse_wheel_filename(filename)[3]
    return ", ".join(sorted({f"{tag.interpreter}-{tag.abi}-{tag.platform}" for tag in tags}))


def _render_pure_files(records):
    lines = []
    for record in records:
        lines.append(
            f'<p class="pure-file"><a class="dl" href="{_wheel_href(record)}" '
            f'title="{_wheel_title(record)}">{_esc(record["filename"])}</a> '
            f'<span class="tag">{_esc(_wheel_tag_label(record["filename"]))}</span> '
            '<span class="hint">pure Python &middot; any platform</span></p>'
        )
    return "".join(lines)


def _render_matrix(info):
    columns = info["columns"]
    head = ['<th scope="col"><span class="platform-head">platform</span></th>']
    head.extend(f'<th scope="col">{_esc(column)}</th>' for column in columns)
    body = []
    for row, display in info["rows"]:
        cells = [f'<th scope="row"><span class="plat">{_esc(display)}</span></th>']
        cells.extend(_render_cell(info["cells"].get((row, column)), column) for column in columns)
        body.append("<tr>" + "".join(cells) + "</tr>")
    note = ""
    if any(entry["abi"] == "abi3" for cell in info["cells"].values() for entry in cell.values()):
        note = (
            '<p class="note">Cells marked <span class="abi3">abi3</span> are stable-ABI wheels: '
            "they are installable on the labelled Python version and newer.</p>"
        )
    return (
        _render_pure_files(info.get("pure_files") or [])
        + '<div class="table-wrap"><table class="matrix"><thead><tr>'
        + "".join(head)
        + "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
        + note
    )


def _release_anchor(repo, tag):
    url = f"https://github.com/{repo}/releases/tag/{urllib.parse.quote(tag, safe='')}"
    return f'<a class="release-tag" href="{_esc(url)}" title="release {_esc(tag)}">{_esc(tag)}</a>'


def _project_kind(entries):
    return "pure" if all(info["kind"] == "pure" for _version, _tag, info in entries) else "compiled"


class FlatPageBuilder:
    """Assemble the flat --find-links page from the accepted index entries."""

    def __init__(self, projects, repo, flat_url):
        self.projects = projects
        self.repo = repo
        self.flat_url = flat_url
        self.names = sorted(projects)
        self.analyzed = {
            name: [(version, tag, _analyze_version(records)) for version, tag, records in projects[name]]
            for name in self.names
        }

    def build(self):
        """Collect the generator exactly once."""
        return "\n".join(self.render())

    def render(self):
        yield "<!DOCTYPE html>"
        yield '<html lang="en">'
        yield "<head>"
        yield '  <meta charset="utf-8">'
        yield '  <meta name="viewport" content="width=device-width, initial-scale=1">'
        yield "  <title>py38deps wheels</title>"
        yield "  <style>"
        yield FLAT_STYLE.strip()
        yield "  </style>"
        yield "</head>"
        yield '<body id="top">'
        yield "<header>"
        yield "  <h1>py38deps wheels</h1>"
        yield (
            '  <p class="lede">Backported wheels, one section per dependency and version. '
            "Anchors are stable: <code>#name</code>, <code>#name-version</code>.</p>"
        )
        yield f'  <pre><code>pip install --no-index --find-links {_esc(self.flat_url)} &lt;package&gt;</code></pre>'
        yield "</header>"
        yield '<nav id="contents" aria-label="Dependencies">'
        yield "  <h2>Contents</h2>"
        yield '  <ul class="toc">'
        yield from self._contents()
        yield "  </ul>"
        yield "</nav>"
        yield "<main>"
        for name in self.names:
            yield from self._project(name)
        yield "</main>"
        yield "<footer>"
        yield (
            '  <p>Every wheel link points at its GitHub release asset and carries <code>#sha256=&hellip;</code>; '
            'no binary is copied or proxied. <a href="#top">Back to top</a></p>'
        )
        yield "</footer>"
        yield "</body>"
        yield "</html>"
        yield ""

    def _contents(self):
        for name in self.names:
            entries = self.analyzed[name]
            kind = _project_kind(entries)
            versions = " &middot; ".join(
                f'<a href="#{_esc(name)}-{_esc(version)}">{_esc(version)}</a>'
                for version, _tag, _info in entries
            )
            yield (
                f'    <li><a href="#{_esc(name)}">{_esc(name)}</a> '
                f'<span class="badge {kind}">{kind}</span> '
                f'<span class="versions">{versions}</span></li>'
            )

    def _project(self, name):
        entries = self.analyzed[name]
        kind = _project_kind(entries)
        yield f'<section class="project" id="{_esc(name)}">'
        yield (
            f'  <h2>{_esc(name)} <span class="badge {kind}">{kind}</span> '
            f'<a class="anchor" href="#{_esc(name)}" aria-label="link to {_esc(name)}">#</a></h2>'
        )
        for version, tag, info in entries:
            yield from self._version(name, version, tag, info)
        yield "</section>"

    def _version(self, name, version, tag, info):
        anchor = f"{name}-{version}"
        release = _release_anchor(self.repo, tag)
        yield f'  <section class="version" id="{_esc(anchor)}">'
        if info["kind"] == "pure":
            count = len(info["files"])
            yield (
                f'    <h3>{_esc(version)} <span class="badge pure">pure Python</span> {release} '
                f'<a class="anchor" href="#{_esc(anchor)}" aria-label="link to {_esc(anchor)}">#</a></h3>'
            )
            yield f'    <p class="meta">{count} wheel{"s" if count != 1 else ""} &middot; any platform</p>'
            yield "    " + _render_pure_files(info["files"])
        else:
            wheel_count = len(
                {entry["record"]["filename"] for cell in info["cells"].values() for entry in cell.values()}
            ) + len(info.get("pure_files") or [])
            yield (
                f'    <h3>{_esc(version)} <span class="badge compiled">compiled</span> {release} '
                f'<a class="anchor" href="#{_esc(anchor)}" aria-label="link to {_esc(anchor)}">#</a></h3>'
            )
            yield (
                f'    <p class="meta">{wheel_count} wheels &middot; {len(info["rows"])} platforms &middot; '
                f'{len(info["columns"])} Python tags</p>'
            )
            yield "    " + _render_matrix(info)
        yield "  </section>"
