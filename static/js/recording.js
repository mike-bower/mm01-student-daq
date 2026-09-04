/**
 * recording.js — Alpine store for recording the live stream to a CSV file.
 *
 * Registers Alpine.store("rec"). Kept separate from mm01.js on purpose: that
 * file is a frozen copy from the parent project and local edits to it are
 * overwritten by tools/sync_bridge.sh.
 *
 * The server does the recording — this is only the control panel. One recording
 * exists at a time, for everybody, so we poll /recording/status whether or not
 * this page started it: a recording begun at the bench has to show up on the
 * laptop too, and stop has to work from either. That endpoint reads counters,
 * not the device.
 *
 * Depends on: api.js
 */

"use strict";

document.addEventListener("alpine:init", () => {

  Alpine.store("rec", {

    // ── Status ────────────────────────────────────────────────────────────────
    available:   true,    // false when the server has no recorder at all
    recording:   false,
    session:     null,    // the recording in progress
    finished:    null,    // the one just stopped, so the page can say so
    sessions:    [],      // saved recordings, newest first
    error:       null,
    busy:        false,

    // ── Form ──────────────────────────────────────────────────────────────────
    name:        "",
    note:        "",
    intervalMs:  "50",   // a string: x-model compares against option values
    directory:   "",
    maxSeconds:  3600,

    // The MM01 converts at 80 samples/second, so 12 ms is as fast as recording
    // can usefully go — anything quicker just repeats the same conversion.
    rates: [
      { ms: 200, label: "5/s" },
      { ms: 100, label: "10/s" },
      { ms:  50, label: "20/s" },
      { ms:  25, label: "40/s" },
      { ms:  12, label: "80/s" },
    ],

    _timer: null,
    _timerMs: null,

    // Once a second while recording (the row count is moving), every three
    // when idle (we are only watching for someone else pressing start).
    POLL_RECORDING_MS: 1000,
    POLL_IDLE_MS:      3000,

    // ── Init ──────────────────────────────────────────────────────────────────

    async init() {
      await this.refresh();
    },

    async refresh() {
      try {
        const status = await API.recordingStatus();
        this.available  = true;
        this.directory  = status.directory || "";
        this.maxSeconds = status.max_seconds ?? 3600;
        if (!this.recording && !this.session) {
          const preferred = String(status.default_interval_ms || "");
          if (this.rates.some(r => String(r.ms) === preferred)) this.intervalMs = preferred;
        }
        this._applyStatus(status);
        await this.refreshSessions();
      } catch (e) {
        // 503 means the server could not open its recordings directory. That is
        // a state to render, not an error to shout about.
        if (e.status === 503) {
          this.available = false;
          return;
        }
        this.error = String(e.message || e);
      }
    },

    async refreshSessions() {
      try {
        const data = await API.listRecordings();
        this.sessions = data.sessions || [];
      } catch (e) {
        if (e.status !== 503) this.error = String(e.message || e);
      }
    },

    _applyStatus(status) {
      const wasRecording = this.recording;
      this.recording = !!status.recording;
      this.session   = status.session || null;
      this._startPolling(this.recording ? this.POLL_RECORDING_MS : this.POLL_IDLE_MS);
      // Either transition changes the saved list: a recording that just
      // stopped joins it, and one that just started leaves it.
      if (wasRecording !== this.recording) this.refreshSessions();
    },

    _startPolling(everyMs) {
      if (this._timer && this._timerMs === everyMs) return;
      this._stopPolling();
      this._timerMs = everyMs;
      this._timer = setInterval(async () => {
        try {
          this._applyStatus(await API.recordingStatus());
        } catch (e) {
          // The server went away. Stop polling rather than filling the console;
          // reloading the page picks it up again.
          this._stopPolling();
        }
      }, everyMs);
    },

    _stopPolling() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      this._timerMs = null;
    },

    // ── Commands ──────────────────────────────────────────────────────────────

    async start() {
      this.error = null;
      this.finished = null;
      this.busy = true;
      try {
        this.session = await API.startRecording({
          name: this.name || "",
          note: this.note || "",
          sample_interval_ms: Number(this.intervalMs),
        });
        this.recording = true;
        this._startPolling(this.POLL_RECORDING_MS);
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.busy = false;
      }
    },

    async stop() {
      this.error = null;
      this.busy = true;
      try {
        this.finished = await API.stopRecording();
        this.recording = false;
        this.session = null;
        this._startPolling(this.POLL_IDLE_MS);
        await this.refreshSessions();
      } catch (e) {
        this.error = String(e.message || e);
        await this.refresh();
      } finally {
        this.busy = false;
      }
    },

    async remove(sessionId) {
      this.error = null;
      if (!confirm("Delete this recording? The CSV file is removed from the Pi.")) return;
      try {
        await API.deleteRecording(sessionId);
        if (this.finished?.session_id === sessionId) this.finished = null;
        await this.refreshSessions();
      } catch (e) {
        this.error = String(e.message || e);
      }
    },

    downloadUrl(sessionId) {
      return `/recording/sessions/${encodeURIComponent(sessionId)}/download`;
    },

    // ── Formatting ────────────────────────────────────────────────────────────

    rateLabel(intervalMs) {
      if (!intervalMs) return "";
      const known = this.rates.find(r => r.ms === Number(intervalMs));
      if (known) return known.label;
      const hz = 1000 / intervalMs;
      return `${hz >= 10 ? Math.round(hz) : hz.toFixed(1)}/s`;
    },

    fmtDuration(seconds) {
      const s = Number(seconds) || 0;
      if (s < 60) return `${s.toFixed(1)} s`;
      if (s < 3600) {
        return `${Math.floor(s / 60)}m ${String(Math.floor(s % 60)).padStart(2, "0")}s`;
      }
      const h = Math.floor(s / 3600);
      const m = Math.round((s % 3600) / 60);
      return m ? `${h}h ${m}m` : `${h}h`;
    },

    fmtSize(bytes) {
      const b = Number(bytes) || 0;
      if (b < 1024) return `${b} B`;
      if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} kB`;
      return `${(b / 1024 / 1024).toFixed(1)} MB`;
    },

    fmtStarted(iso) {
      if (!iso) return "";
      const d = new Date(iso);
      return isNaN(d) ? iso : d.toLocaleString();
    },

    // A session the app never got to close — a power cut, or the Pi switched
    // off mid-lab. The rows before that point are still good.
    isInterrupted(session) {
      return session?.stop_reason === "interrupted";
    },
  });

  Alpine.store("rec").init();
});
