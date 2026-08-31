/**
 * mm01.js — Alpine.js store + WebSocket client for MM01 StudentDAQ / MultiDAQ.
 *
 * Registers Alpine.store("mm01") and an MM01WS WebSocket manager that connects
 * to /mm01/ws and pushes live readings into the store.
 *
 * Each MM01 is single-channel, so a device index is also a channel index — the
 * store has no separate channel list, unlike hid.js. Unified chart IDs are
 * "m:N" where N is the device index.
 *
 * Depends on: api.js (for API.getAuthToken())
 */

"use strict";

// ── MM01 WebSocket ────────────────────────────────────────────────────────────

const MM01WS = (() => {
  let _ws = null;
  let _reconnect = false;
  let _reconnectTimer = null;

  function connect() {
    if (_ws && _ws.readyState <= WebSocket.OPEN) return;
    _reconnect = true;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const jwt   = API.getAuthToken();
    const qs    = jwt ? `?token=${encodeURIComponent(jwt)}` : "";
    _ws = new WebSocket(`${proto}://${location.host}/mm01/ws${qs}`);

    _ws.onopen = () => {
      console.debug("[MM01WS] connected");
      Alpine.store("mm01").wsConnected = true;
    };
    _ws.onclose = () => {
      console.debug("[MM01WS] closed");
      Alpine.store("mm01").wsConnected = false;
      if (_reconnect) _reconnectTimer = setTimeout(connect, 5000);
    };
    _ws.onerror = (e) => console.warn("[MM01WS] error", e);
    _ws.onmessage = (evt) => {
      let frame;
      try { frame = JSON.parse(evt.data); } catch { return; }
      try { Alpine.store("mm01").updateReadings(frame); } catch (e) {
        console.error("[MM01WS.onmessage]", e);
      }
    };
  }

  function disconnect() {
    _reconnect = false;
    clearTimeout(_reconnectTimer);
    if (_ws) { _ws.close(); _ws = null; }
  }

  return { connect, disconnect };
})();

window.MM01WS = MM01WS;

// ── Alpine store ──────────────────────────────────────────────────────────────

