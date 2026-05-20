"""Concrete strace backend for ai-observe."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys
from typing import Any, Callable

from . import BackendCapabilities, BackendSession


ErrorFactory = Callable[[str, int], Exception]


@dataclass
class StraceBackend:
    """Wrap command execution under strace and produce direct events."""

    error_factory: ErrorFactory
    trace_parser_cls: Any
    live_tracer_cls: Any
    parse_trace_file: Callable[..., Any]
    safe_write_jsonl: Callable[[Path, Any, Path], None]
    env_flag: Callable[[dict[str, str], str], bool]
    env_value: Callable[[dict[str, str], str, str | None], str | None]
    live_enabled: Callable[[dict[str, str]], bool]
    live_poll_seconds: Callable[[dict[str, str]], float]
    live_join_timeout: Callable[[dict[str, str]], float]
    name: str = "strace"
    capabilities: BackendCapabilities = field(
        default_factory=lambda: BackendCapabilities(
            wraps_command=True,
            direct_events=True,
            requires_linux=True,
            requires_strace_binary=True,
        )
    )
    _live_tracer: Any = field(default=None, init=False, repr=False)

    def prepare(self, session: BackendSession) -> None:
        if not sys.platform.startswith("linux"):
            raise self.error_factory("Linux required for strace backend", 1)

        strace = shutil.which("strace", path=session.env.get("PATH"))
        if not strace:
            raise self.error_factory(
                "strace not found; install strace or set AI_OBSERVE_DISABLE=1 "
                "(legacy CODEV_OBSERVE_DISABLE=1)",
                127,
            )

        session.child_env["AI_OBSERVE_NESTED"] = "1"
        session.launch_argv = [
            strace,
            "-f",
            "-qq",
            "-ttt",
            "-s",
            "4096",
            "-yy",
            "-o",
            str(session.logs.trace_path),
            "-e",
            "trace=%file,%desc,%process",
            *session.real_argv,
        ]
        session.state.parser_source = "strace"

        if not self.live_enabled(session.env):
            return

        live_parser = self.trace_parser_cls(
            session_id=session.logs.session_id,
            invocation_id=session.logs.session_id,
            command=session.command,
            initial_cwd=session.initial_cwd,
            active_artifacts=session.active_artifacts,
            include_log_writes=session.include_log_writes,
            fail_after_events=session.fail_after_events,
            watched_roots=session.watched_roots,
        )
        candidate = self.live_tracer_cls(
            session.logs.trace_path,
            session.logs.jsonl_path,
            session.logs.observe_dir,
            live_parser,
            self.live_poll_seconds(session.env),
        )
        try:
            candidate.start()
            self._live_tracer = candidate
        except Exception as exc:
            print(
                f"{session.error_prefix}: warning: live tracer failed to start: {exc}; continuing with post-hoc-only",
                file=sys.stderr,
            )
            self._live_tracer = None

    def stop(self, session: BackendSession) -> None:
        if self._live_tracer is not None:
            self._live_tracer.request_stop()

    def finalize(self, session: BackendSession, codex_code: int) -> None:
        parse_failed = False
        parser_status = "ok"
        authoritative_path: Path | None = session.logs.jsonl_path
        meta_warnings: list[str] = []

        if self._live_tracer is not None:
            timed_out, live_error, live_pf = self._live_tracer.join(self.live_join_timeout(session.env))
            if timed_out:
                parse_failed = True
                parser_status = "live_timeout"
                meta_warnings.append("live parser did not exit before timeout; rebuilt artifact may be authoritative")
                print(
                    f"{session.error_prefix}: live parser did not exit within join timeout; rebuilding full trace to "
                    f"{session.logs.rebuilt_path}; original exit {codex_code}",
                    file=sys.stderr,
                )
                try:
                    result = self.parse_trace_file(
                        session.logs.trace_path,
                        None,
                        session_id=session.logs.session_id,
                        invocation_id=session.logs.session_id,
                        command=session.command,
                        initial_cwd=session.initial_cwd,
                        active_artifacts=session.active_artifacts,
                        include_log_writes=session.include_log_writes,
                        watched_roots=session.watched_roots,
                    )
                    self.safe_write_jsonl(session.logs.rebuilt_path, result.events, session.logs.observe_dir)
                    parser_status = "live_timeout_rebuilt"
                    authoritative_path = session.logs.rebuilt_path
                    parse_failed = False
                except Exception as exc:
                    parse_failed, parser_status, authoritative_path = self._handle_rebuild_failure(
                        session,
                        codex_code,
                        exc,
                        parser_failure_status="live_timeout_rebuild_parser_failure",
                        failed_status="live_timeout_rebuild_failed",
                        default_warning_prefix="full-trace rebuild failed",
                    )
                    if not parser_status.endswith("parser_failure"):
                        meta_warnings.append(f"full-trace rebuild failed: {type(exc).__name__}: {exc}")
            elif live_pf is not None:
                parse_failed = True
                parser_status = "parser_failure_partial"
                authoritative_path = None
                meta_warnings.append("live parser failed; partial direct events are in .jsonl.partial")
                self._write_partial(session, codex_code, live_pf)
                try:
                    self.safe_write_jsonl(session.logs.jsonl_path, [], session.logs.observe_dir)
                except Exception:
                    pass
            elif live_error is not None:
                parse_failed = True
                parser_status = "live_error"
                meta_warnings.append(
                    f"live parser raised {type(live_error).__name__}; rebuilt canonical .jsonl post hoc"
                )
                print(
                    f"{session.error_prefix}: warning: live parser raised {type(live_error).__name__}: {live_error}; "
                    f"falling back to post-hoc rebuild; original exit {codex_code}",
                    file=sys.stderr,
                )
                try:
                    result = self.parse_trace_file(
                        session.logs.trace_path,
                        None,
                        session_id=session.logs.session_id,
                        invocation_id=session.logs.session_id,
                        command=session.command,
                        initial_cwd=session.initial_cwd,
                        active_artifacts=session.active_artifacts,
                        include_log_writes=session.include_log_writes,
                        fail_after_events=session.fail_after_events,
                        watched_roots=session.watched_roots,
                    )
                    self.safe_write_jsonl(session.logs.jsonl_path, result.events, session.logs.observe_dir)
                    parser_status = "live_error_rebuilt"
                    authoritative_path = session.logs.jsonl_path
                except Exception as exc:
                    parse_failed, parser_status, authoritative_path = self._handle_live_error_rebuild_failure(
                        session,
                        codex_code,
                        exc,
                    )
                    if parser_status == "live_error_rebuild_failed":
                        meta_warnings.append(f"post-hoc rebuild failed: {type(exc).__name__}: {exc}")
        else:
            try:
                result = self.parse_trace_file(
                    session.logs.trace_path,
                    None,
                    session_id=session.logs.session_id,
                    invocation_id=session.logs.session_id,
                    command=session.command,
                    initial_cwd=session.initial_cwd,
                    active_artifacts=session.active_artifacts,
                    include_log_writes=session.include_log_writes,
                    fail_after_events=session.fail_after_events,
                    watched_roots=session.watched_roots,
                )
                self.safe_write_jsonl(session.logs.jsonl_path, result.events, session.logs.observe_dir)
                parser_status = "ok"
                authoritative_path = session.logs.jsonl_path
                parse_failed = False
            except Exception as exc:
                parse_failed, parser_status, authoritative_path = self._handle_post_hoc_failure(
                    session,
                    codex_code,
                    exc,
                )
                if parser_status == "parser_failure_empty_partial":
                    meta_warnings.append(f"post-hoc parser failed: {type(exc).__name__}: {exc}")
                else:
                    meta_warnings.append("post-hoc parser failed; partial direct events are in .jsonl.partial")

        session.state.parse_failed = parse_failed
        session.state.parser_status = parser_status
        session.state.authoritative_path = authoritative_path
        session.state.meta_warnings.extend(meta_warnings)

    def _handle_rebuild_failure(
        self,
        session: BackendSession,
        codex_code: int,
        exc: Exception,
        *,
        parser_failure_status: str,
        failed_status: str,
        default_warning_prefix: str,
    ) -> tuple[bool, str, Path | None]:
        events = getattr(exc, "events", None)
        if events is not None:
            try:
                self.safe_write_jsonl(session.logs.partial_path, events, session.logs.observe_dir)
            except Exception as inner:
                print(
                    f"{session.error_prefix}: timeout rebuild parser failed; could not write "
                    f"{session.logs.partial_path}; original exit {codex_code}: {inner}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{session.error_prefix}: timeout rebuild parser failed; wrote {session.logs.partial_path}; "
                    f"original exit {codex_code}",
                    file=sys.stderr,
                )
            return True, parser_failure_status, None

        print(
            f"{session.error_prefix}: timeout rebuild failed; leaving partial .jsonl; original exit {codex_code}: {exc}",
            file=sys.stderr,
        )
        return True, failed_status, None

    def _handle_live_error_rebuild_failure(
        self,
        session: BackendSession,
        codex_code: int,
        exc: Exception,
    ) -> tuple[bool, str, Path | None]:
        events = getattr(exc, "events", None)
        if events is not None:
            self._write_partial(session, codex_code, exc)
            try:
                self.safe_write_jsonl(session.logs.jsonl_path, [], session.logs.observe_dir)
            except Exception:
                pass
            return True, "live_error_rebuild_parser_failure", None

        try:
            self.safe_write_jsonl(session.logs.partial_path, [], session.logs.observe_dir)
            print(
                f"{session.error_prefix}: parser failed; wrote empty {session.logs.partial_path}; original exit "
                f"{codex_code}: {exc}",
                file=sys.stderr,
            )
        except Exception as inner:
            print(
                f"{session.error_prefix}: parser failed; could not write {session.logs.partial_path}; original exit "
                f"{codex_code}: {exc}; secondary: {inner}",
                file=sys.stderr,
            )
        try:
            self.safe_write_jsonl(session.logs.jsonl_path, [], session.logs.observe_dir)
        except Exception:
            pass
        return True, "live_error_rebuild_failed", None

    def _handle_post_hoc_failure(
        self,
        session: BackendSession,
        codex_code: int,
        exc: Exception,
    ) -> tuple[bool, str, Path | None]:
        events = getattr(exc, "events", None)
        if events is not None:
            self.safe_write_jsonl(session.logs.partial_path, events, session.logs.observe_dir)
            print(
                f"{session.error_prefix}: parser failed; wrote {session.logs.partial_path}; original exit "
                f"{codex_code}",
                file=sys.stderr,
            )
            return True, "parser_failure_partial", None

        self.safe_write_jsonl(session.logs.partial_path, [], session.logs.observe_dir)
        print(
            f"{session.error_prefix}: parser failed; wrote empty {session.logs.partial_path}; original exit "
            f"{codex_code}: {exc}",
            file=sys.stderr,
        )
        return True, "parser_failure_empty_partial", None

    def _write_partial(self, session: BackendSession, codex_code: int, parser_failure: Any) -> None:
        try:
            self.safe_write_jsonl(session.logs.partial_path, parser_failure.events, session.logs.observe_dir)
        except Exception as exc:
            print(
                f"{session.error_prefix}: parser failed; could not write {session.logs.partial_path}; original exit "
                f"{codex_code}: {exc}",
                file=sys.stderr,
            )
        else:
            print(
                f"{session.error_prefix}: parser failed; wrote {session.logs.partial_path}; original exit "
                f"{codex_code}",
                file=sys.stderr,
            )
