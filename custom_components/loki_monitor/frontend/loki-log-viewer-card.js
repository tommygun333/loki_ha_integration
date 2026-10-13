/* Grafana Loki Monitor — admin-only read-only log viewer for Home Assistant. */
class LokiLogViewerCard extends HTMLElement {
  static getStubConfig() {
    return { type: "custom:loki-log-viewer-card", minutes: 60, page_size: 50 };
  }
  setConfig(config) {
    if (!config || config.type !== "custom:loki-log-viewer-card") {
      throw new Error("Expected type: custom:loki-log-viewer-card");
    }
    this._config = { ...config };
    const labels = config.label_map || {};
    this._labelMap = {
      source_label: String(labels.source || "source"),
      service_label: String(labels.service || "service_name"),
      program_label: String(labels.program || "syslog_identifier"),
    };
    this._cascadingFilters = config.cascading_filters !== false;
    this._hideIpHosts = config.hide_ip_hosts !== false;
    this._hiddenHosts = Array.isArray(config.hidden_hosts) ? config.hidden_hosts.map(String) : [];
    this._suggestionRevision = 0;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this._renderShell();
    }
    this._setDefaultFilters();
    this._populateControls();
    if (this.isConnected && this._hass && !this._loaded) this._initialize();
  }

  set hass(hass) {
    const connectionChanged = !!this._connection && this._connection !== hass.connection;
    this._hass = hass;
    if (connectionChanged) {
      this._loaded = false;
      this._revision = (this._revision || 0) + 1;
    }
    this._connection = hass.connection;
    if (this.isConnected && this._config && !this._loaded) {
      this._initialize();
    }
  }

  connectedCallback() {
    if (this._hass && this._config && !this._loaded) this._initialize();
  }

  disconnectedCallback() {
    this._revision = (this._revision || 0) + 1;
    this._loaded = false;
  }

  getCardSize() { return 8; }

  _setDefaultFilters() {
    const c = this._config;
    this._filters = {
      host: String(c.host || ""), source: String(c.source || ""),
      service: String(c.service || ""), program: String(c.program || ""),
      severity: String(c.severity || "all"), severity_mode: String(c.severity_mode || "canonical"),
      text: String(c.text || ""),
      regex: String(c.regex || ""),
      minutes: [15, 60, 360, 1440, 10080].includes(Number(c.minutes)) ? Number(c.minutes) : 60,
      limit: [25, 50, 100].includes(Number(c.page_size)) ? Number(c.page_size) : 50,
    };
    this._activeEntryId = String(c.entry_id || "");
    this._rows = [];
    this._cursor = null;
    this._anchor = null;
    this._more = false;
    this._revision = (this._revision || 0) + 1;
  }

  _renderShell() {
    // Only static markup. All Loki text is later inserted with textContent.
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family,inherit); }
        ha-card { display:block; background:var(--card-background-color,var(--ha-card-background,var(--secondary-background-color))); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow,none); padding:16px; }
        .head { display:flex; gap:12px; align-items:center; justify-content:space-between; margin-bottom:14px; }
        .head a { font-size:12px; color:var(--primary-color); }
        h2 { font-size:18px; margin:0; font-weight:600; }
        .sub { font-size:12px; color:var(--secondary-text-color); }
        .filters { display:grid; grid-template-columns:repeat(auto-fit,minmax(145px,1fr)); gap:10px; align-items:end; }
        label { font-size:12px; color:var(--secondary-text-color); display:block; }
        input,select { box-sizing:border-box; width:100%; margin-top:4px; min-width:0; min-height:36px; padding:6px 8px; border:1px solid var(--divider-color,#999); border-radius:6px; background:var(--card-background-color,var(--primary-background-color)); color:var(--primary-text-color); font:inherit; font-size:13px; }
        input:focus,select:focus { outline:2px solid var(--primary-color); outline-offset:1px; }
        button { cursor:pointer; padding:7px 14px; min-height:36px; border:1px solid var(--divider-color,#999); border-radius:7px; color:var(--primary-text-color); background:var(--secondary-background-color); font:inherit; }
        button.primary { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:transparent; }
        button[disabled] { opacity:.5; cursor:not-allowed; }
        .actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:12px 0 8px; }
        #status { color:var(--secondary-text-color); font-size:12px; flex:1; }
        #status.error { color:var(--error-color,#b00020); }
        .loglist { max-height:560px; overflow-y:auto; border-top:1px solid var(--divider-color,#aaa); }
        .item { padding:10px 2px; border-bottom:1px solid var(--divider-color,#ddd); }
        .meta { display:flex; flex-wrap:wrap; gap:8px; align-items:center; font-size:12px; color:var(--secondary-text-color); }
        .level { border:1px solid var(--divider-color,#aaa); border-radius:4px; padding:1px 5px; text-transform:uppercase; font-weight:600; }
        .level.error { color:var(--error-color,#e53935); }
        .level.warn { color:var(--warning-color,#df9f00); }
        .line { white-space:pre-wrap; overflow-wrap:anywhere; font:12px/1.55 var(--code-font-family,ui-monospace,monospace); margin:6px 0; }
        details { margin-top:3px; font-size:12px; }
        summary { cursor:pointer; color:var(--secondary-text-color); }
        pre { white-space:pre-wrap; overflow-wrap:anywhere; max-height:240px; overflow:auto; padding:8px; background:var(--secondary-background-color); }
        .foot { display:flex; justify-content:center; margin-top:12px; }
        .empty { padding:26px 6px; text-align:center; color:var(--secondary-text-color); font-size:13px; }
        @media (max-width:580px) { ha-card { padding:12px; } .filters { grid-template-columns:repeat(2,minmax(0,1fr)); } }
      </style>
      <ha-card>
        <div class="head"><div><h2>Loki Log Viewer</h2><span class="sub">Read-only · Grafana-compatible severity · administrators</span></div><a id="grafana" hidden target="_blank" rel="noopener noreferrer">Grafana Explore ↗</a></div>
        <section class="filters">
          <label>Server<select id="entry"></select></label>
          <label>Time range<select id="minutes"><option value="15">15 minutes</option><option value="60">1 hour</option><option value="360">6 hours</option><option value="1440">24 hours</option><option value="10080">7 days</option></select></label>
          <label>Host<input id="host" list="hosts" maxlength="128" placeholder="All hosts"></label><datalist id="hosts"></datalist>
          <label>Service<input id="service" list="services" maxlength="128" placeholder="All services"></label><datalist id="services"></datalist>
          <label>Source<input id="source" list="sources" maxlength="128" placeholder="All sources"></label><datalist id="sources"></datalist>
          <label>Program<input id="program" list="programs" maxlength="128" placeholder="All programs"></label><datalist id="programs"></datalist>
          <label>Severity<select id="severity"><option value="all">All levels</option><option value="errors">High severity (emergency → error)</option><option value="warnings">Warnings</option><option value="emergency">Emergency</option><option value="alert">Alert</option><option value="critical">Critical</option><option value="fatal">Fatal</option><option value="error">Error</option><option value="warning">Warning</option><option value="notice">Notice</option><option value="info">Info</option><option value="debug">Debug</option><option value="trace">Trace</option><option value="unknown">Unknown</option></select></label>
          <label>Severity classification<select id="severity_mode"><option value="canonical">Grafana v1.9 (indexed → detected → marker)</option><option value="detected">Detected level (legacy)</option></select></label>
          <label>Page size<select id="limit"><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label>
          <label>Text contains<input id="text" maxlength="256" placeholder="Plain text"></label>
          <label>Regex<input id="regex" maxlength="256" placeholder="Optional LogQL regex"></label>
        </section>
        <div class="actions"><button class="primary" id="apply">Search / refresh</button><span id="status" role="status" aria-live="polite"></span></div>
        <section class="loglist" id="loglist" aria-label="Loki log entries"></section>
        <div class="foot"><button id="older" hidden>Load older logs</button></div>
      </ha-card>
    `;
    this.shadowRoot.getElementById("apply").addEventListener("click", () => this._query(false));
    this.shadowRoot.getElementById("older").addEventListener("click", () => this._query(true));
    for (const [index, key] of ["host", "source", "service", "program"].entries()) {
      this.shadowRoot.getElementById(key).addEventListener("change", async () => {
        if (this._cascadingFilters) {
          for (const downstream of ["host", "source", "service", "program"].slice(index + 1)) {
            this.shadowRoot.getElementById(downstream).value = "";
          }
        }
        await this._loadSuggestions();
      });
    }
    this.shadowRoot.getElementById("minutes").addEventListener("change", () => this._loadSuggestions());
    this.shadowRoot.getElementById("entry").addEventListener("change", async () => {
      this._activeEntryId = this.shadowRoot.getElementById("entry").value;
      await this._loadSuggestions();
      await this._query(false);
    });
    if (this._config.grafana_url) {
      const link = this.shadowRoot.getElementById("grafana");
      try {
        const url = new URL(String(this._config.grafana_url), window.location.href);
        if (url.protocol === "https:" || url.protocol === "http:") {
          url.pathname = url.pathname.replace(/\/$/, "") + "/explore";
          link.href = url.toString();
          link.hidden = false;
        }
      } catch { /* Ignore invalid optional Grafana URL. */ }
    }
    for (const control of this.shadowRoot.querySelectorAll("input")) {
      control.addEventListener("keydown", (e) => { if (e.key === "Enter") this._query(false); });
    }
  }

  _populateControls() {
    for (const [key, value] of Object.entries(this._filters)) {
      const control = this.shadowRoot.getElementById(key);
      if (control) control.value = String(value);
    }
  }

  async _initialize() {
    this._loaded = true;
    const rev = ++this._revision;
    this._status("Loading Loki servers…");
    try {
      const response = await this._hass.connection.sendMessagePromise({ type: "loki_monitor/sources" });
      if (rev !== this._revision || !this.isConnected) return;
      const sources = response.sources || [];
      const select = this.shadowRoot.getElementById("entry");
      select.replaceChildren();
      for (const source of sources) {
        const opt = document.createElement("option");
        opt.value = source.entry_id;
        opt.textContent = source.title;
        select.appendChild(opt);
      }
      if (!sources.length) { this._status("No loaded Loki connection found", true); return; }
      if (!sources.some(s => s.entry_id === this._activeEntryId)) this._activeEntryId = sources[0].entry_id;
      select.value = this._activeEntryId;
      await this._loadSuggestions();
      if (rev !== this._revision || !this.isConnected) return;
      await this._query(false);
    } catch (err) {
      if (rev === this._revision) this._status(this._errorMessage(err), true);
    }
  }

  _isIpHost(value) {
    const v = String(value);
    const ipv4 = /^(\d{1,3}\.){3}\d{1,3}$/.test(v) &&
      v.split(".").every(part => Number(part) <= 255);
    // IPv6 (including compressed and IPv4-mapped forms) is only hidden in suggestions.
    const ipv6 = v.includes(":") && /^[0-9a-fA-F:.%]+$/.test(v);
    return ipv4 || ipv6;
  }

  async _loadSuggestions() {
    if (!this._hass || !this._activeEntryId) return;
    const id = this._activeEntryId;
    const revision = ++this._suggestionRevision;
    const filters = this._readFilters();
    try {
      const response = await this._hass.connection.sendMessagePromise({
        type: "loki_monitor/filters", entry_id: id,
        host: filters.host, source: filters.source,
        service: filters.service, program: filters.program,
        minutes: filters.minutes, cascading: this._cascadingFilters,
        ...this._labelMap,
      });
      if (id !== this._activeEntryId || revision !== this._suggestionRevision) return;
      const options = response.filters || {};
      for (const [key, listId] of [["host","hosts"],["source","sources"],["service","services"],["program","programs"]]) {
        const list = this.shadowRoot.getElementById(listId);
        list.replaceChildren();
        const filtered = (options[key] || []).filter(value =>
          key !== "host" || (!this._hiddenHosts.includes(value) &&
          (!this._hideIpHosts || !this._isIpHost(value)))
        );
        for (const value of filtered) {
          const element = document.createElement("option");
          element.value = value;
          list.appendChild(element);
        }
      }
    } catch { /* Suggestions are optional; querying logs remains available. */ }
  }

  _readFilters() {
    const values = {};
    for (const key of ["host","service","source","program","severity","severity_mode","text","regex","minutes","limit"]) {
      values[key] = this.shadowRoot.getElementById(key).value;
    }
    values.minutes = Number(values.minutes);
    values.limit = Number(values.limit);
    return values;
  }

  async _query(older) {
    if (!this._hass || !this._activeEntryId) return;
    if (older && !this._cursor) return;
    const rev = ++this._revision;
    const filters = older ? this._appliedFilters : this._readFilters();
    if (!older) {
      this._rows = [];
      this._cursor = null;
      this._anchor = null;
      this._more = false;
      this._appliedFilters = filters;
      this._renderRows();
    }
    const message = { type:"loki_monitor/query_logs", entry_id:this._activeEntryId,
      ...filters, ...this._labelMap };
    if (older) {
      message.cursor_ns = this._cursor;
      message.end_ns = this._anchor;
    }
    this._status("Querying Loki…");
    this._setBusy(true);
    try {
      const result = await this._hass.connection.sendMessagePromise(message);
      if (rev !== this._revision || !this.isConnected) return;
      this._anchor = result.end_ns;
      this._cursor = result.cursor_ns;
      this._more = result.has_more;
      this._rows = older ? this._rows.concat(result.logs || []) : (result.logs || []);
      this._renderRows();
      this._status(`${this._rows.length} log entries${this._more ? " · older entries available" : ""}`);
    } catch (err) {
      if (rev === this._revision) this._status(this._errorMessage(err), true);
    } finally {
      if (rev === this._revision) this._setBusy(false);
    }
  }

  _status(message, error = false) {
    const status = this.shadowRoot?.getElementById("status");
    if (status) { status.textContent = message; status.classList.toggle("error", error); }
  }

  _errorMessage(err) {
    if (err && typeof err.message === "string") return err.message.slice(0,240);
    if (err && typeof err.code === "string") return `Query error: ${err.code}`;
    return "Log query failed — check the Home Assistant log";
  }

  _setBusy(busy) {
    this.shadowRoot.getElementById("apply").disabled = busy;
    const older = this.shadowRoot.getElementById("older");
    older.disabled = busy;
    older.hidden = !this._more;
  }

  _renderRows() {
    const list = this.shadowRoot.getElementById("loglist");
    list.replaceChildren();
    if (!this._rows.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No matching log entries in this range";
      list.appendChild(empty);
    }
    for (const log of this._rows) {
      const item = document.createElement("article"); item.className = "item";
      const meta = document.createElement("div"); meta.className = "meta";
      const date = document.createElement("time");
      const millis = Math.floor(Number(log.timestamp_ns) / 1e6);
      date.textContent = Number.isFinite(millis) ? new Date(millis).toLocaleString() : "Unknown time";
      meta.appendChild(date);
      const labels = log.labels || {};
      const extra = log.metadata || {};
      const level = String(labels.severity || extra.severity || labels.level ||
        extra.detected_level || labels.detected_level || "unknown");
      const levelEl = document.createElement("span"); levelEl.className = `level ${/error|critical|fatal/i.test(level) ? "error" : /warn/i.test(level) ? "warn" : ""}`;
      levelEl.textContent = level; meta.appendChild(levelEl);
      for (const value of [labels.host, labels[this._labelMap.service_label],
        labels[this._labelMap.source_label], labels[this._labelMap.program_label]]) {
        if (!value) continue;
        const span = document.createElement("span"); span.textContent = String(value); meta.appendChild(span);
      }
      const line = document.createElement("pre"); line.className = "line"; line.textContent = String(log.line || "");
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "Labels & metadata";
      const data = document.createElement("pre"); data.textContent = JSON.stringify({labels, metadata:extra},null,2);
      details.append(summary, data); item.append(meta,line,details); list.appendChild(item);
    }
    const button = this.shadowRoot.getElementById("older");
    button.hidden = !this._more;
  }
}

if (!customElements.get("loki-log-viewer-card")) customElements.define("loki-log-viewer-card", LokiLogViewerCard);
window.customCards = window.customCards || [];
if (!window.customCards.some(card => card.type === "loki-log-viewer-card")) {
  window.customCards.push({
    type: "loki-log-viewer-card", name: "Loki Log Viewer", preview: false,
    description: "Read-only searchable Grafana Loki logs (admin users)",
    documentationURL: "https://github.com/tommygun333/loki_ha_integration",
  });
}
