# LIMITS: msgspec on old Python versions

This document records behavioral differences between the msgspec backport
(repo/msgspec) and upstream (msgspec/msgspec) **at runtime** on old Python
versions. The backport targets Python 3.8+ while upstream requires 3.10+, so
every difference below is specific to our fork. Test-suite, type-hint and build
differences are out of scope.

## 1. `Literal` int/bool aliasing on Python 3.8 (core limitation)

CPython 3.8's `typing` caches `Literal` aliases by value, and `1 == True` with
`hash(1) == hash(True)`, so literals that differ only in the `bool`/`int`
spelling of the same value are the *same object*:

```python
Literal[1] is Literal[True]                 # True on 3.8, False on 3.9+
Literal[0] is Literal[False]                # True on 3.8
Literal[1, False] is Literal[True, False]   # True on 3.8
Literal[1, 2] is Literal[True, 2]           # True on 3.8
```

Which of the two spellings ends up in `__args__` depends on which alias was
created first in the process. The information the user wrote is lost before
msgspec ever sees the annotation, so there is no library-level fix.

The backport therefore cross-matches the two spellings on 3.8 (blocks guarded by
`#if PY_VERSION_HEX < 0x03090000` in `src/msgspec/_core.c`:
`ms_post_decode_int64`, `ms_post_decode_uint64`, `mpack_decode_bool`,
`json_decode_true`, `json_decode_false`, `convert_int`, `convert_bool`), so that
on 3.8:

- an `int` input is accepted where a bool literal is expected, and a `bool`
  input where an int literal is expected (the alias may be the other spelling);
- the accepted value set is the union of both spellings;
- the value returned follows whichever spelling survived.

Observable consequences for `Literal[1]` / `Literal[True]` (and the same for
`0` / `False`):

| Case | Backport on 3.8 | Upstream (>= 3.10) |
| --- | --- | --- |
| `json.decode(b"1", type=Literal[True])` | `True` | `ValidationError: Expected `bool`, got `int`` |
| `json.decode(b"true", type=Literal[1])` | `True` or `1` (surviving spelling) | `ValidationError: Expected `int`, got `bool`` |
| `json.decode(b"1", type=Literal[1])` | `1` or `True` (surviving spelling) | `1` |
| `json.decode(b"2", type=Literal[1])` | `ValidationError: Expected `bool`, got `int`` (if the bool spelling survived, else `Invalid enum value 2`) | `ValidationError: Invalid enum value 2` |
| `json.decode(b"0", type=Literal[1, False])` | `False` | `ValidationError: Invalid enum value 0` |
| `json.schema(Literal[1])` | `{"enum": [true]}` if the bool spelling survived | `{"enum": [1]}` |
| `inspect.type_info(Literal[1])` | `LiteralType(values=(True,))` if the bool spelling survived | `LiteralType(values=(1,))` |

The cross-matching errs on the permissive side: values that upstream rejects are
accepted on 3.8 when the two spellings collide, and error messages may name the
surviving spelling (`Expected `bool`` for a `Literal[1]` the user wrote).

All code paths — `json`/`msgpack` decoders, `convert` (and therefore `yaml`,
`toml` and struct fields) — agree with each other on 3.8; that consistency is
covered by `tests/unit/test_common.py::TestLiterals::test_literal_bool_int_input`.

## 2. `typing.TypedDict` required keys with a mixed `total` (3.8 only)

The stdlib `typing.TypedDict` on 3.8 has no `__required_keys__` (`__total__`
only, and it reflects the last class created). `_utils.get_typeddict_info()`
therefore falls back to:

```python
if hasattr(cls, "__required_keys__"):
    required = set(cls.__required_keys__)
elif cls.__total__:
    required = set(raw_hints)
else:
    required = set()
```

For a TypedDict that mixes required and optional fields through inheritance, the
`total=False` subclass reports `__total__ = False`, so **every** field becomes
optional on 3.8:

```python
class Base(TypedDict):
    a: int

class Ex(Base, total=False):   # `a` is still required upstream
    b: int

msgspec.convert({}, Ex)        # 3.8: {}      3.10+: ValidationError (missing `a`)
```

Workaround: use `typing_extensions.TypedDict` (it provides `__required_keys__`
on 3.8), after which the behavior matches 3.10+. Plain (non-inherited)
TypedDicts are unaffected on 3.8, since `__total__` is accurate for them.

## 3. Annotations using new-style typing syntax need `eval_type_backport` below 3.10

String annotations — e.g. any annotation under `from __future__ import
annotations` — that use PEP 585 builtin generics (`list[int]`) or PEP 604 unions
(`int | str`) cannot be evaluated by `typing._eval_type` on 3.8/3.9. The
backport's `_utils._eval_type` catches that `TypeError` and falls back to the
`eval_type_backport` package; when that package is not installed the original
`TypeError` is re-raised with an actionable message:

```
Unable to evaluate type annotation 'list[int]'. If you are making use of the new
typing syntax (unions using `|` since Python 3.10 or builtins subscripting since
Python 3.9), you should either replace the use of new syntax with the existing
`typing` constructs or install the `eval_type_backport` package.
```

Upstream on 3.10+ evaluates such annotations natively and needs no extra
package. On 3.8/3.9 the backport's test dependencies include
`eval-type-backport`, so the test suite always exercises the working path.

## 4. Summary of backport-specific runtime differences vs upstream v0.22.0

| Area | Upstream (>= 3.10) | Backport on 3.8 |
| --- | --- | --- |
| `Literal[1]` vs `Literal[True]` (and `0`/`False`) | two distinct aliases; the wrong spelling is rejected | one object (`typing` cache); both spellings accepted, returned value follows the surviving spelling |
| `inspect.type_info` / `json.schema` for such literals | int spelling preserved | whichever spelling was interned first |
| Validation error messages | name the type the user wrote | may name the other spelling |
| `TypedDict` mixing required and optional fields | required keys from `__required_keys__` | all keys optional (use `typing_extensions.TypedDict` to match) |
| Annotations with `list[int]` / `X \| Y` | evaluated natively | require `eval_type_backport`, otherwise a descriptive `TypeError` |
