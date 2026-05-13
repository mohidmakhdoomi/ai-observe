"""Python mirror of `src/ai_observe/viewer/static/aggregator.js`.

The JS module is the canonical implementation; this module is the test
oracle. A parity test (`tests/test_viewer_aggregator.py::JsParityTests`)
runs both against the same fixture and asserts identical snapshots.
Whenever you change the aggregator behavior, change both.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Iterable, List, Optional


RECENCY_HALF_LIFE_MS = 60_000.0


# Exclude patterns (glob-ish, converted to regex). Aligned with the spec's
# "Path filtering" section: codex tmp, common caches, /tmp, /proc, /sys,
# /dev, /run.
_NOISE_REGEXES = [
    re.compile(p)
    for p in (
        r"^/home/[^/]+/\.codex(/|$)",
        r"^/home/[^/]+/\.cache(/|$)",
        r"^/tmp(/|$)",
        r"^/var/tmp(/|$)",
        r"^/proc(/|$)",
        r"^/sys(/|$)",
        r"^/dev(/|$)",
        r"^/run(/|$)",
    )
]


def is_noise(path: Optional[str]) -> bool:
    if not path:
        return False
    return any(rx.match(path) for rx in _NOISE_REGEXES)


def event_is_noise(event: dict) -> bool:
    """An event is noise iff every non-null path on it matches the
    exclude list."""
    paths = [p for p in (event.get("path"), event.get("old_path"), event.get("new_path")) if p]
    if not paths:
        return False
    return all(is_noise(p) for p in paths)


def _parse_ts_ms(ts: str) -> float:
    # ISO 8601 with trailing Z. Datetime handles offset-aware with +00:00 only,
    # so swap Z → +00:00.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000.0


def _decay(acc_value: float, acc_at_ms: float, now_ms: float) -> float:
    if acc_value == 0.0:
        return 0.0
    dt = max(0.0, now_ms - acc_at_ms)
    return acc_value * (2.0 ** (-dt / RECENCY_HALF_LIFE_MS))


class _PathEntry:
    __slots__ = ("bytes_w", "events", "rec_acc", "rec_at_ms", "last_touched_ms", "tombstoned", "op_counts")

    def __init__(self) -> None:
        self.bytes_w = 0
        self.events = 0
        self.rec_acc = 0.0
        self.rec_at_ms = 0.0
        self.last_touched_ms = 0.0
        self.tombstoned = False
        self.op_counts: dict = {}

    def bump_event(self, op: str) -> None:
        self.events += 1
        self.op_counts[op] = self.op_counts.get(op, 0) + 1

    def add_recency_at(self, when_ms: float, weight: float = 1.0) -> None:
        # Decay the current acc to `when_ms`, then add `weight`.
        cur = _decay(self.rec_acc, self.rec_at_ms, when_ms) if self.rec_at_ms else 0.0
        self.rec_acc = cur + weight
        self.rec_at_ms = when_ms

    def update_last_touched(self, when_ms: float) -> None:
        if when_ms > self.last_touched_ms:
            self.last_touched_ms = when_ms

    def to_state(self) -> dict:
        return {
            "bytes": self.bytes_w,
            "events": self.events,
            "rec_acc": self.rec_acc,
            "rec_at_ms": self.rec_at_ms,
            "last_touched_ms": self.last_touched_ms,
            "tombstoned": self.tombstoned,
            "op_counts": dict(self.op_counts),
        }


class Aggregator:
    """In-memory aggregation of per-path filesystem activity.

    Mirrors `aggregator.js`; semantics match the spec's Rename handling,
    metric definitions, and exclude-filter rules.
    """

    def __init__(self) -> None:
        self.paths: dict = {}
        self.filtered_event_count = 0
        self.total_event_count = 0
        self.latest_ts_ms = 0.0

    def reset(self) -> None:
        self.paths.clear()
        self.filtered_event_count = 0
        self.total_event_count = 0
        self.latest_ts_ms = 0.0

    def _entry(self, path: str) -> _PathEntry:
        e = self.paths.get(path)
        if e is None:
            e = _PathEntry()
            self.paths[path] = e
        return e

    def ingest(self, event: dict) -> None:
        self.total_event_count += 1
        ts_ms = _parse_ts_ms(event["timestamp"])
        if ts_ms > self.latest_ts_ms:
            self.latest_ts_ms = ts_ms

        # Event-level noise accounting (spec rule). Noise events are still
        # retained so the UI can reveal them later when include_noise=True.
        if event_is_noise(event):
            self.filtered_event_count += 1

        op = event["operation"]
        if op == "rename":
            self._apply_rename(event, ts_ms)
            return

        path = event.get("path")
        if not path:
            return
        entry = self._entry(path)
        if entry.tombstoned:
            # A fresh event for a tombstoned path resurrects it as a new entry.
            entry.tombstoned = False
            entry.bytes_w = 0
            entry.events = 0
            entry.rec_acc = 0.0
            entry.rec_at_ms = 0.0
            entry.last_touched_ms = 0.0
            entry.op_counts = {}
        entry.bump_event(op)
        entry.update_last_touched(ts_ms)
        entry.add_recency_at(ts_ms, 1.0)
        if op == "modify":
            result = event.get("result")
            if isinstance(result, int) and result > 0:
                entry.bytes_w += result

    def _apply_rename(self, event: dict, ts_ms: float) -> None:
        old = event.get("old_path")
        new = event.get("new_path")
        if not old and not new:
            return
        # Migrate state from old → new per spec.
        if old and new and old != new:
            src = self.paths.get(old)
            dst = self._entry(new)
            if dst.tombstoned:
                dst.tombstoned = False
            if src is not None:
                # Bytes: move.
                dst.bytes_w += src.bytes_w
                # Events: dst.events += src.events + 1 (rename charges dst).
                dst.events += src.events + 1
                # op_counts: merge + charge the rename to dst.
                for k, v in src.op_counts.items():
                    dst.op_counts[k] = dst.op_counts.get(k, 0) + v
                dst.op_counts["rename"] = dst.op_counts.get("rename", 0) + 1
                # Recency: decay src's acc to ts_ms, dst's to ts_ms, sum, add 1.
                src_at_ts = _decay(src.rec_acc, src.rec_at_ms, ts_ms) if src.rec_at_ms else 0.0
                dst_at_ts = _decay(dst.rec_acc, dst.rec_at_ms, ts_ms) if dst.rec_at_ms else 0.0
                dst.rec_acc = src_at_ts + dst_at_ts + 1.0
                dst.rec_at_ms = ts_ms
                # last_touched: max(src, dst, ts).
                dst.last_touched_ms = max(dst.last_touched_ms, src.last_touched_ms, ts_ms)
                # Tombstone source.
                src.tombstoned = True
                src.bytes_w = 0
                src.events = 0
                src.rec_acc = 0.0
                src.rec_at_ms = 0.0
                src.op_counts = {}
                # last_touched on src is no longer relevant.
            else:
                # No prior src state: dst just records the rename event.
                dst.events += 1
                dst.op_counts["rename"] = dst.op_counts.get("rename", 0) + 1
                dst.add_recency_at(ts_ms, 1.0)
                dst.update_last_touched(ts_ms)
        else:
            # Partial rename resolution is possible when one side is relative
            # to an unresolved directory fd. Preserve the event on whichever
            # path is known instead of dropping it.
            known = new or old
            entry = self._entry(known)
            entry.bump_event("rename")
            entry.update_last_touched(ts_ms)
            entry.add_recency_at(ts_ms, 1.0)

    # ----- snapshots -----

    def snapshot(self, *, metric: str = "bytes", include_noise: bool = False) -> dict:
        """Return a top-down hierarchical snapshot keyed by path.

        Tree nodes carry `{path, name, isDir, bytes, events, recent,
        last_touched_ms, children}`. Tombstoned entries are excluded.
        `include_noise` controls noise filtering at *snapshot time* so the
        same aggregation state can back both the default filtered view and
        the "Show noise" view without reconnecting or replaying events.
        """
        now_ms = self.latest_ts_ms or 0.0
        # Build the tree.
        root = {"path": "/", "name": "/", "isDir": True, "children": {}, "_files": []}
        for path, entry in self.paths.items():
            if entry.tombstoned:
                continue
            if not include_noise and is_noise(path):
                continue
            if not path.startswith("/"):
                continue
            parts = [p for p in path.split("/") if p]
            cur = root
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                if is_last:
                    cur["_files"].append((part, path, entry))
                else:
                    child = cur["children"].get(part)
                    if child is None:
                        ancestor = "/" + "/".join(parts[: i + 1])
                        child = {
                            "path": ancestor,
                            "name": part,
                            "isDir": True,
                            "children": {},
                            "_files": [],
                        }
                        cur["children"][part] = child
                    cur = child

        def _finalize(node: dict) -> dict:
            # Convert children + _files into a sorted children list with
            # aggregates rolled up.
            kids = []
            for fname, fpath, entry in node["_files"]:
                recent = _decay(entry.rec_acc, entry.rec_at_ms, now_ms) if entry.rec_at_ms else 0.0
                kids.append(
                    {
                        "path": fpath,
                        "name": fname,
                        "isDir": False,
                        "bytes": entry.bytes_w,
                        "events": entry.events,
                        "recent": recent,
                        "last_touched_ms": entry.last_touched_ms,
                        "children": [],
                    }
                )
            for name, child in node["children"].items():
                kids.append(_finalize(child))
            kids.sort(key=lambda n: n["name"])
            bytes_sum = sum(k["bytes"] for k in kids)
            events_sum = sum(k["events"] for k in kids)
            recent_sum = sum(k["recent"] for k in kids)
            last_touched = max((k["last_touched_ms"] for k in kids), default=0.0)
            return {
                "path": node["path"],
                "name": node["name"],
                "isDir": True,
                "bytes": bytes_sum,
                "events": events_sum,
                "recent": recent_sum,
                "last_touched_ms": last_touched,
                "children": kids,
            }

        tree = _finalize(root)
        return {
            "metric": metric,
            "include_noise": include_noise,
            "tree": tree,
            "filtered_event_count": self.filtered_event_count,
            "total_event_count": self.total_event_count,
            "latest_ts_ms": self.latest_ts_ms,
        }
