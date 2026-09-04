/**
 * api.js — fetch wrappers for the MM01 REST API.
 *
 * Every function returns the parsed JSON body, or throws an Error carrying
 * `.status` (the HTTP status) and `.detail` (the server's explanation).
 *
 * This kit has no authentication, so getAuthToken() always returns null. It
 * exists because the shared mm01.js store calls it when opening the WebSocket.
 */

"use strict";

const API = (() => {

  async function _req(method, path, body) {
    const init = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) init.body = JSON.stringify(body);

    const resp = await fetch(path, init);
    const text = await resp.text();
    let json;
    try { json = JSON.parse(text); } catch { json = { detail: text }; }

    if (!resp.ok) {
      const err = new Error(json.detail || `HTTP ${resp.status}`);
      err.status = resp.status;
      err.detail = json.detail;
      throw err;
    }
    return json;
  }

  const get  = (path)       => _req("GET",  path);
  const post = (path, body) => _req("POST", path, body);

  return {
    // No auth in this kit — the shared store still asks for a token.
    getAuthToken: () => null,

    listMM01Devices:   ()                  => get("/mm01/devices"),
    scanMM01Devices:   ()                  => post("/mm01/scan"),
    getMM01Device:     (dev)               => get(`/mm01/devices/${dev}`),
    getMM01Readings:   ()                  => get("/mm01/readings"),
    getMM01Reading:    (dev)               => get(`/mm01/devices/${dev}/reading`),
    setMM01Bridge:     (dev, bridge)       => post(`/mm01/devices/${dev}/bridge`, { bridge }),
    setMM01GageFactor: (dev, gage_factor)  => post(`/mm01/devices/${dev}/gage-factor`, { gage_factor }),
    zeroMM01Device:    (dev)               => post(`/mm01/devices/${dev}/zero`),
    clearMM01Zero:     (dev)               => _req("DELETE", `/mm01/devices/${dev}/zero`),
    setMM01Label:      (dev, label)        => post(`/mm01/devices/${dev}/label`, { label }),

    // Recording the live stream to a CSV file on the Pi. The download itself is
    // a plain link (see recording.js downloadUrl), not a fetch.
    recordingStatus:   ()      => get("/recording/status"),
    startRecording:    (body)  => post("/recording/start", body),
    stopRecording:     ()      => post("/recording/stop"),
    listRecordings:    ()      => get("/recording/sessions"),
    getRecording:      (id)    => get(`/recording/sessions/${encodeURIComponent(id)}`),
    deleteRecording:   (id)    => _req("DELETE", `/recording/sessions/${encodeURIComponent(id)}`),
  };
})();

window.API = API;
