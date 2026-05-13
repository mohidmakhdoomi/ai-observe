"""CLI: `python -m ai_observe.viewer <jsonl>`.

Per spec: bind 127.0.0.1 only; no --host flag; no --poll-ms flag (poll
interval is not user-tunable in v1). Validates the path is a regular file.
Optionally opens a browser tab via `webbrowser.open`; failure is silent.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from .server import ViewerServer


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ai_observe.viewer",
        description="Browser visualizer for ai_observe JSONL filesystem-event streams.",
    )
    p.add_argument("path", type=Path, help="Path to a .jsonl file produced by the observer.")
    p.add_argument(
        "--port",
        type=int,
        default=0,
        help="TCP port to bind on 127.0.0.1 (default: OS-chosen).",
    )
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open a browser tab; just print the URL.",
    )
    return p


def _validate_path(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"ai_observe.viewer: path does not exist: {path}")
    if path.is_dir():
        raise SystemExit(f"ai_observe.viewer: path is a directory, not a file: {path}")
    if not path.is_file():
        raise SystemExit(f"ai_observe.viewer: path is not a regular file: {path}")


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    _validate_path(args.path)

    server = ViewerServer(args.path, port=args.port)
    server.start()
    url = server.url
    print(f"ai_observe.viewer serving {args.path} at {url}", file=sys.stderr)

    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - best-effort
            print(f"ai_observe.viewer: webbrowser.open failed: {exc!r}", file=sys.stderr)

    stop = threading.Event()

    def _on_signal(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    try:
        while not stop.is_set():
            stop.wait(timeout=1.0)
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
