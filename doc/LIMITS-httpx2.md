# LIMITS: httpx2 & httpcore2 on old Python versions

This document records behavioral differences between the httpx2/httpcore2
backport (repo/httpx2) and upstream (pydantic/httpx2) **at runtime** on old
Python versions. The backport targets Python 3.8+ while upstream requires
3.10+, so every difference below is specific to our fork. Test-suite and
type-hint differences are out of scope.

## 1. Zstandard decoding uses the `zstandard` package (httpx2)

Upstream v2.12.0 decodes `Content-Encoding: zstd` with `backports.zstd`, which
requires Python >= 3.10 (it is the backport of the 3.14 stdlib
`compression.zstd` module). The backport instead ships `zstandard>=0.23.0` in
the `httpx2[zstd]` extra for all supported versions (the dependency upstream
used before v2.12.0; the py38deps zstandard 0.25.0 backport wheel satisfies it
on 3.8/3.9), and `src/httpx2/httpx2/_decoders.py` wraps its `decompressobj` in
an adapter that mimics the `backports.zstd` API (`eof`, `unused_data`,
`needs_input`, `decompress(data, max_length)`).

Observable behavior is preserved: output is still delivered in chunks of at
most `MAX_DECODE_CHUNK_SIZE` (1 MiB), multi-frame streams, concatenated
frames, and truncation errors behave the same, and the decoder raises
`httpx2.DecodingError` on corrupt input. The only difference is **memory
footprint**: `zstandard`'s `decompressobj.decompress()` has no `max_length`
parameter, so a single compressed input is inflated in one call before the
adapter slices it into bounded chunks, whereas upstream caps each inflation at
1 MiB. A malicious or pathological compressed stream can therefore cause a
larger transient allocation than upstream.

## 2. Dependency resolution relies on the py38deps backport wheels

httpx2/httpcore2 keep the **upstream version requirements unchanged**
(`anyio>=4.10`, `idna>=3.18`, `trio>=0.33.0,<1.0`, `truststore>=0.10`,
`zstandard>=0.23.0` for the `zstd` extra). Several of these packages have no
PyPI release supporting < 3.10:

- `anyio>=4.10` requires Python >= 3.9, so on 3.8 only the py38deps anyio
  backport wheel (same version, widened `requires-python`) satisfies it.
- `idna>=3.18` requires Python >= 3.9; on 3.8 the py38deps idna backport
  wheel is needed. idna < 3.18 lacks the `display` parameter used by
  `URL.host` to decode mixed valid/malformed `xn--` labels leniently
  (per-label recovery, UTS #46); on 3.8 the backport implements the same
  per-label fallback in `src/httpx2/httpx2/_urls.py`, so `URL.host` output
  matches upstream, and other IDNA encode/decode differences between idna
  3.15 and 3.18 may still surface in edge cases.
- `trio>=0.33.0,<1.0` requires Python >= 3.10; on 3.8/3.9 the py38deps trio
  backport wheel is needed.
- `truststore>=0.10` has **no PyPI release supporting < 3.10 at all** (every
  release declares `>= 3.10`); the py38deps truststore backport wheel is
  required on 3.8/3.9.
- `zstandard>=0.23.0` resolves to 0.23.0 on 3.8 from PyPI; the py38deps
  zstandard 0.25.0 backport wheel also satisfies it on all versions.

In short: on 3.8/3.9, `pip install httpx2` succeeds only when the matching
py38deps backport wheels for these dependencies are available (local wheels,
private index, or another backport source).

## 3. `Origin` dataclass has no `__slots__` on 3.8/3.9 (httpx2)

`Origin` is a frozen dataclass. Upstream declares it with
`@dataclass(frozen=True, slots=True, init=False)`, but `slots=True` was only
added to `@dataclass` in Python 3.10. On 3.8/3.9 the backport drops the
`slots` keyword, so instances carry a `__dict__` and consume slightly more
memory. All observable behavior (frozen-ness: attribute assignment raises
`FrozenInstanceError`; `__eq__`/`__hash__`/`__repr__`; IPv6 canonicalization)
is identical to upstream.

## 4. Other runtime differences

- **`requires-python`**: `>= 3.8` (upstream: `>= 3.10`), so the wheels install
  on 3.8/3.9 instead of failing at resolution time.
- **`zstd` extra composition**: upstream installs `backports.zstd` on
  `python_version <= '3.13'`; the backport restricts that to
  `3.10 <= python_version <= '3.13'` and installs `zstandard>=0.23.0` on
  `python_version < '3.10'` (see section 1). On 3.14+ nothing is installed, as
  upstream.
- **`contextlib.aclosing` and `contextlib.nullcontext` async support** are
  backported inside httpx2 source (both are 3.10+ stdlib additions); behavior
  is identical, no user-visible difference.
- **IPv6 zone identifiers in URLs** (`https://[fe80::1%25eth0]`) are handled
  by stripping the `%zone` suffix before `ipaddress.IPv6Address` validation on
  3.8 (the stdlib only supports zone ids from 3.9); the zone is re-attached
  unchanged and origin normalization matches upstream (verified by the
  upstream test suite).

## 5. Summary of backport-specific runtime differences vs upstream v2.12.0

| Area | Upstream (>= 3.10) | Backport on 3.8/3.9 |
| --- | --- | --- |
| zstd backend | `backports.zstd` | `zstandard` + adapter (all < 3.14); chunked output identical, single-call inflation not capped |
| runtime deps | official upper-bound versions | same version requirements; satisfied by py38deps backport wheels on 3.8/3.9 |
| `Origin` memory layout | `__slots__` | `__dict__` on 3.8/3.9 |
| `requires-python` | `>= 3.10` | `>= 3.8` |
