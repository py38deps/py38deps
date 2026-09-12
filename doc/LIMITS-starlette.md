# LIMITS: starlette on Python 3.8

This document records behavioral differences between the starlette backport
(repo/starlette) and upstream (Kludex/starlette) **at runtime** on old Python
versions. The backport targets Python 3.8+ while upstream requires 3.10+
(`Drop Python 3.8` in 0.37, `Drop support for Python 3.9` in 0.52), so the
differences below are specific to our fork. Test-suite, tooling and type-hint
differences are out of scope.

## 1. ETag generation cannot opt out of FIPS on Python 3.8 (core limitation)

`hashlib.md5()` gained the `usedforsecurity` keyword argument in Python 3.9
(gh-92139). Upstream computes file ETags with

```python
hashlib.md5(etag_base.encode(), usedforsecurity=False).hexdigest()
```

so that a FIPS-enabled build is told that MD5 is used for non-security purposes
(ETag checksums) and does not reject the call. Python 3.8's `hashlib` does not
accept the keyword at all, so the fork restores the `md5_hexdigest()` compat
wrapper that upstream deleted together with 3.8 support (now defined in
`starlette/responses.py`):

```python
try:
    # check if the Python version supports the parameter
    hashlib.md5(b"data", usedforsecurity=False)

    def md5_hexdigest(data: bytes, *, usedforsecurity: bool = True) -> str:
        return hashlib.md5(data, usedforsecurity=usedforsecurity).hexdigest()

except TypeError:

    def md5_hexdigest(data: bytes, *, usedforsecurity: bool = True) -> str:
        return hashlib.md5(data).hexdigest()
```

The `except TypeError` branch is dead code on Python 3.9+. There is no
pure-Python workaround for 3.8: the flag exists precisely to tell OpenSSL that
MD5 may be used, and the interpreter does not expose it.

| Platform / build | Impact |
| --- | --- |
| CPython 3.9+ (any platform) | **None.** The `try` branch is used, exactly as upstream. |
| CPython 3.8, regular OpenSSL build | **None.** `hashlib.md5(data)` without the keyword is accepted and produces the same ETag. |
| CPython 3.8, FIPS-enabled OpenSSL | **`ValueError` when an ETag is computed.** `FileResponse.set_stat_headers()` raises, so `FileResponse` and everything built on it (notably `StaticFiles`, which also compares ETags in `is_not_modified()`) fail to serve responses on such builds. Upstream cannot run on 3.8 at all, so this is a limitation of the backport rather than a regression. |

## 2. `starlette.types` aliases come from `typing` on Python 3.8

`starlette/types.py` evaluates its aliases at import time. Upstream builds them
from `collections.abc` together with `contextlib.AbstractAsyncContextManager`,
but those classes only became subscriptable in Python 3.9 (PEP 585), so on 3.8 the
module falls back to the equivalent `typing` objects:

| Alias | Python 3.9+ (same as upstream) | Python 3.8 |
| --- | --- | --- |
| `Scope`, `Message` | `collections.abc.MutableMapping[str, Any]` | `typing.MutableMapping[str, Any]` |
| `Receive`, `Send`, `ASGIApp` | `collections.abc.Callable[...]` | `typing.Callable[...]` |
| `StatelessLifespan`, `StatefulLifespan` | `collections.abc.Callable[..., contextlib.AbstractAsyncContextManager[...]]` | `typing.Callable[..., typing.AsyncContextManager[...]]` |
| `Lifespan`, `ExceptionHandler` | `A \| B` (`types.UnionType`) | `typing.Union[A, B]` |

The subscripted `typing` aliases are distinct objects from their `collections.abc`
counterparts, so on 3.8 `starlette.types.Scope == collections.abc.MutableMapping[str, Any]`
is `False` and `repr()` differs. Everything else is unaffected: both spellings
resolve to the same classes, and `typing.Union[A, B] == A | B` holds on every
version. The difference is observable only to code that introspects
`starlette.types` at runtime; annotations, `typing.get_type_hints()` and static
analysis behave identically. On Python 3.9+ the module imports exactly what
upstream imports and the aliases are equal to upstream's.

`starlette.types` keeps re-exporting `AbstractAsyncContextManager` under the same
name (from `contextlib` on 3.9+, aliased from `typing` on 3.8), so importers of
that name are unaffected.
