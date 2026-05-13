// Phase 2 bootstrap: connects EventSource, pipes events into the aggregator,
// renders a top-N <pre> dump for human sanity-check. Phase 3 will replace the
// <pre> with treemap + table.

"use strict";

(function () {
  const agg = AiObserveAggregator.createAggregator();
  // Expose for tests / debugging; spec allows in-page test hooks since the
  // server binds loopback-only and never leaves the user's machine.
  window.viewer = { agg: agg };

  const statusEl = document.getElementById("status");
  const logEl = document.getElementById("log");

  let pendingRender = false;
  function scheduleRender() {
    if (pendingRender) return;
    pendingRender = true;
    setTimeout(function () {
      pendingRender = false;
      const snap = agg.snapshot({ metric: "bytes", include_noise: false });
      const flat = [];
      function walk(node) {
        if (!node.isDir) flat.push(node);
        for (const c of node.children) walk(c);
      }
      walk(snap.tree);
      flat.sort(function (a, b) {
        return b.bytes - a.bytes;
      });
      const top = flat.slice(0, 20);
      const lines = top.map(function (n) {
        return (
          n.path +
          "\t" +
          n.bytes +
          "\tev=" +
          n.events +
          "\trec=" +
          n.recent.toFixed(2)
        );
      });
      logEl.textContent = lines.join("\n");
      const total = snap.total_event_count;
      const filtered = snap.filtered_event_count;
      statusEl.textContent =
        "live (" + total + " events, " + filtered + " filtered)";
    }, 250);
  }

  const es = new EventSource("/events");
  es.addEventListener("append", function (ev) {
    try {
      const data = JSON.parse(ev.data);
      agg.ingest(data);
      scheduleRender();
    } catch (err) {
      // Ignore malformed SSE payloads; server is the source of truth.
    }
  });
  es.addEventListener("shutdown", function () {
    statusEl.textContent = "server shutdown";
    es.close();
  });
  es.onerror = function () {
    statusEl.textContent = "disconnected";
  };
})();
