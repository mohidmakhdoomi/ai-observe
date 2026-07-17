"""Reusable harness for testing ai-observe against real agent sessions.

Drives a real AI coding agent (claude / agy / codex) in NON-INTERACTIVE mode,
wrapped by ai-observe, and (optionally) monitors the browser viewer
concurrently by HTTP-polling its sanitized `/session` and `/events` endpoints.

Stdlib only -- no third-party deps (per EXPERIMENT protocol: harness deps stay
inside the experiment folder, and here we need none). Import this module from
sibling experiments:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "1_driving_mechanism"))
    from harness import run_observed_session, TOOLS

Design decisions (see notes.md for the feasibility evidence):

* Driving mechanism = non-interactive single-prompt invocation. All three
  tools support it and it is the only mechanism that is scriptable, hermetic,
  and repeatable without a pseudo-terminal. tmux/expect send-keys is documented
  as an alternative for genuinely interactive-only flows but is NOT the default
  because it is flaky and PTY-dependent.
* Monitoring mechanism = HTTP-poll of the viewer server. The viewer already
  exposes everything the browser UI consumes over two endpoints, and polling
  them with urllib proves "the viewer showed X" without a headless browser
  (which is heavier and not installed here).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Repo / path helpers
# ---------------------------------------------------------------------------

def repo_root() -> Path:
    """Locate the ai-observe checkout root (the dir containing bin/ai-observe)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "bin" / "ai-observe").exists():
            return parent
    raise RuntimeError("could not locate repo root (bin/ai-observe not found)")


ROOT = repo_root()
AI_OBSERVE = ROOT / "bin" / "ai-observe"
SRC = ROOT / "src"


# ---------------------------------------------------------------------------
# Tool command builders (non-interactive invocation)
# ---------------------------------------------------------------------------

def _claude_cmd(prompt: str, workdir: Path) -> list[str]:
    return ["claude", "-p", prompt, "--dangerously-skip-permissions"]


def _agy_cmd(prompt: str, workdir: Path) -> list[str]:
    # agy defaults to its own scratch workspace (~/.gemini/antigravity-cli/scratch);
    # --add-dir makes the target workdir writable so ai-observe (watching workdir)
    # actually sees the writes.
    return ["agy", "-p", prompt, "--dangerously-skip-permissions",
            "--add-dir", str(workdir)]


def _codex_cmd(prompt: str, workdir: Path) -> list[str]:
    # workspace-write lets codex write inside the cwd without approval prompts.
    return ["codex", "exec", "--sandbox", "workspace-write", prompt]


TOOLS = {
    "claude": _claude_cmd,
    "agy": _agy_cmd,
    "codex": _codex_cmd,
}


def tool_available(tool: str) -> bool:
    from shutil import which
    return which(tool) is not None


# ---------------------------------------------------------------------------
# Viewer monitor (HTTP-poll of the sanitized endpoints)
# ---------------------------------------------------------------------------

class ViewerMonitor:
    """Starts the ai-observe viewer on a jsonl and polls its HTTP endpoints.

    Captures whatever the *browser* would see: the sanitized `/session`
    metadata and the sanitized `/events` SSE stream. Proves the viewer path
    without a headless browser.

    Note on timing: the viewer tails its jsonl asynchronously, so at the moment
    a client connects to `/events` the backlog may still be empty -- events are
    then pushed incrementally as `append_batch` frames. The server also keeps
    the SSE connection open indefinitely (it cannot know the writer has exited),
    so we cannot wait for EOF. `collect_events` therefore reads with a short
    socket timeout and stops once the event count has *settled*.
    """

    def __init__(self, jsonl_path: Path, port: int = 7899):
        self.jsonl_path = Path(jsonl_path)
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self._proc: subprocess.Popen | None = None
        self._events: list[dict] = []
        self._session: dict | None = None

    def start(self, timeout: float = 10.0) -> bool:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "ai_observe.viewer", str(self.jsonl_path),
             "--port", str(self.port), "--no-browser"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._fetch_session()
                return True
            except Exception:
                time.sleep(0.15)
        return False

    def _fetch_session(self) -> dict:
        with urllib.request.urlopen(self.url + "/session", timeout=2) as r:
            self._session = json.loads(r.read())
        return self._session

    def collect_events(self, max_wait: float = 8.0, settle: float = 1.5) -> list[dict]:
        """Connect to /events and read append_batch frames until settled.

        Uses a raw non-blocking socket + select (the SSE stream never closes on
        its own, and urllib's read-after-timeout on such a stream is
        unreliable). Returns after `settle` seconds with no new event, or after
        `max_wait` seconds total. Refreshes /session afterwards.
        """
        import select

        self._events = []
        try:
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
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
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    @property
    def viewer_events(self) -> list[dict]:
        return list(self._events)

    @property
    def session_info(self) -> dict | None:
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


def summarize_events(events: list[dict], workdir: Path | None = None) -> dict:
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
    viewer_session_info: dict | None
    workdir_files: list[str]
    jsonl_path: str
    meta: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


def run_observed_session(
    tool: str,
    prompt: str,
    session: str,
    workdir: Path,
    outdir: Path,
    *,
    roots: Path | None = None,
    backends: str | None = None,
    timeout: float = 240.0,
    monitor: bool = True,
    viewer_port: int = 7899,
    disable_observe: bool = False,
) -> SessionResult:
    """Drive `tool` with `prompt` under ai-observe; optionally watch the viewer.

    Returns a SessionResult combining: agent output, on-disk canonical events,
    what the viewer served, and the actual files left in workdir -- so callers
    can compare "what the agent did" vs "what ai-observe reported" vs "what the
    viewer showed".
    """
    workdir = Path(workdir).resolve()
    outdir = Path(outdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    roots = Path(roots).resolve() if roots else workdir

    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}; known: {list(TOOLS)}")
    if not tool_available(tool):
        return SessionResult(
            tool=tool, session=session, prompt=prompt, ok=False, returncode=-1,
            duration_s=0.0, agent_stdout_tail="", disk_events={}, viewer_events_count=0,
            viewer_session_info=None, workdir_files=[], jsonl_path="",
            notes=[f"tool {tool!r} not available on this machine"],
        )

    cmd = [str(AI_OBSERVE), "--session", session, "--"] + TOOLS[tool](prompt, workdir)
    env = dict(os.environ)
    env["AI_OBSERVE_DIR"] = str(outdir)
    env["AI_OBSERVE_ROOTS"] = str(roots)
    if backends:
        env["AI_OBSERVE_BACKENDS"] = backends
    if disable_observe:
        env["AI_OBSERVE_DISABLE"] = "1"

    jsonl_path = outdir / f"{session}.jsonl"

    t0 = time.time()
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
        mon = ViewerMonitor(jsonl_path, port=viewer_port)
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


if __name__ == "__main__":
    # Smoke: drive claude to write a file and print the combined report.
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", default="claude", choices=list(TOOLS))
    ap.add_argument("--prompt", default="Create a file named hello.txt containing exactly: hello. Then stop.")
    ap.add_argument("--session", default="smoke")
    ap.add_argument("--workdir", default="./_smoke_work")
    ap.add_argument("--outdir", default="./data/output")
    args = ap.parse_args()
    res = run_observed_session(
        args.tool, args.prompt, args.session,
        Path(args.workdir), Path(args.outdir),
    )
    print(json.dumps(res.to_dict(), indent=2, default=str))
