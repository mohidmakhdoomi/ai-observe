# Spec 5: Browser visualizer for filesystem-event JSONL

## Summary

Add a browser-based visualizer for the observer's `.jsonl` filesystem-event
stream, modeled on WinDirStat / TreeSize. It lets a user *watch* which files
Codex is touching, in real time, while a session is still running — and also
*review* a completed `.jsonl` post-hoc. The visualizer runs as a small local
Python server that opens a localhost page in the user's browser; the page
shows a hierarchical treemap of the touched filesystem on one side and a
sortable indented tree/table on the other, with selection linked between
the two views.

## Goals

- Render the directory tree implied by a session's `.jsonl` as a
  hierarchical treemap (WinDirStat-style: nested rectangles whose area is
  proportional to a chosen "size" metric).
- Render the same tree as a sortable indented table beside the treemap,
  with linked selection (hovering or clicking a rectangle highlights the
  corresponding row, and vice versa).
- Support two modes from the same entry point:
  - **Live**: tail a JSONL while Codex is still writing it; new events
    update the visualization within ~1 second.
  - **Static**: render a completed `.jsonl` end-to-end.
- Default the rectangle-area metric to **bytes written**, with a UI toggle
  to **event count** and to a **recency-weighted** metric (events decayed
  by age). All three operate on the same per-path aggregation.
- Apply a default exclude list (`/home/*/.codex/tmp/`, `/proc`, `/dev`,
  etc.) so the typical session is readable, with a "show everything"
  toggle to disable the filter.
- Ship as `python -m ai_observe.viewer <jsonl>`, which opens an HTTP
  server on a localhost port and launches a browser tab pointing at it.
- No new runtime dependencies beyond Python's stdlib on the server side.
  The browser bundle may use a vendored treemap library (single static
  file) shipped inside the package.

## Non-goals (out of scope for v1)

- Multi-session join, cross-session aggregation, or session diffing.
- Persistence beyond the source `.jsonl` itself (no DB, no sidecar caches
  that survive a restart of the viewer).
- Auth, user accounts, or any form of remote viewing. The server binds to
  loopback only.
- File-content preview, diff view, or any feature that would require data
  the JSONL doesn't carry.
- Editing or annotating events; the viewer is read-only.
- Streaming from anything other than a JSONL file on local disk (no
  attaching to a running wrapper's parser thread, no sockets, no IPC
  with the observer).
- Cross-platform packaging beyond what stdlib supports. Linux is the
  reference platform (matching Spec 1/3).
- Auto-discovery of "the latest session." The user passes the JSONL path.

## User experience

### Typical live invocation

```bash
# Terminal A: Codex session running through the wrapper
codex "implement feature"
# Terminal B: open the visualizer on its in-flight JSONL
python -m ai_observe.viewer .codev/observe/<session>.jsonl
```

`ai_observe.viewer` does the following:

1. Verifies the path exists and is a regular file (creates nothing if the
   path does not yet exist; prints an error and exits non-zero).
2. Binds an HTTP server to `127.0.0.1` on an OS-chosen ephemeral port (or
   a user-supplied `--port`).
3. Prints the URL to stderr.
4. Unless `--no-browser` is passed, attempts `webbrowser.open(url)`. If
   that fails, the printed URL is the fallback.
5. Tails the JSONL: reads everything currently in the file, then keeps the
   file open and polls for appended lines until the user stops the
   server with Ctrl-C (or the process is killed).
6. Streams parsed events to the connected browser over Server-Sent
   Events (SSE). The browser maintains its own aggregation state and
   re-renders incrementally.

In *static* mode (a completed `.jsonl`), the tailer simply reaches EOF
and stays idle; the browser shows the final state. The user can close
the tab whenever they like.

### Page layout

A single page with two linked panels:

- **Left panel — Treemap**: nested rectangles, one rectangle per file,
  grouped by parent directory. Rectangle area is the chosen size metric
  (default: bytes written). Color is by file extension (or a single
  neutral palette if no extension; exact palette is an implementation
  detail of the plan, not pinned here). Hovering a rectangle shows a
  tooltip with absolute path, byte total, event count, and last-seen
  timestamp. Clicking selects the file and highlights it in the right
  panel.
