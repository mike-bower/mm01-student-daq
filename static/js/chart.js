/**
 * chart.js — the live strip chart, drawn with uPlot.
 *
 * The shared mm01.js store calls Charts.pushMM01(frame) for every WebSocket
 * frame. We keep a rolling window of samples per device and redraw.
 *
 * All devices publish in the same frame, so they share one timestamp array —
 * which is exactly the shape uPlot wants: [xs, ys0, ys1, ...].
 */

"use strict";

const Charts = (() => {

  let windowSeconds = 60;      // how much history to show
  let plot = null;             // the uPlot instance
  let el = null;               // the container element

  const xs = [];               // shared timestamps (seconds)
  const ys = new Map();        // deviceIndex -> array of values, aligned to xs

  // ── Data ────────────────────────────────────────────────────────────────────

  function pushMM01(frame) {
    const readings = frame.readings || {};
    const keys = Object.keys(readings);
    if (!keys.length) return;

    const t = Date.now() / 1000;
    xs.push(t);

    for (const key of keys) {
      const idx = Number(key);
      if (!ys.has(idx)) {
        // A device seen for the first time: back-fill so its array lines up
        // with the timestamps already recorded.
        ys.set(idx, new Array(xs.length - 1).fill(null));
      }
      const v = readings[key];
      ys.get(idx).push(v === null || v === undefined ? null : v);
    }
    // Any device that did not report in this frame gets a gap, keeping every
    // series the same length as xs.
    for (const [idx, arr] of ys) {
      while (arr.length < xs.length) arr.push(null);
    }

    _trim();
    redraw();
  }

  function _trim() {
    const cutoff = xs[xs.length - 1] - windowSeconds;
    let drop = 0;
    while (drop < xs.length && xs[drop] < cutoff) drop++;
    if (drop === 0) return;
    xs.splice(0, drop);
    for (const arr of ys.values()) arr.splice(0, drop);
  }

  function clear() {
    xs.length = 0;
    ys.clear();
    redraw();
  }

  function setWindow(seconds) {
    windowSeconds = seconds;
    _trim();
    redraw();
  }

  function getWindow() { return windowSeconds; }

  // ── Drawing ─────────────────────────────────────────────────────────────────

  function mount(container) {
    el = container;
    _build();
  }

  function _seriesInfo() {
    // Ask the store for each device's label and colour so the chart legend
    // matches the rest of the page.
    const store = (() => { try { return Alpine.store("mm01"); } catch { return null; } })();
    return [...ys.keys()].sort((a, b) => a - b).map(idx => {
      const dev = store?.devices?.find(d => d.device_index === idx);
      return {
        idx,
        label: dev?.label || `MM01 #${idx + 1}`,
        color: dev?.color || "#3fb950",
      };
    });
  }

  function _build() {
    if (!el || typeof uPlot === "undefined") return;
    if (plot) { plot.destroy(); plot = null; }

    const info = _seriesInfo();
    const opts = {
      width:  el.clientWidth || 800,
      height: el.clientHeight || 320,
      scales: { x: { time: true } },
      axes: [
        { stroke: "#8b949e", grid: { stroke: "#30363d" }, ticks: { stroke: "#30363d" } },
        { stroke: "#8b949e", grid: { stroke: "#30363d" }, ticks: { stroke: "#30363d" },
          label: "microstrain (µε)", labelSize: 40 },
      ],
      series: [
        {},
        ...info.map(s => ({
          label: s.label,
          stroke: s.color,
          width: 2,
          spanGaps: false,
        })),
      ],
      legend: { show: true },
    };
    plot = new uPlot(opts, _data(info), el);
  }

  function _data(info) {
    return [xs, ...info.map(s => ys.get(s.idx) || [])];
  }

  function redraw() {
    if (!el) return;
    const info = _seriesInfo();
    // Rebuild when the set of devices changes — uPlot fixes its series at
    // construction time.
    if (!plot || plot.series.length - 1 !== info.length) {
      _build();
      return;
    }
    plot.setData(_data(info));
  }

  function resize() {
    if (plot && el) plot.setSize({ width: el.clientWidth, height: el.clientHeight });
  }

  return { pushMM01, mount, redraw, resize, clear, setWindow, getWindow };
})();

window.Charts = Charts;
window.addEventListener("resize", () => Charts.resize());
