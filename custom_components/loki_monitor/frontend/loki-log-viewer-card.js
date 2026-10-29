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
    this._initialExact = config.range_start_ns && config.range_end_ns ? {
      range_start_ns:String(config.range_start_ns), range_end_ns:String(config.range_end_ns)
    } : null;
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
    this._suggestions = { host: [], source: [], service: [], program: [] };
    this._picker = null;
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
      host: this._initialSelection(c.host), source: this._initialSelection(c.source),
      service: this._initialSelection(c.service), program: this._initialSelection(c.program),
      severity: String(c.severity || "all"), severity_mode: "indexed",
      text: String(c.text || ""),
      regex: String(c.regex || ""),
      minutes: this._initialExact ? 'custom' : ([15, 60, 360, 1440, 10080].includes(Number(c.minutes)) ? Number(c.minutes) : 60),
      limit: [25, 50, 100].includes(Number(c.page_size)) ? Number(c.page_size) : 50,
    };
    this._initialExact = c.range_start_ns && c.range_end_ns ? {
      range_start_ns:String(c.range_start_ns), range_end_ns:String(c.range_end_ns)
    } : null;
    this._activeEntryId = String(c.entry_id || "");
    this._rows = [];
    this._cursor = null;
    this._anchor = null;
    this._more = false;
    this._revision = (this._revision || 0) + 1;
  }

  // An old scalar YAML value remains valid; arrays enable multi-selection.
  _initialSelection(value) {
    const items = Array.isArray(value) ? value : (value ? [value] : []);
    return [...new Set(items.filter(item => typeof item === "string")
      .map(item => item.trim()).filter(item => item && item.length <= 128))].slice(0, 24);
  }

  _renderShell() {
    // Only static markup. All Loki text is later inserted with textContent.
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; color:var(--primary-text-color); font-family:var(--paper-font-body1_-_font-family,inherit); }
        ha-card { display:block; background:var(--card-background-color,var(--ha-card-background,var(--secondary-background-color))); border-radius:var(--ha-card-border-radius,12px); box-shadow:var(--ha-card-box-shadow,none); padding:16px; }
        .head { display:flex; gap:12px; align-items:center; justify-content:space-between; margin-bottom:14px; }
        .head a { font-size:12px; color:var(--primary-color); }
        .multi-trigger { display:flex; width:100%; min-height:40px; padding:8px 10px; margin-top:4px; justify-content:space-between; align-items:center; gap:8px; text-align:left; border-radius:7px; background:var(--card-background-color,var(--primary-background-color)); }
        .multi-trigger .selected { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
        .multi-trigger .chevron { color:var(--secondary-text-color); font-size:14px; }
        .multi-trigger:focus-visible { outline:2px solid var(--primary-color); outline-offset:1px; }
        .picker { padding:0; color:var(--primary-text-color); background:var(--card-background-color,var(--primary-background-color)); border:1px solid var(--divider-color,#777); border-radius:15px; width:min(440px,calc(100vw - 24px)); max-height:min(82dvh,760px); box-shadow:0 12px 50px #0006; }
        .picker::backdrop { background:rgba(0,0,0,.56); }
        .picker-inner { display:flex; flex-direction:column; padding:16px; gap:12px; max-height:min(82dvh,760px); box-sizing:border-box; }
        .picker-top { display:flex; align-items:center; justify-content:space-between; gap:10px; }
        .picker-top h3 { font-size:17px; font-weight:600; margin:0; }
        .picker-search { width:100%; min-height:44px; font-size:15px; margin:0; }
        .picker-items { flex:1; min-height:90px; overflow:auto; overscroll-behavior:contain; padding:2px; }
        .picker-choice { display:flex; align-items:center; gap:12px; padding:9px 6px; min-height:44px; box-sizing:border-box; border-bottom:1px solid var(--divider-color,#9994); font-size:14px; cursor:pointer; overflow-wrap:anywhere; }
        .picker-choice input { width:19px; height:19px; min-height:19px; margin:0; flex-shrink:0; accent-color:var(--primary-color); }
        .picker-choice span { flex:1; min-width:0; }
        .picker-footer { display:flex; gap:8px; justify-content:flex-end; flex-wrap:wrap; }
        .picker-hint { font-size:12px; color:var(--secondary-text-color); }
        .picker-custom { display:flex; gap:8px; }
        .picker-custom input { margin:0; flex:1; min-width:0; min-height:42px; }
        .picker-custom button { flex-shrink:0; }
        @media(max-width:580px) { .picker { width:100vw; max-width:100vw; max-height:90dvh; margin:auto 0 0; border-radius:16px 16px 0 0; } .picker-inner { max-height:90dvh; padding:18px 16px max(16px,env(safe-area-inset-bottom)); } .picker-search { font-size:16px; } }
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
        <div class="head"><div><h2>Loki Log Viewer</h2><span class="sub">Read-only · Alloy indexed severity · administrators</span></div><a id="grafana" hidden target="_blank" rel="noopener noreferrer">Grafana Explore ↗</a></div>
        <section class="filters">
          <label>Server<select id="entry"></select></label>
          <label>Time range<select id="minutes"><option value="15">15 minutes</option><option value="60">1 hour</option><option value="360">6 hours</option><option value="1440">24 hours</option><option value="10080">7 days</option><option value="custom">Custom dates</option></select></label>
          <label>Host<button type="button" class="multi-trigger" id="host" aria-haspopup="dialog"><span class="selected">All hosts</span><span class="chevron" aria-hidden="true">▾</span></button></label>
          <label>Source<button type="button" class="multi-trigger" id="source" aria-haspopup="dialog"><span class="selected">All sources</span><span class="chevron" aria-hidden="true">▾</span></button></label>
          <label>Service<button type="button" class="multi-trigger" id="service" aria-haspopup="dialog"><span class="selected">All services</span><span class="chevron" aria-hidden="true">▾</span></button></label>
          <label>Program<button type="button" class="multi-trigger" id="program" aria-haspopup="dialog"><span class="selected">All programs</span><span class="chevron" aria-hidden="true">▾</span></button></label>
          <label>Severity<select id="severity"><option value="all">All levels</option><option value="errors">High severity (emergency → error)</option><option value="warnings">Warnings</option><option value="normal">Normal (notice/info/debug/trace)</option><option value="emergency">Emergency</option><option value="alert">Alert</option><option value="critical">Critical</option><option value="fatal">Fatal</option><option value="error">Error</option><option value="warning">Warning</option><option value="notice">Notice</option><option value="info">Info</option><option value="debug">Debug</option><option value="trace">Trace</option><option value="unknown">Unknown</option></select></label>
          
          <label>Page size<select id="limit"><option value="25">25</option><option value="50">50</option><option value="100">100</option></select></label>
          <label>Text contains<input id="text" maxlength="256" placeholder="Plain text"></label>
          <label>Regex<input id="regex" maxlength="256" placeholder="Optional LogQL regex"></label>
        </section>
        <div id="exact-controls" style="display:none;flex-wrap:wrap;gap:10px;margin:12px 0;align-items:end">
          <label>From (browser local time)<input id="exact-from" type="datetime-local" step="1"></label>
          <label>To (browser local time)<input id="exact-to" type="datetime-local" step="1"></label>
          <span class="sub">Selected window: 1 minute–7 days, seconds precision</span>
        </div>
        <div class="actions"><button class="primary" id="apply">Search / refresh</button><span id="status" role="status" aria-live="polite"></span></div>
        <section class="loglist" id="loglist" aria-label="Loki log entries"></section>
        <div class="foot"><button id="older" hidden>Load older logs</button></div>
      </ha-card>
      <dialog id="filter-picker" class="picker" aria-label="Choose filter values">
        <div class="picker-inner">
          <div class="picker-top"><h3 id="picker-title">Select values</h3><button id="picker-close" type="button" aria-label="Close filter">✕</button></div>
          <input id="picker-search" class="picker-search" type="search" placeholder="Search options" aria-label="Search options" autocomplete="off">
          <span class="picker-hint" id="picker-count">All selected</span>
          <div id="picker-items" class="picker-items" role="group" aria-label="Available values"></div>
          <div class="picker-custom"><input id="picker-custom" type="text" maxlength="128" placeholder="Custom exact value" aria-label="Custom exact value"><button id="picker-add-custom" type="button">Add</button></div>
          <span id="picker-error" class="picker-hint" role="status"></span>
          <div class="picker-footer"><button id="picker-clear" type="button">Clear / All</button><button id="picker-all-visible" type="button">Select shown</button><button id="picker-cancel" type="button">Cancel</button><button id="picker-apply" class="primary" type="button">Apply</button></div>
        </div>
      </dialog>
    `;
    this.shadowRoot.getElementById("apply").addEventListener("click", () => this._query(false));
    this.shadowRoot.getElementById("older").addEventListener("click", () => this._query(true));
    for (const key of ["host", "source", "service", "program"]) {
      this.shadowRoot.getElementById(key).addEventListener("click", () => this._openPicker(key));
    }
    this.shadowRoot.getElementById("picker-search").addEventListener("input", () => this._renderPickerChoices());
    this.shadowRoot.getElementById("picker-apply").addEventListener("click", () => this._applyPicker());
    for (const id of ["picker-close", "picker-cancel"]) {
      this.shadowRoot.getElementById(id).addEventListener("click", () => this.shadowRoot.getElementById("filter-picker").close());
    }
    this.shadowRoot.getElementById("picker-clear").addEventListener("click", () => {
      this._picker.selected.clear(); this._renderPickerChoices();
    });
    this.shadowRoot.getElementById("picker-all-visible").addEventListener("click", () => this._selectVisible());
    this.shadowRoot.getElementById("picker-add-custom").addEventListener("click", () => this._addCustomSelection());
    this.shadowRoot.getElementById("picker-custom").addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); this._addCustomSelection(); }
    });
    this.shadowRoot.getElementById("filter-picker").addEventListener("click", e => {
      if (e.target === e.currentTarget) e.currentTarget.close();
    });
    const from = this.shadowRoot.getElementById("exact-from");
    const to = this.shadowRoot.getElementById("exact-to");
    const localDateTime = ms => {
      const d=new Date(ms), n=x=>String(x).padStart(2,"0");
      return `${d.getFullYear()}-${n(d.getMonth()+1)}-${n(d.getDate())}T${n(d.getHours())}:${n(d.getMinutes())}:${n(d.getSeconds())}`;
    };
    const toLocal = ns => localDateTime(Number(BigInt(ns)/1000000n));
    from.value=this._initialExact ? toLocal(this._initialExact.range_start_ns) : localDateTime(Date.now()-3600000);
    to.value=this._initialExact ? toLocal(this._initialExact.range_end_ns) : localDateTime(Date.now());
    if(this._initialExact) this.shadowRoot.getElementById("minutes").value="custom";
    this._toggleExactControls();
    this.shadowRoot.getElementById("minutes").addEventListener("change", () => {
      this._toggleExactControls();this._loadSuggestions();
    });
    for(const input of [from,to]) input.addEventListener('change',()=>this._loadSuggestions());
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
    for (const key of ["text", "regex"]) {
      this.shadowRoot.getElementById(key).addEventListener("keydown", e => {
        if (e.key === "Enter") this._query(false);
      });
    }
  }

  _toggleExactControls() {
    const custom=this.shadowRoot.getElementById('minutes').value==='custom';
    this.shadowRoot.getElementById('exact-controls').style.display=custom?'flex':'none';
  }

  _exactBounds() {
    const start=this.shadowRoot.getElementById('exact-from').value;
    const end=this.shadowRoot.getElementById('exact-to').value;
    const a=new Date(start).getTime(), b=new Date(end).getTime();
    if(!start || !end || !Number.isFinite(a) || !Number.isFinite(b) ||
       b-a<60000 || b-a>7*86400000 || b>Date.now()+60000 ||
       a%1000!==0 || b%1000!==0) {
      throw new Error('Choose a valid time range (1 minute to 7 days, ending no later than now)');
    }
    return {range_start_ns:String(BigInt(a)*1000000n),range_end_ns:String(BigInt(b)*1000000n)};
  }

  _populateControls() {
    for (const [key, value] of Object.entries(this._filters)) {
      if (Array.isArray(value)) continue;
      const control = this.shadowRoot.getElementById(key);
      if (control) control.value = String(value);
    }
    this._updateSelectionButtons();
  }

  _updateSelectionButtons() {
    for (const key of ["host", "source", "service", "program"]) {
      const selected = this._filters[key];
      const name = {host:"hosts",source:"sources",service:"services",program:"programs"}[key];
      const text = selected.length === 0 ? `All ${name}` :
        selected.length === 1 ? selected[0] : `${selected.length} selected`;
      const button = this.shadowRoot.getElementById(key);
      button.querySelector(".selected").textContent = text;
      button.title = selected.join(", ") || `All ${name}`;
      button.setAttribute("aria-label", `${key}: ${text}`);
    }
  }

  _openPicker(key) {
    const dialog = this.shadowRoot.getElementById("filter-picker");
    if (dialog.open) dialog.close();
    this._picker = { key, selected: new Set(this._filters[key]) };
    this.shadowRoot.getElementById("picker-title").textContent = `Select ${key} (multiple)`;
    this.shadowRoot.getElementById("picker-search").value = "";
    this.shadowRoot.getElementById("picker-custom").value = "";
    this.shadowRoot.getElementById("picker-error").textContent = "";
    this._renderPickerChoices();
    dialog.showModal();
    this.shadowRoot.getElementById("picker-search").focus();
  }

  _pickerOptions() {
    if (!this._picker) return [];
    return [...new Set([...(this._suggestions[this._picker.key] || []), ...this._picker.selected])].sort(
      (a,b) => a.localeCompare(b, undefined, {sensitivity:"base"})
    );
  }

  _visiblePickerOptions() {
    const search = this.shadowRoot.getElementById("picker-search").value.toLocaleLowerCase().trim();
    return this._pickerOptions().filter(value => value.toLocaleLowerCase().includes(search));
  }

  _updatePickerCount() {
    const count = this._picker.selected.size;
    this.shadowRoot.getElementById("picker-count").textContent = count ? `${count} selected · max 24` : "All (no restrictions)";
  }

  _renderPickerChoices() {
    if (!this._picker) return;
    const list = this.shadowRoot.getElementById("picker-items");
    list.replaceChildren();
    for (const value of this._visiblePickerOptions()) {
      const choice = document.createElement("label"); choice.className = "picker-choice";
      const check = document.createElement("input"); check.type = "checkbox";
      check.checked = this._picker.selected.has(value);
      const name = document.createElement("span"); name.textContent = value;
      check.addEventListener("change", () => {
        if (check.checked && this._picker.selected.size >= 24) {
          check.checked = false;
          this.shadowRoot.getElementById("picker-error").textContent = "Maximum 24 values per filter";
          return;
        }
        if (check.checked) this._picker.selected.add(value);
        else this._picker.selected.delete(value);
        this._updatePickerCount();
      });
      choice.append(check,name); list.appendChild(choice);
    }
    if (!list.childElementCount) {
      const empty = document.createElement("div"); empty.className = "empty";
      empty.textContent = "No matching suggestions · you can add an exact value below";
      list.appendChild(empty);
    }
    this._updatePickerCount();
  }

  _selectVisible() {
    for (const value of this._visiblePickerOptions()) {
      if (this._picker.selected.size >= 24) break;
      this._picker.selected.add(value);
    }
    this._renderPickerChoices();
  }

  _addCustomSelection() {
    const field = this.shadowRoot.getElementById("picker-custom");
    const value = field.value.trim();
    const error = this.shadowRoot.getElementById("picker-error");
    error.textContent = "";
    if (!value || value.length > 128 || /[\r\n\0]/.test(value)) {
      error.textContent = "Enter an exact value, up to 128 characters"; return;
    }
    if (!this._picker.selected.has(value) && this._picker.selected.size >= 24) {
      error.textContent = "Maximum 24 values per filter"; return;
    }
    this._picker.selected.add(value); field.value = "";
    this._renderPickerChoices();
  }

  async _applyPicker() {
    const {key, selected} = this._picker;
    const next = [...selected];
    const previous = this._filters[key];
    const changed = next.length !== previous.length || next.some(v => !previous.includes(v));
    this._filters[key] = next;
    if (changed && this._cascadingFilters) {
      const keys = ["host", "source", "service", "program"];
      for (const downstream of keys.slice(keys.indexOf(key)+1)) this._filters[downstream] = [];
    }
    this.shadowRoot.getElementById("filter-picker").close();
    this._updateSelectionButtons();
    if (changed) await this._loadSuggestions();
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
    let filters;
    try { filters=this._readFilters(); } catch { return; }
    try {
      const response = await this._hass.connection.sendMessagePromise({
        type: "loki_monitor/filters", entry_id: id,
        host: filters.host, source: filters.source,
        service: filters.service, program: filters.program,
        minutes: filters.minutes, cascading: this._cascadingFilters,
        ...(filters.range_start_ns ? {
          range_start_ns:filters.range_start_ns,range_end_ns:filters.range_end_ns
        } : {}),
        ...this._labelMap,
      });
      if (id !== this._activeEntryId || revision !== this._suggestionRevision) return;
      const options = response.filters || {};
      for (const key of ["host", "source", "service", "program"]) {
        this._suggestions[key] = (options[key] || []).filter(value =>
          key !== "host" || (!this._hiddenHosts.includes(value) &&
          (!this._hideIpHosts || !this._isIpHost(value)))
        );
      }
      // Keep a currently open picker synchronized with the latest suggestions.
      if (this._picker && this.shadowRoot.getElementById("filter-picker").open) {
        this._renderPickerChoices();
      }
    } catch { /* Suggestions are optional; querying logs remains available. */ }
  }

  _readFilters() {
    const values = {};
    for (const key of ["host", "service", "source", "program"]) {
      values[key] = [...this._filters[key]];
    }
    for (const key of ["severity", "text", "regex", "minutes", "limit"]) {
      values[key] = this.shadowRoot.getElementById(key).value;
    }
    if (values.minutes === 'custom') {
      Object.assign(values, this._exactBounds());
      values.minutes = 60; // Ignored by the server for explicit bounds.
    }
    values.minutes = Number(values.minutes);
    values.limit = Number(values.limit);
    return values;
  }

  async _query(older) {
    if (!this._hass || !this._activeEntryId) return;
    if (older && !this._cursor) return;
    const rev = ++this._revision;
    let filters;
    try { filters = older ? this._appliedFilters : this._readFilters(); }
    catch (err) { this._status(err.message, true); return; }
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
      const level = String(labels.severity || extra.severity || labels.level || "unknown");
      const levelEl = document.createElement("span"); levelEl.className = `level ${/^(emergency|alert|critical|fatal|error)$/i.test(level) ? "error" : /warn/i.test(level) ? "warn" : ""}`;
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
