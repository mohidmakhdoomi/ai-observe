"""Graduated core harness: drive a real agent session under ai-observe (Spec 38).

Graduated from `experiments/1_driving_mechanism/harness.py`. Behavioural changes
from the experiment (all per the approved spec):

* **Entrypoint resolution (Decision 8):** prefer the checkout `bin/ai-observe`,
  fall back to an installed `ai-observe` console script only when the checkout
  shim is absent. A *test* suite must exercise the working tree it imports from;
  preferring an installed script risks a stale global binary observing while the
  assertions target local code.
* **In-process viewer on an OS-assigned ephemeral port (Decision 6):** the viewer
  monitor runs `ai_observe.viewer.server.ViewerServer(jsonl, port=0)` in-process
  and reads the chosen address from `.url`/`.port`, instead of spawning a
  subprocess on a hard-coded sequential port. The OS never hands out the same
  listening port twice, so parallel runs cannot collide by construction.
* **No `sys.path` hack into `experiments/`** (N1): `ai_observe` is imported via the
  package's `src/`-on-path convention (see `__init__.py`).

Stdlib-only. `ai_observe` itself is imported in-process for the viewer.
"""

from __future__ import annotations

import json
import os
import select
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from pathlib import Path
from shutil import which
from typing import Callable, Optional

from . import ROOT

SRC = ROOT / "src"


# ---------------------------------------------------------------------------
# ai-observe wrapper entrypoint resolution (checkout-first; Decision 8)
# ---------------------------------------------------------------------------

def resolve_ai_observe() -> str:
    """Locate the `ai-observe` wrapper CLI, preferring the checkout shim.

    Checkout `bin/ai-observe` first (test the working tree), then an installed
    `ai-observe` console script on PATH. Raises if neither is found.
    """
    checkout = ROOT / "bin" / "ai-observe"
    if checkout.exists():
        return str(checkout)
    installed = which("ai-observe")
    if installed:
        return installed
    raise RuntimeError(
        "could not locate the ai-observe wrapper: no checkout bin/ai-observe and "
        "no installed 'ai-observe' on PATH"
    )


# ---------------------------------------------------------------------------
# Tool command builders (non-interactive invocation)
# ---------------------------------------------------------------------------

def _claude_cmd(prompt: str, workdir: Path) -> list[str]:
    return ["claude", "-p", prompt, "--dangerously-skip-permissions"]


def _agy_cmd(prompt: str, workdir: Path) -> list[str]:
    # agy defaults to its own scratch workspace; --add-dir makes the target
    # workdir writable so ai-observe (watching workdir) actually sees the writes.
    return ["agy", "-p", prompt, "--dangerously-skip-permissions",
            "--add-dir", str(workdir)]


def _codex_cmd(prompt: str, workdir: Path) -> list[str]:
    # workspace-write lets codex write inside the cwd without approval prompts.
    return ["codex", "exec", "--sandbox", "workspace-write", prompt]


TOOLS: dict[str, Callable[[str, Path], list[str]]] = {
    "claude": _claude_cmd,
    "agy": _agy_cmd,
    "codex": _codex_cmd,
}


def tool_available(tool: str) -> bool:
    return which(tool) is not None


# ---------------------------------------------------------------------------
# Viewer monitor (in-process ViewerServer on an OS-assigned ephemeral port)
# ---------------------------------------------------------------------------