- **Right panel — Indented tree/table**: each row is a path (file or
  directory). Columns: `Path` (indented by depth), `Bytes written`,
  `Events`, `Last touched`. Directories show subtree totals; files show
  their own. Click a row to expand or collapse a directory (for
  directory rows), and to highlight the corresponding rectangle.

  **Sort semantics (precise, so it is testable)**: sorting is
  *sibling-local* — clicking a column header re-orders rows *within
  each parent* by that column, but never lifts a row out of its
  hierarchical position. The tree remains a tree; you do not get a
  flat global sort that would scramble parent/child relationships.
  Sort direction toggles on repeated header clicks (asc/desc). The
  default sort is `Bytes written` descending. Expand/collapse state
  is preserved across sort changes and across metric-toggle changes.
  The currently-selected row is preserved across both as well; if its
  sort position changes, the table scrolls to keep it visible.
- **Top bar**:
  - Metric toggle: `Bytes` | `Events` | `Recent` (recency-weighted).
  - "Show noise" toggle (off by default): when off, paths matching the
    default exclude list are hidden from both panels and excluded from
    aggregation; when on, everything is visible.
  - Live indicator: a small badge that turns green when an SSE event
    has arrived in the last ~2 s, gray otherwise. A counter shows the
    number of events ingested so far.

### Metric definitions

Per file path `p`, with `events(p)` the list of JSONL events touching
that path (matching `path`, `old_path`, or `new_path`):

- **Bytes**: `sum(e.result for e in events(p) if e.operation == "modify"
  and isinstance(e.result, int) and e.result > 0)`. All other events
  (and non-positive / non-integer `result`) contribute zero bytes. This
  matches the observer's contract: `result` is bytes-written for
  `modify` events only.
- **Events**: `len(events(p))`. Every event for `p` counts once,
  regardless of operation.
- **Recent**: a recency-weighted event count using an exponential decay,
  with the cutoff age and weighting computed relative to the most
  recently ingested event's timestamp. The exact decay constant is an
  implementation detail of the plan; it must be a single fixed value
  shared by all paths so the resulting areas are comparable. (We choose
  a recency-weighted *count*, not bytes, because byte-decay would be
  dominated by large recent SQLite/state writes and visually swamp the
  small-but-active source files the user actually cares about.)

Directory totals are the sum of their children for `Bytes` and `Events`,
and the sum for `Recent` as well (decayed counts add).

The viewer also tracks `last_touched` per path (max event timestamp).

### Path filtering

The default exclude list filters paths whose absolute form matches any
of the following patterns (glob-style, evaluated on both `path` and on
`old_path`/`new_path` for rename events; an event is excluded iff *all*
of its non-null paths match the exclude list):

- `/home/*/.codex/**`
- `/home/*/.cache/**`
- `/tmp/**`, `/var/tmp/**`
- `/proc/**`, `/sys/**`, `/dev/**`
- `/run/**`

The "Show noise" toggle in the UI disables this filter. The filter is
applied client-side after events are received (so toggling does not
require reconnecting). The default list is also reflected in the count
of "filtered events" shown next to the live indicator, so the user can
see how many events are hidden.

### Rename handling

A `rename` event in the observer schema carries `old_path`, `new_path`,
**and** `path` — the trace parser sets `path == new_path` for
`rename`/`renameat`/`renameat2` (see `src/ai_observe/trace_parser.py`).
The viewer must therefore not use "`path is None`" as a rename
detector; it must dispatch on `operation == "rename"`.

On a rename event with old `A` and new `B`, the viewer mutates its
per-path aggregation as follows. Let `S(A)` be the accumulated state
for path `A` (bytes, events, recency-decay accumulator, last_touched):

- **Bytes**: `bytes(B) += bytes(A)` (move). `bytes(A) := 0`.
- **Events**: `events(B) += events(A) + 1`. `events(A) := 0`. The
  `+1` charges the rename event itself to the destination, *not* the
  source, so the destination's count reflects the full "this is the
  active path now" reality. The source's prior count is folded in so
  no events are lost from totals if the viewer is later asked about
  the subtree as a whole.
