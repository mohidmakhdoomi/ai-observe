// Browser-side aggregator for ai_observe viewer.
//
// Canonical implementation. The Python mirror in tests/_aggregator_oracle.py
// is the test oracle; semantics must stay in lock-step. See the spec's
// "Metric definitions" and "Rename handling" sections, and the plan's
// Phase 2 block, for the contracts implemented here.

"use strict";

(function (root) {
  const RECENCY_HALF_LIFE_MS = 60000;

  const NOISE_PATTERNS = [
    /^\/home\/[^/]+\/\.codex(\/|$)/,
    /^\/home\/[^/]+\/\.cache(\/|$)/,
    /^\/tmp(\/|$)/,
    /^\/var\/tmp(\/|$)/,
    /^\/proc(\/|$)/,
    /^\/sys(\/|$)/,
    /^\/dev(\/|$)/,
    /^\/run(\/|$)/,
  ];

  function isNoise(path) {
    if (!path) return false;
    for (const rx of NOISE_PATTERNS) {
      if (rx.test(path)) return true;
    }
    return false;
  }

  function eventIsNoise(event) {
    const paths = [event.path, event.old_path, event.new_path].filter(
      (p) => p != null && p !== ""
    );
    if (paths.length === 0) return false;
    return paths.every(isNoise);
  }

  function parseTsMs(ts) {
    // ISO 8601 with trailing Z. Date.parse handles it; multiply by 1.
    const ms = Date.parse(ts);
    return Number.isNaN(ms) ? 0 : ms;
  }

  function decay(accValue, accAtMs, nowMs) {
    if (accValue === 0) return 0;
    const dt = Math.max(0, nowMs - accAtMs);
    return accValue * Math.pow(2, -dt / RECENCY_HALF_LIFE_MS);
  }

  function newEntry() {
    return {
      bytes: 0,
      events: 0,
      recAcc: 0,
      recAtMs: 0,
      lastTouchedMs: 0,
      tombstoned: false,
      opCounts: {},
    };
  }

  function bumpEvent(entry, op) {
    entry.events += 1;
    entry.opCounts[op] = (entry.opCounts[op] || 0) + 1;
  }

  function addRecencyAt(entry, whenMs, weight) {
    const cur = entry.recAtMs ? decay(entry.recAcc, entry.recAtMs, whenMs) : 0;
    entry.recAcc = cur + weight;
    entry.recAtMs = whenMs;
  }

  function updateLastTouched(entry, whenMs) {
    if (whenMs > entry.lastTouchedMs) entry.lastTouchedMs = whenMs;
  }

  function createAggregator() {
    const state = {
      paths: new Map(),
      filteredEventCount: 0,
      totalEventCount: 0,
      latestTsMs: 0,
    };

    function entryFor(path) {
      let e = state.paths.get(path);
      if (!e) {
        e = newEntry();
        state.paths.set(path, e);
      }
      return e;
    }

    function applyRename(event, tsMs) {
      const oldP = event.old_path;
      const newP = event.new_path;
      if (!oldP && !newP) return;
      if (oldP && newP && oldP !== newP) {
        const src = state.paths.get(oldP);
        const dst = entryFor(newP);
        if (dst.tombstoned) dst.tombstoned = false;
        if (src) {
          dst.bytes += src.bytes;
          dst.events += src.events + 1;
          for (const k of Object.keys(src.opCounts)) {
            dst.opCounts[k] = (dst.opCounts[k] || 0) + src.opCounts[k];
          }
          dst.opCounts["rename"] = (dst.opCounts["rename"] || 0) + 1;
          const srcAtTs = src.recAtMs ? decay(src.recAcc, src.recAtMs, tsMs) : 0;
          const dstAtTs = dst.recAtMs ? decay(dst.recAcc, dst.recAtMs, tsMs) : 0;
          dst.recAcc = srcAtTs + dstAtTs + 1;
          dst.recAtMs = tsMs;
          dst.lastTouchedMs = Math.max(dst.lastTouchedMs, src.lastTouchedMs, tsMs);
          src.tombstoned = true;
          src.bytes = 0;
          src.events = 0;
          src.recAcc = 0;
          src.recAtMs = 0;
          src.opCounts = {};
        } else {
          dst.events += 1;
          dst.opCounts["rename"] = (dst.opCounts["rename"] || 0) + 1;
          addRecencyAt(dst, tsMs, 1);
          updateLastTouched(dst, tsMs);
        }
      } else {
        // Partial rename resolution is possible when strace reports one side
        // relative to an unresolved directory fd. If we only know one side,
        // retain the event on that known path instead of dropping it.
        const knownP = newP || oldP;
        const entry = entryFor(knownP);
        bumpEvent(entry, "rename");
        updateLastTouched(entry, tsMs);
        addRecencyAt(entry, tsMs, 1);
      }
    }

    function ingest(event) {
      state.totalEventCount += 1;
      const tsMs = parseTsMs(event.timestamp);
      if (tsMs > state.latestTsMs) state.latestTsMs = tsMs;

      if (eventIsNoise(event)) {
        state.filteredEventCount += 1;
      }

      const op = event.operation;
      if (op === "rename") {
        applyRename(event, tsMs);
        return;
      }

      const path = event.path;
      if (!path) return;
      const entry = entryFor(path);
      if (entry.tombstoned) {
        // Resurrect: a fresh non-rename event for a tombstoned path
        // resets accumulators (a new file at that location).
        entry.tombstoned = false;
        entry.bytes = 0;
        entry.events = 0;
        entry.recAcc = 0;
        entry.recAtMs = 0;
        entry.lastTouchedMs = 0;
        entry.opCounts = {};
      }
      bumpEvent(entry, op);
      updateLastTouched(entry, tsMs);
      addRecencyAt(entry, tsMs, 1);
      if (op === "modify") {
        const result = event.result;
        if (typeof result === "number" && Number.isInteger(result) && result > 0) {
          entry.bytes += result;
        }
      }
    }

    function snapshot(opts) {
      const metric = (opts && opts.metric) || "bytes";
      const includeNoise = !!(opts && opts.include_noise);
      const nowMs = state.latestTsMs || 0;
      const rootChildren = new Map();
      const rootFiles = [];

      for (const [path, entry] of state.paths.entries()) {
        if (entry.tombstoned) continue;
        if (!includeNoise && isNoise(path)) continue;
        if (!path.startsWith("/")) continue;
        const parts = path.split("/").filter((p) => p !== "");
        let cur = { children: rootChildren, files: rootFiles, full: "" };
        for (let i = 0; i < parts.length; i++) {
          const part = parts[i];
          const isLast = i === parts.length - 1;
          if (isLast) {
            cur.files.push({ name: part, path: path, entry: entry });
          } else {
            cur.full = cur.full + "/" + part;
            let child = cur.children.get(part);
            if (!child) {
              child = {
                children: new Map(),
                files: [],
                full: cur.full,
                name: part,
              };
              cur.children.set(part, child);
            }
            cur = { children: child.children, files: child.files, full: child.full, name: child.name };
          }
        }
      }

      function finalize(name, fullPath, childrenMap, filesList) {
        const kids = [];
        for (const f of filesList) {
          const e = f.entry;
          const recent = e.recAtMs ? decay(e.recAcc, e.recAtMs, nowMs) : 0;
          kids.push({
            path: f.path,
            name: f.name,
            isDir: false,
            bytes: e.bytes,
            events: e.events,
            recent: recent,
            last_touched_ms: e.lastTouchedMs,
            children: [],
          });
        }
        for (const [cname, child] of childrenMap.entries()) {
          kids.push(finalize(cname, fullPath + "/" + cname, child.children, child.files));
        }
        kids.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
        let bytesSum = 0,
          eventsSum = 0,
          recentSum = 0,
          lastTouched = 0;
        for (const k of kids) {
          bytesSum += k.bytes;
          eventsSum += k.events;
          recentSum += k.recent;
          if (k.last_touched_ms > lastTouched) lastTouched = k.last_touched_ms;
        }
        return {
          path: fullPath || "/",
          name: name,
          isDir: true,
          bytes: bytesSum,
          events: eventsSum,
          recent: recentSum,
          last_touched_ms: lastTouched,
          children: kids,
        };
      }

      const tree = finalize("/", "", rootChildren, rootFiles);
      return {
        metric: metric,
        include_noise: includeNoise,
        tree: tree,
        filtered_event_count: state.filteredEventCount,
        total_event_count: state.totalEventCount,
        latest_ts_ms: state.latestTsMs,
      };
    }

    function reset() {
      state.paths.clear();
      state.filteredEventCount = 0;
      state.totalEventCount = 0;
      state.latestTsMs = 0;
    }

    return { ingest: ingest, snapshot: snapshot, reset: reset };
  }

  const api = { createAggregator: createAggregator, isNoise: isNoise, eventIsNoise: eventIsNoise };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.AiObserveAggregator = api;
  }
})(typeof self !== "undefined" ? self : this);