class ViewerMonitor:
    """Run the ai-observe viewer in-process and capture what the browser sees.

    Attaches an `ai_observe.viewer.server.ViewerServer` to a finalized (or
    in-progress) `.jsonl` on an OS-assigned ephemeral port, then reads the same
    sanitized `/session` metadata and `/events` SSE stream the browser UI
    consumes — proving "the viewer showed X" without a headless browser.

    Timing note (unchanged from the experiment): the viewer tails its jsonl
    asynchronously and keeps the SSE connection open indefinitely (it cannot know
    the writer has exited), so `collect_events` reads with a short socket timeout
    and stops once the event count has *settled*.
    """

    def __init__(self, jsonl_path: Path):
        self.jsonl_path = Path(jsonl_path)
        self._server = None  # ai_observe.viewer.server.ViewerServer
        self._events: list[dict] = []
        self._session: Optional[dict] = None

    @property
    def url(self) -> str:
        return self._server.url if self._server is not None else ""

    @property
    def port(self) -> int:
        return self._server.port if self._server is not None else 0

    def start(self, timeout: float = 10.0) -> bool:
        # Imported here (not at module load) so importing the harness never
        # requires ai_observe to be importable until a viewer is actually used.
        from ai_observe.viewer.server import ViewerServer

        # Construction binds the listening socket, so it can raise (e.g.
        # PermissionError/OSError on a restricted host); normalize any startup
        # failure — construction OR serve — into the boolean API the callers and
        # self-tests expect, rather than letting it propagate.
        try:
            self._server = ViewerServer(self.jsonl_path, port=0)
            self._server.start()
        except Exception:
            self._server = None
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._fetch_session()
                return True
            except Exception:
                time.sleep(0.15)
        return False

    def _fetch_session(self) -> dict:
        with urllib.request.urlopen(self.url + "session", timeout=2) as r:
            self._session = json.loads(r.read())
        return self._session

    def collect_events(self, max_wait: float = 8.0, settle: float = 1.5) -> list[dict]:
        """Connect to /events and read append_batch frames until settled.

        Uses a raw non-blocking socket + select (the SSE stream never closes on
        its own). Returns after `settle` seconds with no new event, or after
        `max_wait` seconds total. Refreshes /session afterwards.
        """
        self._events = []
        # Parse the host/port from the server's own url rather than assuming a
        # host (the port is OS-assigned; the host is whatever ViewerServer bound).
        parts = urlsplit(self.url)
        host, port = (parts.hostname or "127.0.0.1"), (parts.port or self.port)
        if not port:
            return []
        try:
            sock = socket.create_connection((host, port), timeout=3)
        except Exception:
            return []
        try:
            sock.sendall(
                b"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Accept: text/event-stream\r\nConnection: close\r\n\r\n"
            )
            sock.setblocking(False)
            buf = b""
            headers_done = False
            t0 = time.time()
            last_change = time.time()
            while time.time() - t0 < max_wait:
                r, _, _ = select.select([sock], [], [], 0.3)
                if r:
                    try:
                        chunk = sock.recv(4096)
                    except (BlockingIOError, InterruptedError):
                        continue
                    except Exception:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    if not headers_done and b"\r\n\r\n" in buf:
                        _, buf = buf.split(b"\r\n\r\n", 1)
                        headers_done = True
                    if headers_done:
                        before = len(self._events)
                        while b"\n\n" in buf:
                            frame, buf = buf.split(b"\n\n", 1)
                            self._handle_frame(frame.decode("utf-8", "replace"))
                        if len(self._events) != before:
                            last_change = time.time()
                if self._events and time.time() - last_change >= settle:
                    break
        finally:
            try:
                sock.close()
            except Exception:
                pass
        try:
            self._fetch_session()
        except Exception:
            pass
        return list(self._events)

    def _handle_frame(self, frame: str) -> None:
        kind = None
        data = None
        for line in frame.splitlines():
            if line.startswith("event:"):
                kind = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if kind == "append_batch" and data:
            try:
                for ev in json.loads(data):
                    self._events.append(ev)
            except Exception:
                pass

    def poll_session(self) -> dict:
        return self._fetch_session()

    def stop(self) -> None:
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None

    @property
    def viewer_events(self) -> list[dict]:
        return list(self._events)

    @property
    def session_info(self) -> Optional[dict]:
        return self._session


# ---------------------------------------------------------------------------
# Event summarization (from the canonical jsonl on disk)
# ---------------------------------------------------------------------------

def load_events(jsonl_path: Path) -> list[dict]:
    events = []
    p = Path(jsonl_path)
    if not p.exists():
        return events
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except Exception:
                pass
    return events


def summarize_events(events: list[dict], workdir: Optional[Path] = None) -> dict:
    by_source: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_operation: dict[str, int] = {}
    rows = []
    for e in events:
        by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + 1
        by_confidence[e.get("confidence", "?")] = by_confidence.get(e.get("confidence", "?"), 0) + 1
        by_operation[e.get("operation", "?")] = by_operation.get(e.get("operation", "?"), 0) + 1
        path = e.get("path", "") or ""
        rel = path
        if workdir and str(workdir) in path:
            rel = path.split(str(workdir) + "/", 1)[-1]
        rows.append({
            "operation": e.get("operation"),
            "path": rel,
            "source": e.get("source"),
            "confidence": e.get("confidence"),
        })
    return {
        "total": len(events),
        "by_source": by_source,
        "by_confidence": by_confidence,
        "by_operation": by_operation,
        "rows": rows,
    }


def writes_onto(events: list[dict], name: str) -> int:
    """Count direct (strace) writes whose destination basename == `name`.

    Atomic-write tools (claude) rewrite a file as a fresh tmp+rename, so a
    modify/append shows up as a *rename* onto the target, not a `modify` — both
    count as a write here (ported from the round-2 multi-turn probe).
    """
    n = 0
    for e in events:
        if e.get("source") != "strace":
            continue
        dest = (e.get("new_path") or e.get("path") or "").rsplit("/", 1)[-1]
        if dest == name and e.get("operation") in ("create", "rename", "modify", "write"):
            n += 1
    return n


def list_workdir(workdir: Path) -> list[str]:
    out = []
    workdir = Path(workdir)
    if not workdir.exists():
        return out
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            out.append(str(p.relative_to(workdir)))
    return out


# ---------------------------------------------------------------------------
# The main entry point: drive one observed session
# ---------------------------------------------------------------------------

