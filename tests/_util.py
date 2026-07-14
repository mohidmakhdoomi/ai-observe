"""Shared helpers for the ai_observe test suite.

`tests/` is not a package (no `__init__.py`). Under
`python -m unittest discover -s tests` the start directory is added to
`sys.path`, so sibling test modules import this as a top-level module:

    sys.path.insert(0, str(ROOT / "tests"))
    from _util import poll_until

(the explicit insert matches the `_aggregator_oracle` precedent and keeps
the import working under non-discover invocations too).
"""

import time


def poll_until(predicate, timeout=3.0, interval=0.02):
    """Poll `predicate` until it is truthy or `timeout` seconds elapse.

    Bounded replacement for fixed `time.sleep()` synchronization: returns
    as soon as the condition holds (fast on healthy hosts) and tolerates
    slow CI hosts up to `timeout`. Consolidates the former
    `_wait_until` (test_live_trace) and `_wait_for` (test_viewer_tailer)
    helpers; the default timeout is the more generous of the two.

    Returns the result of a final `predicate()` check, so a condition that
    becomes true exactly at the deadline still passes. Callers assert on
    the return value: `self.assertTrue(poll_until(...))`.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
