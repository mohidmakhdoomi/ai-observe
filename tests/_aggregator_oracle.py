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
from typing import Iterable, Optional


RECENCY_HALF_LIFE_MS = 60_000.0
DEFAULT_ENABLED_SOURCES = ("strace", "snapshot")
SOURCE_ORDER = ("strace", "snapshot")
CONFIDENCE_ORDER = ("direct", "inferred")

FACTORY_FILTER_PATTERNS = (
    "/home/*/.codex/**",
    "/home/*/.cache/**",
    "/tmp/**",
    "/var/tmp/**",
    "/proc/**",
    "/sys/**",
    "/dev/**",
    "/run/**",
)


def validate_filter_pattern(pattern) -> tuple[bool, str, str | None]:
    if not isinstance(pattern, str):
        return False, pattern, "filter pattern must be a string"
    trimmed = pattern.strip()
    if trimmed == "":
        return False, trimmed, "filter pattern must not be empty"
    if not trimmed.startswith("/"):
        return False, trimmed, "filter pattern must start with /"
    return True, trimmed, None


def _compile_segment_glob(segment: str) -> str:
    out = []
    for ch in segment:
        if ch == "*":
            out.append(r"[^/]*")
        elif ch == "?":
            out.append(r"[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def glob_to_regex_source(pattern: str) -> str:
    if pattern == "/":
        return r"^/$"
    segments = pattern[1:].split("/")
    out = "^"
    for i, seg in enumerate(segments):
        last = i == len(segments) - 1
        if seg == "**":
            if last:
                out += r"/.*" if out == "^" else r"(?:/.*)?"
            else:
                out += r"(?:/[^/]+)*"
        else:
            out += "/" + _compile_segment_glob(seg)
    return out + "$"


def compile_filter_pattern(pattern: str) -> re.Pattern:
    ok, normalized, error = validate_filter_pattern(pattern)
    if not ok:
        raise ValueError(error)
    return re.compile(glob_to_regex_source(normalized))


def _normalized_filter_patterns(patterns: Optional[Iterable[str]] = None) -> list[str]:
    source = FACTORY_FILTER_PATTERNS if patterns is None else patterns
    normalized = []
    for pattern in source:
        ok, value, error = validate_filter_pattern(pattern)
        if not ok:
            raise ValueError(error)
        normalized.append(value)
    return normalized


def compile_filter_patterns(patterns: Optional[Iterable[str]] = None) -> list[re.Pattern]:
    return [compile_filter_pattern(p) for p in _normalized_filter_patterns(patterns)]


def normalize_enabled_sources(raw=None) -> list[str]:
    if raw is None:
        raw = DEFAULT_ENABLED_SOURCES
    if isinstance(raw, dict):
        raw = [key for key, enabled in raw.items() if enabled]
    seen: set[str] = set()
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            continue
        trimmed = value.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        out.append(trimmed)
    return out


def order_values(values: Iterable[str], preferred: Iterable[str]) -> list[str]:
    pref = {value: index for index, value in enumerate(preferred)}
    unique = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return sorted(unique, key=lambda value: (pref.get(value, math.inf), value))


def normalize_event_source(event: dict) -> str:
    return event.get("source") or "strace"


def normalize_event_confidence(event: dict) -> str:
    return event.get("confidence") or "direct"


_DEFAULT_FILTER_REGEXES = compile_filter_patterns(FACTORY_FILTER_PATTERNS)


def is_filtered_path(path: Optional[str], regexes: Optional[Iterable[re.Pattern]] = None) -> bool:
    if not path:
        return False
    compiled = _DEFAULT_FILTER_REGEXES if regexes is None else regexes
    return any(rx.match(path) for rx in compiled)


def event_matches_filters(event: dict, regexes: Optional[Iterable[re.Pattern]] = None) -> bool:
    paths = [p for p in (event.get("path"), event.get("old_path"), event.get("new_path")) if p]
    if not paths:
        return False
    return all(is_filtered_path(p, regexes) for p in paths)


def is_noise(path: Optional[str]) -> bool:
    return is_filtered_path(path)


def event_is_noise(event: dict) -> bool:
    return event_matches_filters(event)


def _parse_ts_ms(ts: str) -> float:
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
    __slots__ = (
        "bytes_w",
        "events",
        "rec_acc",
        "rec_at_ms",
        "last_touched_ms",
        "tombstoned",
        "op_counts",
        "sources",
        "confidences",
    )

    def __init__(self) -> None:
        self.bytes_w = 0
        self.events = 0
        self.rec_acc = 0.0
        self.rec_at_ms = 0.0
        self.last_touched_ms = 0.0
        self.tombstoned = False
        self.op_counts: dict[str, int] = {}
        self.sources: set[str] = set()
        self.confidences: set[str] = set()

    def reset(self) -> None:
        self.bytes_w = 0
        self.events = 0
        self.rec_acc = 0.0
        self.rec_at_ms = 0.0
        self.last_touched_ms = 0.0
        self.tombstoned = False
        self.op_counts = {}
        self.sources = set()
        self.confidences = set()

    def bump_event(self, op: str) -> None:
        self.events += 1
        self.op_counts[op] = self.op_counts.get(op, 0) + 1

    def add_recency_at(self, when_ms: float, weight: float = 1.0) -> None:
        cur = _decay(self.rec_acc, self.rec_at_ms, when_ms) if self.rec_at_ms else 0.0
        self.rec_acc = cur + weight
        self.rec_at_ms = when_ms

    def update_last_touched(self, when_ms: float) -> None:
        if when_ms > self.last_touched_ms:
            self.last_touched_ms = when_ms

    def record_provenance(self, event: dict) -> None:
        self.sources.add(normalize_event_source(event))
        self.confidences.add(normalize_event_confidence(event))


class Aggregator:
    """In-memory aggregation of per-path filesystem activity."""

    def __init__(
        self,
        filter_patterns: Optional[Iterable[str]] = None,
        enabled_sources: Optional[Iterable[str] | dict[str, bool]] = None,
    ) -> None:
        self.filter_patterns = _normalized_filter_patterns(filter_patterns)
        self.enabled_sources = normalize_enabled_sources(enabled_sources)
        self._enabled_source_set = set(self.enabled_sources)
        self._filter_regexes = compile_filter_patterns(self.filter_patterns)
        self.paths: dict[str, _PathEntry] = {}
        self.filtered_event_count = 0
        self.total_event_count = 0
        self.latest_ts_ms = 0.0

    def reset(self) -> None:
        self.paths.clear()
        self.filtered_event_count = 0
        self.total_event_count = 0
        self.latest_ts_ms = 0.0

    def _entry(self, path: str) -> _PathEntry:
        entry = self.paths.get(path)
        if entry is None:
            entry = _PathEntry()
            self.paths[path] = entry
        return entry

    def _source_enabled(self, event: dict) -> bool:
        return normalize_event_source(event) in self._enabled_source_set

    def ingest(self, event: dict) -> None:
        if not self._source_enabled(event):
            return
        self.total_event_count += 1
        ts_ms = _parse_ts_ms(event["timestamp"])
        if ts_ms > self.latest_ts_ms:
            self.latest_ts_ms = ts_ms

        if event_matches_filters(event, self._filter_regexes):
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
            entry.reset()
        entry.bump_event(op)
        entry.update_last_touched(ts_ms)
        entry.add_recency_at(ts_ms, 1.0)
        entry.record_provenance(event)
        if op == "modify":
            result = event.get("result")
            if isinstance(result, int) and result > 0:
                entry.bytes_w += result

    def _apply_rename(self, event: dict, ts_ms: float) -> None:
        old = event.get("old_path")
        new = event.get("new_path")
        if not old and not new:
            return
        if old and new and old != new:
            src = self.paths.get(old)
            dst = self._entry(new)
            if dst.tombstoned:
                dst.reset()
            if src is not None:
                dst.bytes_w += src.bytes_w
                dst.events += src.events + 1
                for key, value in src.op_counts.items():
                    dst.op_counts[key] = dst.op_counts.get(key, 0) + value
                dst.op_counts["rename"] = dst.op_counts.get("rename", 0) + 1
                src_at_ts = _decay(src.rec_acc, src.rec_at_ms, ts_ms) if src.rec_at_ms else 0.0
                dst_at_ts = _decay(dst.rec_acc, dst.rec_at_ms, ts_ms) if dst.rec_at_ms else 0.0
                dst.rec_acc = src_at_ts + dst_at_ts + 1.0
                dst.rec_at_ms = ts_ms
                dst.last_touched_ms = max(dst.last_touched_ms, src.last_touched_ms, ts_ms)
                dst.sources.update(src.sources)
                dst.confidences.update(src.confidences)
                dst.record_provenance(event)
                src.tombstoned = True
                src.bytes_w = 0
                src.events = 0
                src.rec_acc = 0.0
                src.rec_at_ms = 0.0
                src.op_counts = {}
                src.sources = set()
                src.confidences = set()
            else:
                dst.events += 1
                dst.op_counts["rename"] = dst.op_counts.get("rename", 0) + 1
                dst.add_recency_at(ts_ms, 1.0)
                dst.update_last_touched(ts_ms)
                dst.record_provenance(event)
        else:
            known = new or old
            entry = self._entry(known)
            if entry.tombstoned:
                entry.reset()
            entry.bump_event("rename")
            entry.update_last_touched(ts_ms)
            entry.add_recency_at(ts_ms, 1.0)
            entry.record_provenance(event)

    def snapshot(self, *, metric: str = "bytes", include_noise: bool = False, include_filtered: bool = False) -> dict:
        include_filter_matches = include_noise or include_filtered
        now_ms = self.latest_ts_ms or 0.0
        root = {"path": "/", "name": "/", "isDir": True, "children": {}, "_files": []}

        for path, entry in self.paths.items():
            if entry.tombstoned:
                continue
            if not include_filter_matches and is_filtered_path(path, self._filter_regexes):
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
                        child = {
                            "path": "/" + "/".join(parts[: i + 1]),
                            "name": part,
                            "isDir": True,
                            "children": {},
                            "_files": [],
                        }
                        cur["children"][part] = child
                    cur = child

        def _finalize(node: dict) -> dict:
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
                        "sources": order_values(entry.sources, SOURCE_ORDER),
                        "confidences": order_values(entry.confidences, CONFIDENCE_ORDER),
                        "children": [],
                    }
                )
            for child in node["children"].values():
                kids.append(_finalize(child))
            kids.sort(key=lambda item: item["name"])
            return {
                "path": node["path"],
                "name": node["name"],
                "isDir": True,
                "bytes": sum(kid["bytes"] for kid in kids),
                "events": sum(kid["events"] for kid in kids),
                "recent": sum(kid["recent"] for kid in kids),
                "last_touched_ms": max((kid["last_touched_ms"] for kid in kids), default=0.0),
                "sources": order_values(
                    (source for kid in kids for source in kid.get("sources", [])),
                    SOURCE_ORDER,
                ),
                "confidences": order_values(
                    (confidence for kid in kids for confidence in kid.get("confidences", [])),
                    CONFIDENCE_ORDER,
                ),
                "children": kids,
            }

        return {
            "metric": metric,
            "include_noise": include_filter_matches,
            "enabled_sources": list(self.enabled_sources),
            "tree": _finalize(root),
            "filtered_event_count": self.filtered_event_count,
            "total_event_count": self.total_event_count,
            "latest_ts_ms": self.latest_ts_ms,
        }