@dataclass
class SessionResult:
    tool: str
    session: str
    prompt: str
    ok: bool
    returncode: int
    duration_s: float
    agent_stdout_tail: str
    disk_events: dict
    viewer_events_count: int
    viewer_session_info: Optional[dict]
    workdir_files: list[str]
    jsonl_path: str
    meta: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def run_observed_command(
    command: list[str],
    *,
    tool: str,
    session: str,
    workdir: Path,
    outdir: Path,
    prompt: str = "",
    roots: Optional[Path] = None,
    backends: Optional[str] = None,
    timeout: float = 240.0,
    monitor: bool = True,
    disable_observe: bool = False,
    extra_env: Optional[dict] = None,
) -> SessionResult:
    """Run an arbitrary `command` (argv after the `--`) under ai-observe.

    The lower-level core shared by `run_observed_session` (single prompt), the
    round-2 chained multi-turn driver (`bash -lc "<t1> && <t2> && …"`), and the S7
    degraded scenario. Sequencing (Decision 11 / F5): the session runs to a finalized
    `.jsonl` first, then the in-process viewer attaches.

    `extra_env` injects additional ai-observe env knobs into the wrapper process
    (e.g. `AI_OBSERVE_TEST_FAIL_AFTER` for the S7 forced-degraded path); it is applied
    last, so a caller can override the defaults set here if ever needed.
    """
    workdir = Path(workdir).resolve()
    outdir = Path(outdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    roots = Path(roots).resolve() if roots else workdir

    ai_observe = resolve_ai_observe()
    cmd = [ai_observe, "--session", session, "--"] + list(command)
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(outdir)
    env["AI_OBSERVE_ROOTS"] = str(roots)
    if backends:
        env["AI_OBSERVE_BACKENDS"] = backends
    if disable_observe:
        env["AI_OBSERVE_DISABLE"] = "1"
    if extra_env:
        env.update(extra_env)

    jsonl_path = outdir / f"{session}.jsonl"

    t0 = time.time()
    stderr_tail = ""
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), env=env, timeout=timeout,
            capture_output=True, text=True,
        )
        returncode = proc.returncode
        stdout_tail = (proc.stdout or "")[-1500:]
        stderr_tail = (proc.stderr or "")[-800:]
        timed_out = False
    except subprocess.TimeoutExpired as e:
        returncode = -9
        stdout_tail = (e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or ""))[-1500:]
        stderr_tail = "TIMEOUT"
        timed_out = True
    duration = time.time() - t0

    disk = summarize_events(load_events(jsonl_path), workdir=roots)

    viewer_count = 0
    viewer_info = None
    notes = []
    if monitor and jsonl_path.exists() and jsonl_path.stat().st_size > 0:
        mon = ViewerMonitor(jsonl_path)
        if mon.start():
            events = mon.collect_events()
            viewer_count = len(events)
            viewer_info = mon.session_info
            mon.stop()
        else:
            notes.append("viewer failed to start")
    elif monitor:
        notes.append("no events on disk -- viewer monitor skipped")

    if timed_out:
        notes.append("agent invocation TIMED OUT")

    meta_path = outdir / f"{session}.meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            pass

    return SessionResult(
        tool=tool, session=session, prompt=prompt,
        ok=(returncode == 0), returncode=returncode, duration_s=round(duration, 1),
        agent_stdout_tail=stdout_tail, disk_events=disk,
        viewer_events_count=viewer_count, viewer_session_info=viewer_info,
        workdir_files=list_workdir(workdir), jsonl_path=str(jsonl_path),
        meta={"warnings": meta.get("warnings"), "stderr_tail": stderr_tail},
        notes=notes,
    )


def run_observed_session(
    tool: str,
    prompt: str,
    session: str,
    workdir: Path,
    outdir: Path,
    *,
    roots: Optional[Path] = None,
    backends: Optional[str] = None,
    timeout: float = 240.0,
    monitor: bool = True,
    disable_observe: bool = False,
) -> SessionResult:
    """Drive `tool` with a single `prompt` under ai-observe; optionally watch the
    viewer. Thin wrapper over `run_observed_command` using the tool's non-interactive
    invocation. Returns a SessionResult combining agent output, on-disk canonical
    events, what the viewer served, and the actual files left in workdir."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}; known: {list(TOOLS)}")
    if not tool_available(tool):
        return SessionResult(
            tool=tool, session=session, prompt=prompt, ok=False, returncode=-1,
            duration_s=0.0, agent_stdout_tail="", disk_events={}, viewer_events_count=0,
            viewer_session_info=None, workdir_files=[], jsonl_path="",
            notes=[f"tool {tool!r} not available on this machine"],
        )
    return run_observed_command(
        TOOLS[tool](prompt, Path(workdir).resolve()),
        tool=tool, session=session, workdir=workdir, outdir=outdir, prompt=prompt,
        roots=roots, backends=backends, timeout=timeout, monitor=monitor,
        disable_observe=disable_observe,
    )
