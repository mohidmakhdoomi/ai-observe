"""Backward-compatible Codex observer module.

The implementation now lives in :mod:`ai_observe.observe`.  This module is
kept as an alias rather than a simple re-export so existing tests and callers
that monkeypatch module-level helpers such as ``LiveTracer`` or
``safe_write_jsonl`` still affect the code path used by ``run()``.
"""
from __future__ import annotations

import sys as _sys

from . import observe as _observe

_sys.modules[__name__] = _observe