- **Recent**: the destination inherits the source's recency
  accumulator (same exponential decay state), then adds one fresh
  contribution for the rename event itself.
- **last_touched**: `last_touched(B) = max(last_touched(A),
  last_touched(B), rename_event.timestamp)`. The source's
  `last_touched` is no longer relevant because the source path is
  tombstoned (see below).
- **Tombstone**: `A` is marked tombstoned. Tombstoned paths are hidden
  from both panels — even when "Show noise" is on — to avoid showing
  ghost rectangles for paths that no longer exist on disk. (If the
  user later sees fresh events for `A` — e.g., something recreates the
  file — the tombstone is cleared and `A` becomes a fresh entry with
  zero accumulators.)
- **Collision**: if `B` already has accumulated state when the rename
  arrives, the migration is additive — `B`'s prior bytes/events/recent
  remain, and `A`'s are added on top, per the rules above. Collisions
  are expected for atomic-save patterns where the same final path is
  rewritten repeatedly.

This keeps the treemap stable under the dominant atomic-save pattern
(write tmp → rename over target) and avoids both double-counting and
orphan rectangles.

### Aggregation lifecycle

- The tailer must **buffer partial trailing lines**: if a poll reads
  bytes that do not end in `\n`, the trailing fragment is held until
  the next poll that completes it with a newline. Only fully
  newline-terminated lines are parsed as JSON. A fragment that never
  completes (e.g., the producer crashed mid-line) is reported once on
  stderr when the viewer is told to shut down, but is otherwise
  silently held — it is **not** treated as a malformed line and does
  **not** trigger the malformed-line warning. This mirrors the
  contract Spec 3 relies on for its own live parser, which appends
  line-at-a-time after `json.dumps()` + `\n`.
- The tailer must handle **file rotation/deletion** the same way it
  handles truncation: if the path disappears or its inode changes, the
  tailer reopens from offset 0 once the path is readable again,
  emitting a stderr warning. The observer never rotates its `.jsonl`,
  so this is defensive; we mention it because the viewer reads files
  outside its own control.
- Aggregation state lives in the **browser**, not on the server. The
  server is a thin tailer: it parses JSONL lines and forwards minimal
  event records over SSE. This keeps the server stateless across page
  reloads and avoids holding large dicts in Python.
- On page (re)load, the server replays the whole file from the start
  before resuming tail. The browser re-builds aggregation from scratch.
  Replay uses the same SSE channel as live updates.
- The browser may batch DOM updates (e.g., coalesce SSE events for ~250
  ms before re-laying out the treemap) to keep rendering smooth at the
  ~8800-event scale of the reference trace.

## Stakeholders

- **Primary user**: a developer running a Codex session under the
  observer wrapper, who wants to see in real time which files Codex is
  touching most heavily. This is a debugging / situational-awareness
  tool.
- **Secondary user**: the same developer after the session, doing a
  post-hoc review of where work concentrated.
- **Out-of-scope user**: anyone needing remote, multi-user, or
  shared-dashboard access.

## Approach options (considered)

### A. Stateful server (server-side aggregation, polled by browser)

The server maintains the per-path aggregation dict and exposes a JSON
endpoint (or sends snapshots over SSE). The browser is a thin renderer.

**Pros**:
- Smaller client; less work to recompute on reload.
- Could be reused as an API.

**Cons**:
- Server memory grows with unique paths; same problem we'd push to the
  client but at least the client is throwaway.
- Reconnect / multi-tab gets more complex (cache invalidation,
  consistency).
- Pushes more code into Python where iteration is slower than vanilla JS
  for this kind of UI work.

### B. Stateless server, browser-side aggregation (selected)

Server tails the file and forwards per-event records over SSE. Browser
keeps the dict and renders.

**Pros**:
- Server stays trivial; one process, one file handle, a poll loop.
- Multiple browser tabs each get an independent stream.
- All UI state (metric toggle, sort, expanded rows) lives where the UI
  is; no cross-process synchronization needed.

