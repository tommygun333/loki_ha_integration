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
  static getStubConfig() { return {type:"custom:loki-monitor-overview-card", minutes:60}; }
  setConfig(config) {
    if (!config || config.type !== "custom:loki-monitor-overview-card") throw Error("Expected custom:loki-monitor-overview-card");
    this.config = {...config};
    if (!this.shadowRoot) this.attachShadow({mode:"open"});
    this._render();
  }
  set hass(hass) { this._hass = hass; lastHass = hass; this._render(); }
  getCardSize() { return 5; }
  _render() {
    if (!this.shadowRoot || !this.config || !this._hass) return;
    const entries = Object.values(this._hass.states).filter(s =>
      s.entity_id.startsWith("sensor.") && ["fleet", "host", "service"].includes(s.attributes?.monitor_type) &&
      (!this.config.entry_id || s.attributes.loki_entry_id === this.config.entry_id));
    entries.sort((a,b) => (a.attributes.monitor_type === "fleet" ? -1 : b.attributes.monitor_type === "fleet" ? 1 :
      (a.attributes.host || "").localeCompare(b.attributes.host || "")));
    const selectedWindow = String(this._minutes ?? this.config.minutes ?? 60);
    const custom = selectedWindow === "custom";
    const w = selectedWindow === "1440" ? "24h" : "1h";
    const minutes = w === "24h" ? 1440 : 60;
    const exactData = custom ? this._exactData : null;
    this.shadowRoot.innerHTML = `<style>
      :host{display:block}ha-card{padding:14px}h3{margin:0 0 12px;font-size:16px}
      table{width:100%;border-collapse:collapse}th,td{padding:9px 5px;text-align:left;border-bottom:1px solid var(--divider-color)}
      select{float:right;min-height:32px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:7px}
      th{font-size:12px;color:var(--secondary-text-color)}td.numeric{text-align:right;font-variant-numeric:tabular-nums}
      button{cursor:pointer;border:1px solid var(--divider-color);border-radius:7px;background:transparent;
      color:var(--primary-text-color);padding:6px 9px}button:focus-visible{outline:2px solid var(--primary-color)}
      </style><ha-card>
      <h3>Homelab Logs <select id="window" aria-label="Count period"><option value="60">1 hour</option><option value="1440">24 hours</option><option value="custom">Custom range</option></select></h3>
      <div id="exact-controls" ${custom ? '' : 'hidden'} style="margin:10px 0;display:${custom ? 'flex':'none'};flex-wrap:wrap;gap:8px;align-items:end">
        <label>From (local)<input id="exact-from" type="datetime-local" step="1" style="display:block"></label>
        <label>To (local)<input id="exact-to" type="datetime-local" step="1" style="display:block"></label>
        <button id="apply-exact" type="button">Load counts</button>
        <span id="exact-message" role="status"></span>
      </div>
      <div style="margin-bottom:10px"><button id="full-explorer" type="button">Open full log explorer</button></div>
      <table><thead><tr><th>Host / service</th><th>High</th><th>Warnings</th><th>Logs</th></tr></thead><tbody></tbody></table></ha-card>`;
    const windowSelect = this.shadowRoot.querySelector("#window");
    windowSelect.value = selectedWindow;
    windowSelect.addEventListener("change", () => { this._minutes=windowSelect.value; this._render(); });
    const fromInput = this.shadowRoot.querySelector('#exact-from');
    const toInput = this.shadowRoot.querySelector('#exact-to');
    fromInput.value = this._rangeFrom || localDateTime(Date.now()-3600000);
    toInput.value = this._rangeTo || localDateTime(Date.now());
    fromInput.addEventListener('change', () => {this._rangeFrom=fromInput.value;this._exactData=null;this._appliedExact=null;this._exactRequestId=(this._exactRequestId||0)+1;this._render();});
    toInput.addEventListener('change', () => {this._rangeTo=toInput.value;this._exactData=null;this._appliedExact=null;this._exactRequestId=(this._exactRequestId||0)+1;this._render();});
    this.shadowRoot.querySelector('#apply-exact').addEventListener('click', () => this._fetchExact(fromInput.value,toInput.value));
    this.shadowRoot.querySelector('#full-explorer').addEventListener('click', () => {
      let params = {};
      try { if (custom) params=exactRange(fromInput.value,toInput.value); }
      catch (e) { this._setRangeMessage(e.message);return; }
      openLogPopup(this._hass, {entry_id:this.config.entry_id || '',severity:'all',minutes,...params})
        .catch(e=>this._setRangeMessage(e.message));
    });
    const tbody = this.shadowRoot.querySelector("tbody");
    for (const state of entries) {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      name.textContent = state.attributes.monitor_type === "fleet" ? "All hosts" :
        (state.attributes.host + (state.attributes.service_name ? " / " + state.attributes.service_name : ""));
      const high = document.createElement("td"); high.className="numeric";
      const scoped = state.attributes.monitor_type === 'fleet' ? exactData?.fleet :
        exactData?.monitors?.find(x => x.host === state.attributes.host &&
          (x.service_name || '') === (state.attributes.service_name || ''));
      high.textContent = custom ? (scoped?.high ?? '—') : (state.attributes[`high_${w}`] ?? '—');
      const warning = document.createElement("td"); warning.className="numeric";
      warning.textContent = custom ? (scoped?.warning ?? '—') : (state.attributes[`warning_${w}`] ?? '—');
      const cell = document.createElement("td");
      const button = document.createElement("button"); button.textContent="View"; button.type="button";
      button.setAttribute("aria-label", `View errors for ${name.textContent}`);
      button.addEventListener("click", () => {
        let params={};
        try { if (custom) params=exactRange(fromInput.value,toInput.value); }
        catch (e) { this._setRangeMessage(e.message);return; }
        openLogPopup(this._hass, {
          entry_id:state.attributes.loki_entry_id, host:state.attributes.host || "",
          service:state.attributes.service_name || "", severity:"errors", minutes,...params,
        }).catch(err => this._setRangeMessage(err.message));
      });
      cell.append(button); tr.append(name,high,warning,cell); tbody.append(tr);
    }
    if (!entries.length) {
      const row = document.createElement("tr"); const cell = document.createElement("td");
      cell.colSpan = 4; cell.textContent = "No Log Summary sensors found"; row.append(cell); tbody.append(row);
    }
  }
  _setRangeMessage(message) {
    const node = this.shadowRoot?.querySelector('#exact-message');
    if (node) node.textContent = message;
  }
  async _fetchExact(start, end) {
    let params;
    try { params = exactRange(start, end); }
    catch (e) { this._setRangeMessage(e.message); return; }
    this._rangeFrom=start;this._rangeTo=end;
    this._setRangeMessage('Loading Prometheus counts…');
    const req = this._exactRequestId = (this._exactRequestId || 0) + 1;
    try {
      const response=await this._hass.connection.sendMessagePromise({
        type:'loki_monitor/range_counts',entry_id:this.config.entry_id || '',...params,
      });
      if (req !== this._exactRequestId) return;
      this._exactData=response;
      this._appliedExact=params;
      this._render();
      this._setRangeMessage('Exact-range counts loaded');
    } catch(e) {
      if(req === this._exactRequestId) this._setRangeMessage(e.message || 'Prometheus query failed');
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
