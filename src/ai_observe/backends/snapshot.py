"""Concrete snapshot backend for ai-observe."""
from __future__ import annotations

from dataclasses import dataclass, field
import sys
from typing import Any, Callable

from . import BackendCapabilities, BackendSession


ErrorFactory = Callable[[str, int], Exception]


@dataclass
class SnapshotBackend:
    """Capture start/end manifests and merge inferred snapshot events."""

    error_factory: ErrorFactory
    prepare_plan: Callable[..., Any]
    finalize_plan: Callable[..., Any]
    merge_snapshot_events: Callable[..., tuple[Any, int]]
    build_snapshot_summary: Callable[[Any], dict[str, Any]]
    build_session_meta: Callable[..., dict[str, Any]]
    safe_write_meta: Callable[..., None]
    name: str = "snapshot"
    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(
            inferred_events=True,
            watched_root_reconciliation=True,
        )
    )
    _plan: Any = field(default=None, init=False, repr=False)

    def prepare(self, session: BackendSession) -> None:
        self._plan = self.prepare_plan(
            session.env,
            session.logs,
            session.initial_cwd,
            session.active_artifacts,
            error_prefix=session.error_prefix,
        )
        if getattr(self._plan, "roots", None):
            return

        try:
            self.safe_write_meta(
                session.logs.meta_path,
                self.build_session_meta(
                    session.logs,
                    "snapshot_root_error",
                    None,
                    list(getattr(self._plan, "warnings", [])),
                    snapshot_summary=self.build_snapshot_summary(self._plan),
                    parser_source=session.state.parser_source,
                ),
                session.logs.observe_dir,
            )
        except Exception as exc:
            print(f"{session.error_prefix}: warning: could not write {session.logs.meta_path}: {exc}", file=sys.stderr)
        raise self.error_factory("no usable snapshot roots remain after resolving AI_OBSERVE_ROOTS", 1)

    def stop(self, session: BackendSession) -> None:
        del session

    def finalize(self, session: BackendSession, codex_code: int) -> None:
        del codex_code
        if self._plan is None:
            return
        self._plan = self.finalize_plan(self._plan, session.logs, error_prefix=session.error_prefix)
        snapshot_summary = self.build_snapshot_summary(self._plan)
        if getattr(self._plan, "raw_events", None):
            try:
                authoritative_path, emitted_snapshot_events = self.merge_snapshot_events(
                    session.logs,
                    session.state.authoritative_path,
                    session.state.parser_status,
                    self._plan.raw_events,
                    error_prefix=session.error_prefix,
                )
                session.state.authoritative_path = authoritative_path
                snapshot_summary["emitted_event_count"] = emitted_snapshot_events
            except Exception as exc:
                session.state.meta_warnings.append(f"snapshot merge failed: {exc}")
                print(f"{session.error_prefix}: warning: snapshot merge failed: {exc}", file=sys.stderr)
        session.state.meta_warnings.extend(getattr(self._plan, "warnings", []))
        session.state.snapshot_summary = snapshot_summary
