"""Backend protocol and selection helpers for ai-observe."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..observe import LogPaths


DEFAULT_BACKENDS = ("strace", "snapshot")
SUPPORTED_BACKENDS = frozenset(DEFAULT_BACKENDS)
PREPARE_ORDER = ("snapshot", "strace")
FINALIZE_ORDER = ("strace", "snapshot")


@dataclass(frozen=True)
class BackendCapabilities:
    wraps_command: bool = False
    direct_events: bool = False
    inferred_events: bool = False
    requires_linux: bool = False
    requires_strace_binary: bool = False
    watched_root_reconciliation: bool = False


@dataclass
class BackendState:
    authoritative_path: Path | None = None
    meta_warnings: list[str] = field(default_factory=list)
    parser_status: str = "backend_disabled"
    parser_source: str = "none"
    parse_failed: bool = False
    snapshot_summary: dict[str, Any] | None = None


@dataclass
class BackendSession:
    env: dict[str, str]
    child_env: dict[str, str]
    real_argv: list[str]
    launch_argv: list[str]
    command: list[str]
    logs: LogPaths
    initial_cwd: str
    active_artifacts: set[str]
    error_prefix: str
    include_log_writes: bool
    fail_after_events: int | None
    state: BackendState = field(default_factory=BackendState)


class Backend(Protocol):
    name: str
    capabilities: BackendCapabilities

    def prepare(self, session: BackendSession) -> None:
        """Validate/configure the backend before launching the child."""

    def stop(self, session: BackendSession) -> None:
        """Request the backend stop or drain after the child exits."""

    def finalize(self, session: BackendSession, codex_code: int) -> None:
        """Finish backend event production after the child exits."""


def parse_backend_selection(value: str | None) -> tuple[str, ...]:
    """Parse ``AI_OBSERVE_BACKENDS`` as a comma-separated backend list."""
    if value is None or not value.strip():
        return DEFAULT_BACKENDS
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not requested:
        return DEFAULT_BACKENDS

    seen: set[str] = set()
    names: list[str] = []
    invalid: list[str] = []
    for name in requested:
        if name not in SUPPORTED_BACKENDS:
            invalid.append(name)
            continue
        if name in seen:
            continue
        seen.add(name)
        names.append(name)

    if invalid:
        supported = ", ".join(DEFAULT_BACKENDS)
        unknown = ", ".join(invalid)
        raise ValueError(f"unsupported backend name(s): {unknown}; supported backends: {supported}")
    return tuple(names)


def backends_in_prepare_order(selected: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in PREPARE_ORDER if name in selected)


def backends_in_finalize_order(selected: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name for name in FINALIZE_ORDER if name in selected)
