"""JSONL tailer for the ai_observe viewer.

Reads a `.jsonl` file forward, polling for appended bytes. Buffers partial
trailing lines until a newline arrives. On truncation or inode change,
reopens from offset 0 with a stderr warning. Skips malformed JSON lines and
lines with `schema_version != 1`, with stderr warnings. Empty-file startup
is supported (zero-byte file is not an error).

Unlike `codex_observe.LiveTracer`, this tailer does NOT flush a pending
fragment on stop. A non-newline-terminated fragment is held silently
during runtime and warned-about exactly once on shutdown.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional


SCHEMA_VERSION = 1


_SANITIZED_FIELDS = ("timestamp", "operation", "path", "old_path", "new_path", "result")


def sanitize_event(raw: dict) -> dict:
    """Return the public, page-safe subset of a JSONL event.

    Excludes `raw_syscall`, `command`, `pid`, `process`, `session_id`,
    `invocation_id`, `schema_version` per the spec's security posture.
    """
    return {k: raw.get(k) for k in _SANITIZED_FIELDS}


class JsonlTailer:
    """Tail a JSONL file. Call `start()` to launch the background thread.

    Each newly observed event is passed to `on_event(sanitized_dict)`. The
    callback is invoked from the tailer thread under no lock; the consumer
    is responsible for its own synchronization.
    """

    def __init__(
        self,
        path: Path,
        on_event: Callable[[dict], None],
        *,
        poll_interval: float = 0.25,
        warn: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
    ) -> None:
        self._path = Path(path)
        self._on_event = on_event
        self._poll_interval = float(poll_interval)
        self._warn = warn
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._buf = b""
        self._offset = 0
        self._inode: Optional[int] = None

    # ----- lifecycle -----

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("JsonlTailer already started")
        self._thread = threading.Thread(target=self._run, name="JsonlTailer", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        # On shutdown: warn once if a non-newline-terminated fragment is held.
        if self._buf:
            self._warn(
                f"ai_observe.viewer: discarding {len(self._buf)} bytes of incomplete trailing line on shutdown"
            )
            self._buf = b""

    # ----- internals -----

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # pragma: no cover - defensive
                self._warn(f"ai_observe.viewer: tailer poll error: {exc!r}")
            # Sleep on the stop event so shutdown is prompt.
            self._stop.wait(self._poll_interval)

    def _poll_once(self) -> None:
        try:
            st = os.stat(self._path)
        except FileNotFoundError:
            # The file went away. Drop state; next poll may find it again.
            if self._inode is not None:
                self._warn(f"ai_observe.viewer: source {self._path} disappeared; will reopen if it reappears")
                self._inode = None
                self._offset = 0
                self._buf = b""
            return

        # Detect inode change (rotation / replacement) or truncation.
        if self._inode is None:
            self._inode = st.st_ino
            self._offset = 0
            self._buf = b""
        elif st.st_ino != self._inode:
            self._warn(f"ai_observe.viewer: source {self._path} inode changed; reopening from 0")
            self._inode = st.st_ino
            self._offset = 0
            self._buf = b""
        elif st.st_size < self._offset:
            self._warn(f"ai_observe.viewer: source {self._path} truncated; reopening from 0")
            self._offset = 0
            self._buf = b""

        if st.st_size == self._offset:
            return

        try:
            with open(self._path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
        except FileNotFoundError:
            return

        if not chunk:
            return

        self._offset += len(chunk)
        data = self._buf + chunk if self._buf else chunk

        start = 0
        while True:
            nl = data.find(b"\n", start)
            if nl < 0:
                break
            self._handle_line(data[start:nl])
            start = nl + 1
        self._buf = data[start:] if start < len(data) else b""

    def _handle_line(self, line_bytes: bytes) -> None:
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            return
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            self._warn(f"ai_observe.viewer: skipping malformed JSONL line: {exc}")
            return
        if not isinstance(raw, dict):
            self._warn("ai_observe.viewer: skipping non-object JSONL line")
            return
        sv = raw.get("schema_version")
        if sv != SCHEMA_VERSION:
            self._warn(f"ai_observe.viewer: skipping event with schema_version={sv!r}")
            return
        try:
            self._on_event(sanitize_event(raw))
        except Exception as exc:  # pragma: no cover - defensive
            self._warn(f"ai_observe.viewer: on_event callback failed: {exc!r}")
