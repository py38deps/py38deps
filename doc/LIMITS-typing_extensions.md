# LIMITS: typing_extensions on Python 3.8

This document records behavioral differences between the typing_extensions backport
(repo/typing_extensions) and upstream (python/typing_extensions) **at runtime** on
Python 3.8. The backport targets Python 3.8+ while upstream 4.16.0 requires 3.9+
(support for 3.8 was dropped in 4.14.0, commit `7cfb2c0` / PR #585), so every
difference below is specific to our fork. Test-suite and type-hint differences are out
of scope.

The backport restores the Python 3.8 branches that upstream removed in 4.14.0, so the
behavior on 3.8 matches what upstream 4.13.2 — the last release supporting 3.8 —
provided, plus everything 4.14.0–4.16.0 added that is version-independent (`sentinel`,
`disjoint_base`, `type_repr`, `Reader`/`Writer`, `Unpack`/`Concatenate` `isinstance`
fixes, PEP 728/764 TypedDict keywords, …). Where upstream 4.13.x left a 3.8 behavior
undone on purpose (section 3), the backport aligns it with 3.9+.

## 1. `Annotated` and `_AnnotatedAlias` are typing_extensions implementations

PEP 593 (`typing.Annotated`, together with the private `typing._AnnotatedAlias`) was
added in Python 3.9, so on 3.8 there is nothing to alias to. The backport defines both
objects itself:

```python
# Python 3.9+ has PEP 593 (Annotated)
if hasattr(typing, 'Annotated'):
    Annotated = typing.Annotated
    _AnnotatedAlias = typing._AnnotatedAlias
# 3.8
else:
    class _AnnotatedAlias(typing._GenericAlias, _root=True): ...
    class Annotated: ...
```

Consequences for user code on 3.8:

- `typing_extensions.Annotated is typing.Annotated` is `False` on 3.8 (on 3.9+ it is
  `True`); `typing_extensions._AnnotatedAlias is typing._AnnotatedAlias` is `False` for
  the same reason.
- `type(Annotated[int, "meta"])` is `typing_extensions._AnnotatedAlias` on 3.8 and
  `typing._AnnotatedAlias` on 3.9+. Identity checks against `typing.Annotated` (or
  `isinstance` checks against the typing module's alias class) must not be used on 3.8.
- Pickling works on both, but the reduce target is `typing_extensions.Annotated` on
  3.8, so unpickling requires typing_extensions to be importable.
- Everything else is identical: verified on cp38 and cp39 that `get_origin()`,
  `get_args()`, `get_type_hints(..., include_extras=True)` (extras kept) and
  `get_type_hints(...)` (extras stripped) return the same values. Only the repr keeps
  the `typing_extensions.` prefix on 3.8 (as it did upstream before 4.14.0).

## 2. `ForwardRef` carries no module/class information on Python 3.8

`typing_extensions.ForwardRef` is `typing.ForwardRef` on every supported version, but
the parameters grew over time: `module` in 3.9.7 (gh-101771 backport), `is_class` in
3.13, `owner` in 3.14. On 3.8 the constructor only accepts `arg` and `is_argument`.

Consequences for user code on 3.8:

- `typing_extensions.ForwardRef("X", module="pkg")` raises `TypeError` on 3.8; on 3.9+
  the same call produces a forward reference that resolves against `pkg`'s namespace.
- Consequently `typing_extensions.evaluate_forward_ref()` has no module-based lookup
  path on 3.8. It still resolves names through `globals`, `locals`, `owner` and
  `type_params` (all of those are covered by the test suite on 3.8); only
  `ForwardRef(..., module=...)` input is unavailable.
- `ForwardRef` instances expose fewer attributes: `__forward_module__` and
  `__forward_is_class__` exist on 3.9+ but are absent on 3.8 (verified). Code that
  inspects these attributes must guard them with `getattr()`.
- `typing_extensions._FORWARD_REF_HAS_CLASS` is `False` on 3.8, mirroring the missing
  `is_class` parameter (it is the flag upstream uses to gate PEP 695 class-scope
  lookups in its own test suite).
- Nested forward references written *inside a string* are not resolved recursively: the
  `recursive_guard` parameter of `typing._eval_type()` was only added in 3.9 (3.8 has
  `def _eval_type(t, globalns, localns)`), so `evaluate_forward_ref()` cannot descend
  into a reference that is nested as a string inside another reference. The inner part
  stays a `ForwardRef` on 3.8 while 3.9+ resolves it; upstream's `test_nested_strings`
  is skipped on 3.8 for this reason.

## 3. Subscripting arbitrary TypedDict/NamedTuple types is provided on 3.8

On 3.9+ the following works because PEP 585 gives `dict` and `tuple` a
`__class_getitem__`, which their subclasses inherit:

```python
class TD(TypedDict):
    a: int
class Group(NamedTuple):
    key: int

TD[int]      # 3.9+: types.GenericAlias(TD, int)
Group[int]   # 3.9+: types.GenericAlias(Group, int)
```

Python 3.8 has no PEP 585, so upstream did not provide this: in 4.13.x (the last release
supporting 3.8) `Group[int]` raised `TypeError: 'type' object is not subscriptable`,
`TD[int]` raised `TypeError: '_TypedDictMeta' object is not subscriptable`, and both
were asserted by the test suite.

The backport restores the 3.9+ behavior by injecting a `__class_getitem__` into
non-generic TypedDict and NamedTuple classes (`_class_getitem_for_arbitrary_type`),
which returns `typing._GenericAlias(cls, args)`:

- `__origin__`, `__args__`, `__parameters__` and instantiation (`TD[int](a=1)`,
  `Group[int](1, [2])`) match 3.9+ exactly; results are not cached on either version
  (`TD[int] is TD[int]` is `False`).
- Only the type of the alias object differs: `typing._GenericAlias` on 3.8 versus
  `types.GenericAlias` on 3.9+. This cannot be avoided, as `types.GenericAlias` does not
  exist on 3.8.
- Generic TypedDicts and NamedTuples are untouched: they resolve `__class_getitem__`
  through `typing.Generic` on every version.
- Both syntaxes are covered: the class-based one (`class Group(NamedTuple)`) and the
  functional one (`Group = NamedTuple("Group", [("key", int)])`), since both go through
  `_make_nmtuple()`.
- Only classes created by `typing_extensions.TypedDict` / `typing_extensions.NamedTuple`
  are affected. Subclasses of the *stdlib* `typing.TypedDict` / `typing.NamedTuple`
  remain unsubscriptable on 3.8 (`TypeError: '_TypedDictMeta' object is not
  subscriptable` / `'type' object is not subscriptable`), while they are subscriptable
  on 3.9+.

## 4. `typing.NamedTuple` is still a metaclass-based class on 3.8

`typing_extensions.NamedTuple` is a plain function on CPython and on PyPy 3.9+ (PyPy
3.8 gets a callable wrapper instance instead, so that `class X(NamedTuple)` keeps
working there: PyPy < 3.9 does not honour a `__mro_entries__` attribute set on a
function), but on 3.8 the stdlib `typing.NamedTuple` it wraps is *not*: 3.8 uses
`NamedTupleMeta`, while 3.9+ turned it into a function. This is upstream's own version
split (upstream 4.13.x gated its tests on `TYPING_3_9_0` the same way), and the two
implementations are not interchangeable:

- `type(typing.NamedTuple)` is `NamedTupleMeta` on 3.8 and `function` on 3.9+.
- Consequently the compatibility test `test_same_as_typing_NamedTuple_39_plus` is
  skipped on 3.8, and the 3.8-only counterpart checks the `_field_types` attribute that
  was removed in 3.9 (`_make_nmtuple()` re-creates it on 3.8).

## 5. Other runtime differences

- **`requires-python`**: `>= 3.8` (upstream: `>= 3.9`), so the backport installs on 3.8
  instead of failing at resolution time.
- **`get_type_hints()` on 3.8** cannot pass `include_extras=` to the stdlib (it was
  added in 3.9), so `typing_extensions.get_type_hints()` calls
  `typing.get_type_hints()` without it on 3.8 and applies its own `_strip_extras()`
  pass afterwards. The observable result is the same as on 3.9+ (verified above),
  because on 3.8 the stdlib does not know about `Annotated` and therefore never strips
  it.

## 6. Summary of backport-specific runtime differences vs upstream v4.16.0

| Area | Upstream (>= 3.9) | Backport on 3.8 |
| --- | --- | --- |
| `Annotated` | alias of `typing.Annotated` | typing_extensions implementation (`class Annotated`) |
| `_AnnotatedAlias` | `typing._AnnotatedAlias` | `typing_extensions._AnnotatedAlias(typing._GenericAlias)` |
| `ForwardRef(..., module=/is_class=/owner=)` | supported | `TypeError` (only `arg`, `is_argument`) |
| `ForwardRef.__forward_module__` / `__forward_is_class__` | present | absent |
| `evaluate_forward_ref()` module lookup | via `ForwardRef(module=...)` | unavailable (globals/locals/owner/type_params still work) |
| `TD[int]` / `Group[int]` | `types.GenericAlias` (PEP 585) | same semantics, object is `typing._GenericAlias` |
| `typing.NamedTuple` | function (3.9+) | `NamedTupleMeta` class |
| `requires-python` | `>= 3.9` | `>= 3.8` |