**Cons**:
- On reload, the browser re-receives every line. At 8800 events this is
  cheap; at 1M it could matter (not a v1 concern; see Open Questions).
- The browser must implement the aggregation correctly, including the
  rename-merge edge case.

### C. Static file dump (no live mode)

A one-shot command that reads the JSONL and writes a self-contained
HTML file with the treemap baked in.

**Pros**:
- No long-running server, trivial to share the artifact.

**Cons**:
- Defeats the primary use case ("watch while Codex runs"). The user
  explicitly named live mode as the headline feature.
- Either we ship two tools (live + static) or we lose the headline.
  Approach B already supports static mode for free (open a completed
  JSONL, browser sees full replay, server idles).

**Decision**: Approach B.

## Success criteria

- [ ] `python -m ai_observe.viewer <path-to-jsonl>` starts a server on
  loopback, prints the URL, and opens a browser tab (or honors
  `--no-browser`).
- [ ] With a freshly-created empty JSONL, the page loads, shows an empty
  treemap, and updates within ~1 s of new lines being appended.
- [ ] Rendering the reference sample
  (`.codev/observe/20260513T165110Z-16975-8f23.jsonl`, ~8800 events) end
  to end completes within a few seconds and is interactive (treemap
  zooms, table sorts, toggles respond) at the end.
- [ ] All three metrics (Bytes / Events / Recent) produce non-degenerate
  treemaps on the reference sample (i.e., the toggle visibly
  redistributes rectangle areas).
- [ ] Default exclude list, applied to the reference sample, hides at
  least the `/home/user/.codex/` subtree and leaves a treemap dominated
  by genuinely interesting paths. "Show noise" reveals the hidden
  subtree.
- [ ] Rename events do not produce orphaned ghost rectangles for the
  `old_path` if the rename is to a path the visualizer was already
  tracking; the accumulated bytes migrate to `new_path`.
- [ ] Closing the browser tab does not crash the server; reopening the
  URL re-replays from the start.
- [ ] Sending Ctrl-C to the server exits cleanly (no traceback on the
  signal path; the file handle is closed).
- [ ] Unit tests cover: metric aggregation (each of the three metrics),
  rename-merge behavior, exclude-filter behavior, the JSONL line
  parser's handling of malformed lines, and the SSE framing.
- [ ] Integration test: spawn the server against a fixture JSONL,
  connect a real HTTP client to `/events`, assert the expected event
  records arrive in order; append more lines and assert the new events
  arrive.
- [ ] `docs/observe.md` (or a new `docs/viewer.md`) is updated with the
  invocation, the metric definitions, and the exclude list defaults.
- [ ] The viewer fails gracefully on malformed JSONL lines: the bad line
  is skipped, a warning is logged to the server's stderr, and the
  stream continues. (This matches the observer's own tolerance for the
  `.jsonl.partial` failure mode.)

## Constraints

### Technical

- Linux-first, matching Spec 1/3. macOS / Windows are not required to
  work but should not be deliberately broken (e.g., no `inotify`-only
  code paths; we tail by polling `os.stat` and seeking, same approach
  Spec 3 uses for the live parser).
- Python 3 stdlib only on the server side. No Flask / FastAPI /
  uvicorn / websockets. `http.server` plus a small SSE writer is
  sufficient.
- Browser bundle: prefer a single vendored treemap library file shipped
  inside the Python package. No network fetch from a CDN at runtime
  (the viewer should work offline). No build step that requires Node
  in CI; if a bundler is unavoidable, the built artifact is checked
  in.
- The viewer must not write to or modify the source `.jsonl` under any
  circumstances. It opens it read-only.
- The viewer must not require root or any capability the observer
  itself does not already require.

### Security / privacy

