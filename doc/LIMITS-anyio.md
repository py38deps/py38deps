# LIMITS: anyio asyncio backend on old Python versions

This document records behavioral differences between the anyio backport
(repo/anyio) and upstream (agronholm/anyio) **at runtime** on old Python versions
(3.8 and 3.9, with some notes on the 3.10/3.11 boundaries). The backport targets
Python 3.8+ while upstream 4.14.2 requires 3.10+, so every difference below is
specific to our fork. Test-suite and type-hint differences are out of scope.

## 1. Cancellation reasons cannot be delivered inside tasks on Python 3.8

`asyncio.Task.cancel(msg)` was added in Python 3.9 (bpo-38906). On Python 3.8 the
cancellation reason must be delivered through a custom mechanism:

- `CancelScope._deliver_cancellation()` stores the reason on the task as
  `task._anyio_cancel_marker = origin._cancel_reason` and then calls `task.cancel()`
  without a message (see `_deliver_cancellation()` in `_asyncio.py`).
- The `CancelledError` injected into the task therefore carries **no message** on 3.8;
  the marker is only consumed in two places:
  - **Recognition**: `is_anyio_cancellation()` / the task group host task cleanup
    (`_cancel_scope_task_done`) check the marker to decide that the cancellation came
    from AnyIO.
  - **Cross-thread propagation**: `AsyncBackend.run_async_from_thread()` reads the
    marker and re-raises `concurrent.futures.CancelledError(cancel_reason)` so the
    reason survives a thread hop.

Consequences:

- Inside a task, `str(CancelledError)` is empty on 3.8, so any code relying on the
  exception message of an in-task cancellation must not do so on 3.8.
- The Trio backend has the same limitation on 3.8: `CancelScope.cancel(reason)` only
  exists in Trio >= 0.30, while the newest Trio available on Python 3.8 is 0.27
  (see the `_CANCEL_ACCEPTS_REASON` wrapper in `_trio.py`). `trio.Cancelled` is a
  singleton whose `str()` is always `"Cancelled"`.

This is a hard CPython/Trio limitation: the 3.8 branch of `_deliver_cancellation()`
cannot be extended to put the reason into the in-task exception without rewriting the
cancellation injection itself, and it is not worth doing.

## 2. asyncio primitives bind to the event loop at construction on Python < 3.10

Since Python 3.10, `asyncio.Event()`, `asyncio.Lock()` etc. no longer call
`get_event_loop()` in their constructor; the loop is bound lazily on first use. On
3.8/3.9 the constructor calls `events.get_event_loop()`, which returns (or creates) the
current thread's loop.

Consequences:

- Creating an `asyncio.Event()` while no loop is running binds it to whatever loop
  `get_event_loop()` returns. If tasks later run on a different loop (e.g. a loop
  created with `asyncio.new_event_loop()` and never set as current), awaiting the
  event fails with `RuntimeError: Task ... got Future ... attached to a different
  loop`.
- Workaround: create the primitive inside a running loop (e.g. inside a coroutine
  scheduled with `run_until_complete()`), so `get_event_loop()` returns the running
  loop.
- On 3.10/3.11 `get_event_loop()` without a running loop emits a `DeprecationWarning`
  (and raises `RuntimeError` since 3.12), but the primitives no longer call it at
  construction, so the problem only exists on 3.8/3.9.

## 3. `subprocess.Popen` user/group/extra_groups/umask require Python 3.9

`open_process()` in `src/anyio/_core/_subprocesses.py` only forwards `user`, `group`,
`extra_groups` and `umask` to the backend on 3.9+ (they were added to `subprocess.Popen`
in 3.9). On 3.8 these arguments are silently ignored.

## 4. `anyio.Path.write_text()` drops the `newline` argument below Python 3.10

`pathlib.Path.write_text()` gained the `newline` parameter in Python 3.10 (gh-91124).
The backport's `Path.write_text()` in `src/anyio/_core/_fileio.py` therefore does not
pass `newline` to `pathlib.Path.write_text()` on 3.8/3.9, so the argument is silently
ignored there (it is forwarded as on upstream on 3.10+).

## 5. The selector thread is a daemon and uses `atexit` on 3.8

The socket selector thread in `src/anyio/_core/_asyncio_selector_thread.py` is started
as a **daemon thread** by the backport (upstream starts it as a non-daemon thread), and
its shutdown hook prefers `threading._register_atexit()` (a 3.9+ private API) with a
fallback to `atexit.register()` when it is missing (Python 3.8). The daemon flag
guarantees the interpreter can exit even if the selector thread has not been stopped.

## 6. `asyncio.get_event_loop()` semantics change across versions

- 3.8/3.9: creates and sets a new loop when none exists (no warning).
- 3.10/3.11: `DeprecationWarning`.
- 3.12+: `RuntimeError`.

Code paths that call `get_event_loop()` indirectly (e.g. asyncio primitive
construction, see section 2) therefore behave differently on each version. Code in the
backend always prefers `get_running_loop()`.

## 7. `asyncio.get_child_watcher()` was removed in Python 3.12

`_forcibly_shutdown_process_pool_on_exit()` in `_asyncio.py` only queries the child
watcher on Python < 3.12 (the API was deprecated since 3.8 and removed in 3.12).

## 8. Trio version is pinned per Python version

`pyproject.toml` pins the trio extra per interpreter version because no single Trio
release supports 3.8 through 3.14 (upstream declares an unconditional
`trio >= 0.32.0`):

| Python | trio pin | Notes |
| --- | --- | --- |
| 3.8 | `>= 0.26.1, < 0.28` | no `CancelScope.cancel(reason)` (section 1); zero-capacity `CapacityLimiter` not supported |
| 3.9 | `>= 0.31, < 0.32` | |
| 3.10+ | `>= 0.32` | matches upstream |

## 9. Other runtime differences

- **`requires-python`**: `>= 3.8` (upstream: `>= 3.10`), so the backport installs on
  3.8/3.9 instead of failing at resolution time.
- **`asyncio` cancellation semantics**: upstream assumes `Task.cancel(msg)` exists
  (3.9+); the backport's `_deliver_cancellation()` uses the marker mechanism on 3.8
  (section 1). The `Task.uncancel()` bookkeeping is inert below 3.11 on both upstream
  and the backport (upstream already gates it on `sys.version_info >= (3, 11)`).
