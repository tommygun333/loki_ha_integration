/* Reusable Home Assistant dashboard controls for the Loki Monitor explorer.
 * No periodic queries. The existing viewer is imported only on user action.
 */
const VIEWER_MODULE = "/loki_monitor/loki-log-viewer-card.js";
const ALLOWED_SEVERITIES = new Set(["all", "errors", "warnings", "normal", "emergency", "alert", "critical", "fatal", "error", "warning", "notice", "info", "debug", "trace", "unknown"]);
const ALLOWED_MINUTES = new Set([15, 60, 360, 1440, 10080]);
let lastHass = null;
let activeDialog = null;
const MAX_EXACT_MS = 7 * 86400 * 1000;
function localDateTime(value) {
  const d = new Date(value);
  if (!Number.isFinite(d.getTime())) return "";
  const n = x => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${n(d.getMonth()+1)}-${n(d.getDate())}T${n(d.getHours())}:${n(d.getMinutes())}:${n(d.getSeconds())}`;
}
function exactRange(start, end) {
  const a = new Date(start).getTime(), b = new Date(end).getTime();
  if (!start || !end || !Number.isFinite(a) || !Number.isFinite(b) ||
      b-a < 60000 || b-a > MAX_EXACT_MS || b > Date.now()+60000 ||
      a % 1000 !== 0 || b % 1000 !== 0) {
    throw new Error("Choose a valid 1-minute to 7-day interval ending no later than now (seconds precision)");
  }
  return {range_start_ns:String(BigInt(a)*1000000n),range_end_ns:String(BigInt(b)*1000000n)};
}


function cleanConfig(config = {}) {
  const host = Array.isArray(config.host) ? config.host.filter(x => typeof x === "string" && x.trim()) : (config.host ? [String(config.host)] : []);
  return {
    host: [...new Set(host)].slice(0, 24),
    source: config.source || "",
    service: config.service || "",
    program: config.program || "",
    severity: ALLOWED_SEVERITIES.has(config.severity) ? config.severity : "errors",
    minutes: ALLOWED_MINUTES.has(Number(config.minutes)) ? Number(config.minutes) : 60,
    entry_id: config.entry_id || "",
    page_size: [25, 50, 100].includes(Number(config.page_size)) ? Number(config.page_size) : 50,
    text: config.text || "",
    regex: config.regex || "",
    label_map: config.label_map || {},
    ...(config.range_start_ns && config.range_end_ns ? {
      range_start_ns:String(config.range_start_ns), range_end_ns:String(config.range_end_ns)
    } : config.start && config.end ? exactRange(config.start, config.end) : {}),
  };
}

async function openLogPopup(hass, config) {
  if (!hass) throw new Error("Home Assistant connection not available");
  // No Loki activity happens until this function is invoked by the user.
  if (!customElements.get("loki-log-viewer-card")) await import(VIEWER_MODULE);
  if (activeDialog) activeDialog.close();
  const dialog = document.createElement("dialog");
  dialog.setAttribute("aria-label", "Log explorer");
  dialog.style.cssText = `padding:0;border:0;border-radius:14px;max-width:min(1080px,96vw);width:96vw;
    max-height:94dvh;overflow:auto;background:var(--card-background-color,var(--primary-background-color));
    color:var(--primary-text-color);box-shadow:0 18px 55px #0008;`;
  const close = document.createElement("button");
  close.type = "button";
  close.textContent = "Close explorer ✕";
  close.setAttribute("aria-label", "Close log explorer");
  close.style.cssText = `position:sticky;z-index:10;top:0;float:right;margin:8px;cursor:pointer;
    background:var(--primary-color);color:var(--text-primary-color,#fff);border:0;border-radius:8px;padding:10px;`;
  close.addEventListener("click", () => dialog.close());
  const viewer = document.createElement("loki-log-viewer-card");
  viewer.setConfig({type:"custom:loki-log-viewer-card", ...cleanConfig(config)});
  dialog.append(close, viewer);
  dialog.addEventListener("click", ev => { if (ev.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => { viewer.remove(); dialog.remove(); if (activeDialog === dialog) activeDialog = null; });
  document.body.append(dialog);
  activeDialog = dialog;
  dialog.showModal();
  viewer.hass = hass;
}

class LokiLogButtonCard extends HTMLElement {
  static getStubConfig() { return {type:"custom:loki-log-button-card", name:"View error logs", severity:"errors", minutes:60}; }
  setConfig(config) {
    if (!config || config.type !== "custom:loki-log-button-card") throw Error("Expected custom:loki-log-button-card");
    this.config = {...config};
    if (!this.shadowRoot) this.attachShadow({mode:"open"});
    this.shadowRoot.innerHTML = `<style>
      :host{display:block} button{width:100%;min-height:54px;cursor:pointer;text-align:left;display:flex;
      align-items:center;justify-content:space-between;gap:8px;border-radius:var(--ha-card-border-radius,12px);
      background:var(--ha-card-background,var(--card-background-color));box-shadow:var(--ha-card-box-shadow);
      border:1px solid var(--divider-color);color:var(--primary-text-color);padding:12px 16px;font:inherit}
      button:focus-visible{outline:2px solid var(--primary-color)}.count{font-weight:bold;font-variant-numeric:tabular-nums}
      </style><button type="button"><span class="name"></span><span class="count"></span><span aria-hidden="true">↗</span></button>`;
    this.shadowRoot.querySelector(".name").textContent = config.name || "View error logs";
    this.shadowRoot.querySelector("button").addEventListener("click", () => {
      openLogPopup(this._hass, this.config).catch(err => console.error("Loki Monitor popup:", err.message));
    });
    this._renderCount();
  }
  set hass(hass) { this._hass = hass; lastHass = hass; this._renderCount(); }
  _renderCount() {
    if (!this.shadowRoot || !this.config) return;
    const state = this.config.entity && this._hass?.states?.[this.config.entity];
    const el = this.shadowRoot.querySelector(".count");
    if (el) el.textContent = state ? String(state.state) : "";
  }
  getCardSize() { return 1; }
}

class LokiMonitorOverviewCard extends HTMLElement {
  static getStubConfig() {
    return {type: "custom:loki-monitor-overview-card", minutes: 60};
  }

  setConfig(config) {
    if (!config || config.type !== "custom:loki-monitor-overview-card") {
      throw new Error("Expected custom:loki-monitor-overview-card");
    }
    this.config = {...config};
    this._minutes = String(config.minutes ?? 60);
    if (!["15", "30", "60", "180", "360", "720", "1440", "2880", "4320", "10080", "custom"].includes(this._minutes)) {
      this._minutes = "60";
    }
    this._exactData = null;
    this._appliedExact = null;
    this._exactRequestId = (this._exactRequestId || 0) + 1;
    this._shellReady = false;
    this._rowSignature = null;
    if (!this.shadowRoot) this.attachShadow({mode: "open"});
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    lastHass = hass;
    if (!this.config) return;
    if (!this._shellReady) this._render();
    else this._renderRows();
  }

  getCardSize() { return 5; }

  _getEntries() {
    if (!this._hass?.states) return [];
    const entries = Object.values(this._hass.states).filter(s =>
      s.entity_id.startsWith("sensor.") &&
      ["fleet", "host", "service"].includes(s.attributes?.monitor_type) &&
      (!this.config.entry_id || s.attributes.loki_entry_id === this.config.entry_id));
    entries.sort((a, b) => a.attributes.monitor_type === "fleet" ? -1 :
      b.attributes.monitor_type === "fleet" ? 1 :
      (a.attributes.host || "").localeCompare(b.attributes.host || "") ||
      (a.attributes.service_name || "").localeCompare(b.attributes.service_name || ""));
    return entries;
  }

  _isCustom() { return this._minutes === "custom"; }
  _isBuiltin() { return this._minutes === "60" || this._minutes === "1440"; }

  _render() {
    if (!this.config || !this._hass || !this.shadowRoot) return;
    // Only create controls when the card is configured or mounted. Rebuilding
    // innerHTML on every hass update closes the dropdown and date pickers.
    this.shadowRoot.innerHTML = `<style>
      :host{display:block}ha-card{padding:14px}h3{margin:0;font-size:16px}
      .toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}
      table{width:100%;border-collapse:collapse;table-layout:fixed}
      th,td{padding:9px 5px;text-align:left;border-bottom:1px solid var(--divider-color)}
      th:first-child,td:first-child{overflow-wrap:anywhere}
      th:nth-child(2),td:nth-child(2){width:3.8rem;text-align:right}
      th:nth-child(3),td:nth-child(3){width:5.25rem;text-align:right}
      th:nth-child(4),td:nth-child(4){width:4.7rem;text-align:center}
      select{min-height:36px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:7px;padding:4px}
      th{font-size:12px;color:var(--secondary-text-color)}td.numeric{text-align:right;font-variant-numeric:tabular-nums}
      button{cursor:pointer;border:1px solid var(--divider-color);border-radius:7px;background:transparent;
        color:var(--primary-text-color);padding:7px 10px;min-height:36px}
      button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--primary-color)}
      .exact-controls{margin:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:end}
      .exact-controls[hidden]{display:none}
      .exact-controls label{font-size:12px;color:var(--secondary-text-color)}
      .exact-controls input{box-sizing:border-box;min-height:36px;display:block;max-width:100%;font:inherit;
        color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:7px;padding:5px}
      .status{font-size:12px;color:var(--secondary-text-color)}
      </style><ha-card>
      <div class="toolbar"><h3>Homelab Logs</h3>
      <select id="window" aria-label="Count period">
        <option value="15">15 minutes</option><option value="30">30 minutes</option>
        <option value="60">1 hour</option><option value="180">3 hours</option>
        <option value="360">6 hours</option><option value="720">12 hours</option>
        <option value="1440">24 hours</option><option value="2880">2 days</option>
        <option value="4320">3 days</option><option value="10080">7 days</option>
        <option value="custom">Custom range</option>
      </select></div>
      <div id="exact-controls" class="exact-controls" hidden>
        <label>From (local)<input id="exact-from" type="datetime-local" step="1"></label>
        <label>To (local)<input id="exact-to" type="datetime-local" step="1"></label>
        <button id="apply-exact" type="button">Load counts</button>
      </div>
      <div class="toolbar"><button id="full-explorer" type="button">Open full log explorer</button>
        <button id="refresh-preset" type="button" hidden>Refresh counts</button>
        <span id="exact-message" class="status" role="status" aria-live="polite"></span></div>
      <table><thead><tr><th>Host / service</th><th>High</th><th>Warnings</th><th>Logs</th></tr></thead><tbody></tbody></table>
      </ha-card>`;

    const select = this.shadowRoot.querySelector("#window");
    select.value = this._minutes;
    select.addEventListener("change", () => this._selectWindow(select.value));
    const from = this.shadowRoot.querySelector("#exact-from");
    const to = this.shadowRoot.querySelector("#exact-to");
    from.value = this._rangeFrom || localDateTime(Date.now() - 3600000);
    to.value = this._rangeTo || localDateTime(Date.now());
    const changedDate = () => {
      this._rangeFrom = from.value;
      this._rangeTo = to.value;
      this._invalidateHistorical();
      this._renderRows(true);
      this._setRangeMessage("Choose Load counts to update this interval");
    };
    from.addEventListener("change", changedDate);
    to.addEventListener("change", changedDate);
    this.shadowRoot.querySelector("#apply-exact").addEventListener("click", () => {
      try { this._fetchHistorical(exactRange(from.value, to.value)); }
      catch (e) { this._setRangeMessage(e.message); }
    });
    this.shadowRoot.querySelector("#refresh-preset").addEventListener("click", () => {
      this._invalidateHistorical();
      this._renderRows(true);
      this._fetchPreset();
    });
    this.shadowRoot.querySelector("#full-explorer").addEventListener("click", () => {
      try {
        openLogPopup(this._hass, {
          entry_id: this.config.entry_id || "", severity: "all", minutes: this._displayMinutes(),
          ...this._currentRangeParams(),
        }).catch(e => this._setRangeMessage(e.message));
      } catch (e) { this._setRangeMessage(e.message); }
    });
    this._shellReady = true;
    this._syncControls();
    this._renderRows(true);
    if (!this._isCustom() && !this._isBuiltin()) this._fetchPreset();
  }

  _selectWindow(value) {
    this._minutes = value;
    this._invalidateHistorical();
    this._syncControls();
    this._renderRows(true);
    if (!this._isCustom() && !this._isBuiltin()) this._fetchPreset();
  }

  _invalidateHistorical() {
    this._exactRequestId = (this._exactRequestId || 0) + 1;
    this._exactData = null;
    this._appliedExact = null;
  }

  _syncControls() {
    const custom = this._isCustom();
    this.shadowRoot.querySelector("#exact-controls").hidden = !custom;
    this.shadowRoot.querySelector("#refresh-preset").hidden = custom || this._isBuiltin();
    this.shadowRoot.querySelector("#window").value = this._minutes;
    this._setRangeMessage(custom ? "Select dates and load counts" : "");
  }

  _displayMinutes() { return this._isCustom() ? 60 : Number(this._minutes); }

  _currentRangeParams() {
    if (this._isBuiltin()) return {};
    if (this._appliedExact) return {...this._appliedExact};
    if (this._isCustom()) {
      return exactRange(
        this.shadowRoot.querySelector("#exact-from").value,
        this.shadowRoot.querySelector("#exact-to").value,
      );
    }
    const end = Math.floor(Date.now() / 1000) * 1000;
    const start = end - Number(this._minutes) * 60000;
    // Epoch arithmetic avoids DST fall-back ambiguity in local datetime text.
    return {range_start_ns: String(BigInt(start) * 1000000n),
      range_end_ns: String(BigInt(end) * 1000000n)};
  }

  _fetchPreset() {
    try { this._fetchHistorical(this._currentRangeParams()); }
    catch (e) { this._setRangeMessage(e.message); }
  }

  _setRangeMessage(message) {
    const node = this.shadowRoot?.querySelector("#exact-message");
    if (node) node.textContent = message;
  }

  async _fetchHistorical(params) {
    const requestId = this._exactRequestId = (this._exactRequestId || 0) + 1;
    if (!params || !params.range_start_ns || !params.range_end_ns) {
      this._setRangeMessage("Choose a valid range");
      return;
    }
    this._setRangeMessage("Loading Prometheus counts…");
    try {
      const data = await this._hass.connection.sendMessagePromise({
        type: "loki_monitor/range_counts", entry_id: this.config.entry_id || "", ...params,
      });
      if (requestId !== this._exactRequestId) return;
      this._exactData = data;
      this._appliedExact = params;
      this._renderRows(true);
      this._setRangeMessage("Counts loaded");
    } catch (e) {
      if (requestId !== this._exactRequestId) return;
      this._setRangeMessage(e.message || "Prometheus query failed");
    }
  }

  _renderRows(force = false) {
    if (!this._shellReady || !this._hass?.states) return;
    const entries = this._getEntries();
    const window = this._minutes;
    const data = (!this._isBuiltin() && this._exactData) || null;
    const signature = JSON.stringify([
      window, data, entries.map(s => [s.entity_id, s.state,
        s.attributes.high_1h, s.attributes.high_24h,
        s.attributes.warning_1h, s.attributes.warning_24h,
        s.attributes.host, s.attributes.service_name, s.attributes.loki_entry_id]),
    ]);
    if (!force && signature === this._rowSignature) return;
    this._rowSignature = signature;
    const tbody = this.shadowRoot.querySelector("tbody");
    if (!tbody) return;
    tbody.replaceChildren();
    const builtin = this._isBuiltin();
    const w = window === "1440" ? "24h" : "1h";
    for (const state of entries) {
      const isFleet = state.attributes.monitor_type === "fleet";
      const host = state.attributes.host || "";
      const service = state.attributes.service_name || "";
      const scope = isFleet ? data?.fleet : data?.monitors?.find(
        x => x.host === host && (x.service_name || "") === service);
      const row = document.createElement("tr");
      const label = document.createElement("td");
      label.textContent = isFleet ? "All hosts" : host + (service ? ` / ${service}` : "");
      const high = document.createElement("td"); high.className = "numeric";
      high.textContent = builtin ? (state.attributes[`high_${w}`] ?? "—") : (scope?.high ?? "—");
      const warnings = document.createElement("td"); warnings.className = "numeric";
      warnings.textContent = builtin ? (state.attributes[`warning_${w}`] ?? "—") : (scope?.warning ?? "—");
      const cell = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "View";
      button.setAttribute("aria-label", `View errors for ${label.textContent}`);
      button.addEventListener("click", () => {
        try {
          openLogPopup(this._hass, {
            entry_id: state.attributes.loki_entry_id, host, service,
            severity: "errors", minutes: this._displayMinutes(), ...this._currentRangeParams(),
          }).catch(e => this._setRangeMessage(e.message));
        } catch (e) { this._setRangeMessage(e.message); }
      });
      cell.append(button);
      row.append(label, high, warnings, cell);
      tbody.append(row);
    }
    if (!entries.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No Log Summary sensors found";
      row.append(cell);
      tbody.append(row);
    }
  }
}
customElements.define("loki-log-button-card", LokiLogButtonCard);
customElements.define("loki-monitor-overview-card", LokiMonitorOverviewCard);
window.customCards = window.customCards || [];
window.customCards.push(
  {type:"loki-log-button-card",name:"Loki Log Popup Button",description:"Open on-demand host error logs"},
  {type:"loki-monitor-overview-card",name:"Homelab Log Overview",description:"Prometheus counts with per-host log buttons"},
);
// Cards supporting `fire-dom-event` can open the exact same popup on demand.
// The event is client-local. Loki's WebSocket API still enforces HA admin access.
window.addEventListener("ll-custom", event => {
  const cfg = event.detail?.loki_monitor;
  if (cfg && typeof cfg === "object") {
    openLogPopup(lastHass || document.querySelector("home-assistant")?.hass, cfg).catch(err => console.error("Loki Monitor popup:", err.message));
  }
});