- The JSONL is documented as containing potentially sensitive data
  (paths, command args, raw syscalls). The viewer:
  - Binds to `127.0.0.1` only. There is **no** flag to bind elsewhere.
  - Does not log request bodies or paths to disk.
  - Renders all path strings as text (no `innerHTML`); XSS-safety
    matters because paths can contain arbitrary characters.
  - Does not include `raw_syscall` in the page — the JSONL has it, but
    showing it in tooltips would leak content excerpts. The tooltip
    shows path + counts + timestamp only.
  - Does not put path content, query strings derived from paths, or
    any other JSONL field into the browser tab's title, `document.URL`
    query string, or window history. The title is a fixed string
    (e.g., `ai_observe viewer`); navigation does not change the URL
    based on selection. This prevents sensitive paths from leaking via
    shell-integrated terminals that watch window titles, browser
    history sync, or screen-recording tools that capture titles.

### Compatibility

- The viewer reads `schema_version: 1` (the current observer schema).
  Lines with other `schema_version` values are skipped with a stderr
  warning, in case the schema evolves. We do not promise forward
  compatibility for v1 of the viewer.

## Performance requirements

- **Live latency**: a new line appended to the JSONL should appear in
  the rendered treemap within ~1 s under normal load, mirroring the
  observer's own ~`CODEV_OBSERVE_LIVE_POLL_MS` (default 200 ms) +
  parser cost budget. The viewer's polling interval is bounded the
  same way and defaults to 250 ms; this is not user-tunable in v1.
- **Initial render**: a full replay of the reference 8800-event sample
  must finish initial layout in ≤5 s on a developer laptop.
- **Steady state**: ongoing live updates at modest rates (≤100 events
  per second sustained) must not stall the UI thread for more than
  ~100 ms at a time; the browser-side update loop coalesces.

These are budget targets, not hard SLAs; they exist so the plan can
choose a treemap library and update strategy that doesn't visibly
regress them.

## Consultation log

### Iteration 1 (codex + claude; gemini skipped per project preference)

- **Codex — REQUEST_CHANGES**: rename semantics underdefined across
  all metrics; live tailer must explicitly buffer partial trailing
  lines; reference JSONL fixture is not in the repo; sortable
  tree/table UX model unclear; sensitive paths must not appear in
  title or URL.
- **Claude — REQUEST_CHANGES**: factual error — rename events *do*
  carry `path` (set to `new_path`) per `trace_parser.py`; minor gaps
  on entry-point packaging (`__main__.py`, deferred to plan) and
  file-deletion edge case.

Updates made: rewrote "Rename handling" section to specify per-metric
migration, tombstoning, and collision behavior; added partial-line
buffering and file-rotation rules to "Aggregation lifecycle"; tightened
the right-panel sort semantics to sibling-local sort with preserved
expand/collapse; added an explicit no-title-leak / no-URL-leak rule to
the security section; and called out that the reference JSONL is
git-ignored so the plan must supply a committed synthetic fixture.

## Open questions

### Critical (block progress)
- None. The architect-settled decisions cover the headline behavior.

### Important (affect design)
- **Treemap library choice**. The plan must pick one; criteria are:
  pure JS (no build step), small (<200 KB), supports incremental
  re-layout cheaply, MIT/BSD-equivalent license. Candidates: D3
  `d3-hierarchy.treemap`, `webtreemap`, a hand-rolled squarified
  treemap (~150 LOC). This is a plan-phase decision, not a spec one.
- **Recency decay constant**. The spec fixes "recency-weighted count
  with a single shared decay constant" as the contract. The actual
  half-life (30 s? 5 min?) is a plan-phase tunable; the success
  criterion is just that the metric produces a visibly different
  treemap from `Events`.

### Nice-to-know
- Would a fourth metric — "modify events only, counted" — be useful in
  addition to Bytes/Events/Recent? Probably yes for some workflows, but
  the three settled metrics cover the headline use cases, so deferred.
- Should the table show operation breakdowns (create/modify/delete
  counts per row)? Useful but adds columns and complexity; deferred.

## Test scenarios

### Functional
1. **Empty file, live append**: start viewer on an empty `.jsonl`, append
   3 events from a fixture script, expect three rectangles to appear.
