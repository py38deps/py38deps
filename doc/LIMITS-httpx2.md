# LIMITS: httpx2 & httpcore2 on old Python versions

This document records behavioral differences between the httpx2/httpcore2
backport (repo/httpx2) and upstream (pydantic/httpx2) **at runtime** on old
Python versions. The backport targets Python 3.8+ while upstream requires
3.11+.

Only differences that user code can observe are listed. Packaging and
environment changes (widened `requires-python`, dependency sources and version
markers, CI setup), shims and equivalent rewrites that preserve upstream
behavior, and test-suite or type-hint differences are out of scope.

## 1. Zstandard decoding uses the `zstandard` package (httpx2)

Upstream decodes `Content-Encoding: zstd` with `backports.zstd` on Python <
3.14 (the backported 3.14 stdlib module, which has no 3.8/3.9 support). The
backport instead uses the `zstandard` package — the dependency upstream used
before v2.12.0 — and wraps its `decompressobj` in an adapter that mimics the
`backports.zstd` API (`eof`, `unused_data`, `needs_input`,
`decompress(data, max_length)`).

Observable behavior is preserved: output is still delivered in chunks of at
most `MAX_DECODE_CHUNK_SIZE` (1 MiB), multi-frame streams, concatenated
frames, and truncation errors behave the same, and the decoder raises
`httpx2.DecodingError` on corrupt input. The only difference is **memory
footprint**: `zstandard`'s `decompressobj.decompress()` has no `max_length`
parameter, so a single compressed input is inflated in one call before the
adapter slices it into bounded chunks, whereas upstream caps each inflation at
1 MiB. A malicious or pathological compressed stream can therefore cause a
larger transient allocation than upstream.

## 2. `Origin` dataclass has no `__slots__` on 3.8/3.9 (httpx2)

`Origin` is a frozen dataclass. Upstream declares it with
`@dataclass(frozen=True, slots=True, init=False)`, but `slots=True` was only
added to `@dataclass` in Python 3.10. On 3.8/3.9 the backport drops the
`slots` keyword, so instances carry a `__dict__` and consume slightly more
memory. All observable behavior (frozen-ness: attribute assignment raises
`FrozenInstanceError`; `__eq__`/`__hash__`/`__repr__`; IPv6 canonicalization)
is identical to upstream.

## 3. TLS writes after a ragged EOF are not reported on OpenSSL 1.1.1

`httpcore2` maps `anyio.EndOfStream` to `WriteError`, so writing to a TLS
stream whose peer closed the TCP connection without a `close_notify` alert
raises `httpcore2.WriteError` (upstream behaviour since v2.14.0). On
interpreters built against OpenSSL 1.1.1 — for example the CPython 3.8-3.10
Windows installers and wheels built in `manylinux2014` images — the SSL layer
does not report that condition at write time at all, so
`await stream.write(...)` succeeds silently. Reads still return `b""` at the
end of the stream.

This is an OpenSSL limitation rather than a backport behaviour change: the
same code raises `httpcore2.WriteError` on the same Python when linked against
OpenSSL 3.0+. The upstream test for it
(`tests/httpx2/test_exceptions.py::test_anyio_write_after_ragged_tls_eof`) is
skipped below Python 3.11 or OpenSSL 3.0 for that reason.

## 4. Summary of backport-specific runtime differences vs upstream v2.16.0

| Area | Upstream (>= 3.11) | Backport on 3.8-3.10 |
| --- | --- | --- |
| zstd backend | `backports.zstd` | `zstandard` + adapter (all < 3.14); chunked output identical, single-call inflation not capped |
| `Origin` memory layout | `__slots__` | `__dict__` on 3.8/3.9 |
| TLS write after ragged EOF | `WriteError` | silently succeeds on OpenSSL 1.1.1 |
