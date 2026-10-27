# LIMITS: multidict on old Python versions

This document records behavioral differences between the multidict backport
(`repo/multidict`) and upstream (`aio-libs/multidict`) **at runtime** on old
Python versions. The backport targets Python 3.8+ while upstream 7.2 and later
requires Python 3.11+, so every difference below is specific to our fork.
Test-suite and type-hint differences are out of scope.

## 1. The C extension is unavailable in Python 3.8 subinterpreters (core limitation)

multidict 7.x keeps all mutable state in the module state and relies on PEP 573
to find it from an object's type (`PyType_FromModuleAndSpec()`,
`PyType_GetModule()`). PEP 573 exists since Python 3.9. Python 3.8 has no
per-type module association, so `multidict/_multilib/py38_compat.h` remembers
the module in a single process-wide pointer.

That pointer cannot represent more than one interpreter. Without a guard,
importing multidict in two subinterpreters and destroying one corrupted the
other: constructing a mapping afterwards either crashed immediately or
corrupted the heap and crashed at interpreter shutdown (observed as access
violation / heap corruption, non-deterministically).

The backport therefore refuses to initialize the C extension anywhere but the
main interpreter on 3.8:

- `multidict` still imports: `multidict/_compat.py` catches the `ImportError`
  and falls back to the pure-Python implementation
  (`multidict._multidict_py`).
- Importing `multidict._multidict` directly raises `ImportError`.
- The main interpreter always gets the C extension.
- Python 3.9+ has PEP 573 and is unaffected; the guard is compiled out.

Impact: code that expects the C extension inside a 3.8 subinterpreter silently
gets the pure-Python implementation instead (slower, same behavior). Upstream
does not support 3.8 at all.

## 2. `MultiDict[int]` returns the class on Python 3.8

`types.GenericAlias` was added in Python 3.9. On 3.8 both implementations return
the class itself:

```python
multidict.MultiDict[int] is multidict.MultiDict  # True on 3.8
```

On 3.9+ the same expression is a `types.GenericAlias`, as upstream. Consequences
on 3.8:

- `isinstance(md, multidict.MultiDict[int])` returns `True` instead of raising
  `TypeError` ("isinstance() argument 2 cannot be a parameterized generic").
- The alias has no `__origin__` / `__args__`.

This matches the behavior of multidict 6.x, which still supported 3.8.

## 3. The C extension's types are mutable on Python 3.8/3.9

`Py_TPFLAGS_IMMUTABLETYPE` was added in Python 3.10. `py38_compat.h` defines the
bit for older versions, but 3.8/3.9 do not interpret it, and the types are heap
types created from a spec, so their attributes can be changed:

```python
multidict.MultiDict.add = something  # succeeds on 3.8/3.9
```

On 3.10+ the same statement raises `TypeError: cannot set '...' attribute of
immutable type ...`. Deleting attributes and replacing methods behave the same
way. multidict 6.1.0 (the last release that supported 3.8) used static
`PyTypeObject` definitions and was therefore immutable there; the 7.x
architecture replaced them with heap types, and 3.8/3.9 cannot mark those
immutable.

Things that are still blocked on every version:

- direct instantiation of the internal view/iterator types (`_ItemsView`,
  `_KeysView`, `_ValuesView` and the three iterator types);
- `object.__new__(...)` on those types.

The pure-Python classes are mutable on every version, which is upstream behavior.

## 4. Other runtime differences

- **C API header on 3.8/3.9**: `multidict_capi.h` uses `Py_TYPE(op) == type`
  instead of `Py_IS_TYPE()` (3.10+), so third-party extensions can include it
  without an extra compatibility header. The capsule name, version and all
  `MultiDict_*` entry points are unchanged.
- **Argument error wording**: CPython 3.8 words some `TypeError` messages
  differently from 3.10+ (for example "expected at most 1 argument, got 2"
  instead of "takes at most 1 positional argument"). This comes from the
  interpreter's argument parser, not from multidict.

## 5. Performance notes

The compatibility layer does not change the hot paths. Measured on the 7.2.1
backport with the C extension (best of five `timeit` runs, 10-key multidicts
unless noted), Python 3.8 is on par with Python 3.9 for `get`, `contains`,
`getall`, `setitem`, `update`, `copy`, iteration and proxy lookups: within
about 6%, in either direction.

Three interpreter-level effects remain on 3.8:

- **`istr(...)` construction is ~1.3x slower than on 3.9+** (161 ns vs 125 ns
  per call). CPython 3.9 started honouring a type object's `tp_vectorcall`,
  which the extension installs (`istr_tp_vectorcall`) for the one-argument
  form; on 3.8 the call goes through `type_call()` + `tp_new()`. The backport
  adds an exact-str fast path to `istr_new()` (the same
  `_istr_from_exact_str()` allocation the vectorcall path uses); without it
  the gap would be ~1.8x.
- **A `CIMultiDict` lookup of a non-ASCII key is ~20% slower on 3.8** (172 ns
  vs 143 ns). Folding the key needs `str.lower()` on a `str` subclass, which
  the extension reaches through `PyObject_CallMethodNoArgs`; 3.8 only has the
  varargs `PyObject_CallMethodObjArgs()`, so the shim in `py38_compat.h` costs
  about 7 ns per call. ASCII keys take the `str_cmp.h` fast path and are
  within 5%.
- **Subinterpreters use the pure-Python implementation** (section 1), which is
  slower than the C extension.

For reference, on Python 3.8 the 7.1.0 backport measured substantially faster
than multidict 6.1.0: `update()` of 10 keys ~2.7x, `copy()` ~9.7x, a
`CIMultiDict` ASCII key lookup ~1.6x, and `istr()` ~1.4x. Those figures were
taken before the 7.2 C rewrite and were not re-measured against 6.1.0.

## Summary

| Area | 3.8 | 3.9 | 3.10+ |
| --- | --- | --- | --- |
| C extension in subinterpreters | pure-Python fallback | available | available |
| `MultiDict[int]` | the class itself | `types.GenericAlias` | `types.GenericAlias` |
| C extension types | mutable | mutable | immutable |
| C API header usable | yes | yes | yes |