document.addEventListener("alpine:init", () => {

  Alpine.store("mm01", {

    // ── Status ────────────────────────────────────────────────────────────────
    enabled:     false,   // true once the server confirms MM01 support is active
    wsConnected: false,
    loading:     false,
    error:       null,

    // ── Device data ───────────────────────────────────────────────────────────
    // One MM01 is one channel: {device_index, serial_number, firmware_version,
    // in_error, active, bridge, bridge_name, gage_factor, zero_offset, label, color}
    devices: [],

    // ── Live readings ─────────────────────────────────────────────────────────
    // { deviceIndex: { value, min, max, mvPerV } }
    readings:    {},
    frameCount:  0,
    lastFrameAt: null,

    // Per-device busy flags so the zero button can show progress.
    busy: {},

    _defaultPalette: [
      "#3fb950", "#388bfd", "#d29922", "#bc8cff",
      "#f85149", "#79c0ff", "#ff7b72", "#56d364",
    ],

    // ── Init ──────────────────────────────────────────────────────────────────

    async init() {
      this.loading = true;
      try {
        await this._fetchDevices();
        if (this.enabled) {
          this._loadDeviceColors();
          MM01WS.connect();
        }
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.loading = false;
      }
    },

    async _fetchDevices() {
      try {
        const data = await API.listMM01Devices();
        this.enabled = true;
        this.devices = (data.devices || []).map(d => ({ ...d, color: null }));
      } catch (e) {
        // 503 means the bridge is not running — MM01_ENABLED unset, or no device.
        // That is a state to render, not an error to shout about. Test the status
        // code, not the message: api.js throws the server's `detail` text as the
        // message, so it never contains the literal "503".
        if (e.status === 503 || String(e.message || e).includes("503")) {
          this.enabled = false;
          this.devices = [];
          return;
        }
        throw e;
      }
    },

    async refresh() {
      this.loading = true;
      this.error = null;
      try {
        await this._fetchDevices();
        this._loadDeviceColors();
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.loading = false;
      }
    },

    async rescan() {
      this.loading = true;
      this.error = null;
      try {
        const data = await API.scanMM01Devices();
        this.enabled = true;
        this.devices = (data.devices || []).map(d => ({ ...d, color: null }));
        this._loadDeviceColors();
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this.loading = false;
      }
    },

    // ── Colors (localStorage-backed, matching the HID store) ──────────────────

    _colorKey: "mm01DeviceColors",

    _loadDeviceColors() {
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem(this._colorKey) || "{}"); } catch {}
      this.devices = this.devices.map((d, i) => ({
        ...d,
        color: saved[d.device_index] || this._defaultPalette[i % this._defaultPalette.length],
      }));
    },

    saveDeviceColor(deviceIndex, color) {
      let saved = {};
      try { saved = JSON.parse(localStorage.getItem(this._colorKey) || "{}"); } catch {}
      saved[deviceIndex] = color;
      try { localStorage.setItem(this._colorKey, JSON.stringify(saved)); } catch {}
      // Replace the object reference so Alpine sees the nested change.
      this.devices = this.devices.map(d =>
        d.device_index === deviceIndex ? { ...d, color } : d
      );
    },

    deviceColor(deviceIndex) {
      const d = this.devices.find(x => x.device_index === deviceIndex);
      return d?.color || "#8b949e";
    },

    // ── Live data ─────────────────────────────────────────────────────────────

    updateReadings(frame) {
      const entries = Object.entries(frame.readings || {});
      if (!entries.length) return;
      const mv = frame.mv_per_v || {};
      const r = { ...this.readings };
      for (const [key, val] of entries) {
        if (val === null || val === undefined) continue;
        const idx = parseInt(key);
        const cur = r[idx];
        if (!cur) {
          r[idx] = { value: val, min: val, max: val, mvPerV: mv[key] ?? null };
        } else {
          cur.value  = val;
          cur.mvPerV = mv[key] ?? cur.mvPerV;
          if (val < cur.min) cur.min = val;
          if (val > cur.max) cur.max = val;
        }
      }
      this.readings    = r;
      this.frameCount++;
      this.lastFrameAt = Date.now();
      // Feed samples into the Charts ring buffers for time-series chart types.
      if (window.Charts?.pushMM01) {
        try { Charts.pushMM01(frame); } catch {}
      }
    },

    resetMinMax() {
      const r = { ...this.readings };
      for (const idx of Object.keys(r)) {
        r[idx] = { ...r[idx], min: r[idx].value, max: r[idx].value };
      }
      this.readings = r;
    },

    reading(deviceIndex) {
      return this.readings[deviceIndex] || null;
    },

    // ── Commands ──────────────────────────────────────────────────────────────

    _setBusy(deviceIndex, value) {
      this.busy = { ...this.busy, [deviceIndex]: value };
    },

    // Always return a real boolean. Binding a bare `busy[i]` to :disabled sends
    // `undefined` for a device that has never been zeroed, and Alpine sets the
    // boolean attribute anyway — which left the Zero button permanently disabled.
    isBusy(deviceIndex) {
      return this.busy[deviceIndex] === true;
    },

    _patchDevice(deviceIndex, patch) {
      this.devices = this.devices.map(d =>
        d.device_index === deviceIndex ? { ...d, ...patch } : d
      );
    },

    async setBridge(deviceIndex, bridge) {
      this.error = null;
      const b = Number(bridge);
      try {
        await API.setMM01Bridge(deviceIndex, b);
        this._patchDevice(deviceIndex, {
          bridge: b,
          bridge_name: ["QB", "HB", "FB"][b] || "",
        });
      } catch (e) {
        this.error = String(e.message || e);
      }
    },

    async setGageFactor(deviceIndex, gageFactor) {
      this.error = null;
      const gf = parseFloat(gageFactor);
      if (!isFinite(gf) || gf <= 0) {
        this.error = "Gage factor must be greater than zero";
        return;
      }
      try {
        await API.setMM01GageFactor(deviceIndex, gf);
        this._patchDevice(deviceIndex, { gage_factor: gf });
      } catch (e) {
        this.error = String(e.message || e);
      }
    },

    async zero(deviceIndex) {
      this.error = null;
      this._setBusy(deviceIndex, true);
      try {
        const res = await API.zeroMM01Device(deviceIndex);
        this._patchDevice(deviceIndex, { zero_offset: res.zero_offset });
        this.resetMinMax();
      } catch (e) {
        this.error = String(e.message || e);
      } finally {
        this._setBusy(deviceIndex, false);
      }
    },

    async clearZero(deviceIndex) {
      this.error = null;
      try {
        await API.clearMM01Zero(deviceIndex);
        this._patchDevice(deviceIndex, { zero_offset: 0 });
      } catch (e) {
        this.error = String(e.message || e);
      }
    },

    async zeroAll() {
      for (const d of this.devices) {
        await this.zero(d.device_index);
      }
    },

    async setLabel(deviceIndex, label) {
      this.error = null;
      try {
        await API.setMM01Label(deviceIndex, label || "");
        this._patchDevice(deviceIndex, { label: label || "" });
      } catch (e) {
        this.error = String(e.message || e);
      }
    },
  });

  // Boot the MM01 store once Alpine is running
  Alpine.store("mm01").init();
});