2. **Full replay, static**: point viewer at the reference 8800-event
   sample, expect the treemap to render and the three metric toggles
   to produce distinct layouts.
3. **Rename-merge**: feed a sequence (`modify A`, `modify A`,
   `rename A → B`, `modify B`); expect `B` to carry the accumulated
   bytes and `A` to disappear from the visible tree (its row remains
   only if "Show noise" reveals deleted/renamed-away paths — for v1,
   it's hidden once superseded).
4. **Exclude toggle**: load the reference sample, confirm
   `/home/user/.codex/**` is hidden by default and visible after the
   toggle.
5. **Malformed line**: feed a JSONL with one broken line in the
   middle; expect a stderr warning, no crash, and all surrounding
   events ingested normally.
6. **Schema version mismatch**: feed a line with `schema_version: 2`;
   expect it to be skipped with a warning.
7. **Reload during live**: open the page, append events, reload the
   page; expect the full state to reappear (replay from start).

### Non-functional
1. **Latency**: with the live tailer polling at 250 ms, time from
   append to first paint should be ≤1 s on a quiet machine.
2. **Loopback only**: attempt to connect from a non-loopback interface;
   expect connection refused.
3. **No external network**: run with network disabled; the page must
   load and function from the vendored bundle.

## Dependencies

- **Internal**: Spec 1 (observer + JSONL schema). The viewer is an
  independent reader of that JSONL; it does not link to wrapper code.
  It is *not* coupled to Spec 3's live parser thread — they happen to
  produce/consume the same growing file, which is the contract.
- **External**: a vendored treemap library (TBD in plan). No runtime
  network deps. No new Python deps.

## Risks and mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Treemap library choice locks us into a heavy framework. | Med | Med | Plan picks a small / no-build library; spec allows hand-rolled squarified treemap as a fallback. |
| Rename-merge logic is subtly wrong and creates double-counting. | Med | Med | Explicit unit tests in success criteria; deterministic fixture covering the move pattern. |
| Sensitive paths in tooltips / titles leak through window titles or browser history. | Med | High | Bind loopback only; no `raw_syscall` in DOM; document the privacy boundary in `docs/observe.md`. |
| Live tailer races a rotating / truncated file. | Low | Med | Treat truncation (`os.stat().st_size < last_offset`) as "reopen from 0"; emit a stderr warning. The observer never truncates its `.jsonl`, so this is defensive. |
| Browser-side memory grows unbounded on very long sessions. | Low | Med | Out of scope for v1; document the v1 envelope (~10⁴ events) and defer compression / windowing. |

## References

- `docs/observe.md` — JSONL schema, operations, sensitive-data
  warnings.
- `codev/specs/3-stream-observe-events-in-near-.md` — the live tailing
  approach the wrapper itself uses; the viewer's tailer is structurally
  similar (poll on EOF, reopen on truncation, handle partial last
  line) but reads JSONL instead of strace output.
- Sample trace at `.codev/observe/20260513T165110Z-16975-8f23.jsonl`
  (~8818 events; ~99% under `/home/user/.codex/`, which is why the
  exclude list matters). **This file is git-ignored
  (`.gitignore`: `.codev/observe/`) because real traces may contain
  secrets, so it is not a committed fixture.** The plan must commit a
  small synthetic fixture (a few dozen events, hand-crafted to cover
  create / modify / delete / rename / chmod / metadata and at least
  one rename collision) under `tests/fixtures/` to make success
  criteria reproducible in CI. Performance-flavored criteria (e.g.,
  "render 8800 events in ≤5 s") are measured locally against the
  developer's own real trace, not in CI, and are budget targets — not
  CI assertions.

## Approval

- [ ] Spec approved by architect
- [ ] 3-way consultation complete (codex + claude; gemini skipped per
  project preference)

## Notes

The viewer is deliberately a *reader*, not a peer of the observer: it
reuses no observer code, opens the JSONL read-only, and depends only on
the documented schema. If the observer changes the schema, the viewer
breaks loudly (skipped lines + warning) rather than silently
misrendering. This independence is the main reason it's a separate
spec rather than an extension to Spec 3.
