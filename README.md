# Grafana Loki Monitor for Home Assistant

**Version 0.4.0 — HACS custom repository**

A **read-only** Home Assistant integration for Grafana Loki. It queries Loki's HTTP API and creates monitoring entities and custom LogQL metric sensors. **Grafana Alloy remains responsible for collecting logs.** No duplicate ingestion is performed.

## Install or migrate to HACS

**Repository:** `tommygun333/loki_ha_integration` — `https://github.com/tommygun333/loki_ha_integration`

1. In Home Assistant open **HACS → ⋮ → Custom repositories**.
2. Paste `https://github.com/tommygun333/loki_ha_integration` and choose **Integration**, then Add.
3. Find **Grafana Loki Monitor** in HACS and select **Download**.
4. Restart Home Assistant Core.
5. If this is your first installation, navigate to **Settings → Devices & services → Add integration → Grafana Loki Monitor** and connect to the Loki API (not the Grafana frontend URL).

**Already installed manually?** Keep the existing `loki_monitor` config entry, sensors, and custom queries. **Do not delete the integration in Devices & services.** HACS installs the same files to `/config/custom_components/loki_monitor`, replacing the manual files, then you restart HA. Existing config entries use the unchanged `loki_monitor` domain and remain intact. Keep a HA backup before migration.

Updates: Until the first GitHub release, HACS tracks commits on the default branch. After a GitHub release is published, HACS will track release versions. Update in HACS, then restart HA and refresh the frontend if translations look stale.

**Before adding to HACS:** The repository owner must publish this repository on GitHub. A local ZIP by itself is **not** a working HACS custom repository.

## Features

- Loki connection UI: HTTP Basic, Bearer token or no authentication, optional X-Scope-OrgID tenant, SSL verification.
- Built-in 1h / 24h error and warning sensors; default `detected_level` queries match Grafana dashboard v1.5.
- Last error timestamp, observed host list, and binary connectivity/label diagnostics.
- User-defined numeric LogQL sensors (add, **edit**, remove and test queries in Configure).
- **Per-host/service monitors**: choose a host and optional `service_name`, then get 1h/24h warning/error counts for that scope.
- **Automatic entity registry cleanup**: deleting a custom metric or host/service monitor also removes its obsolete sensor entities during the next integration setup, including older orphaned entries. No extra reload mechanism is added.
- **Isolated query failures**: individual broken queries do not take unrelated sensors offline; a diagnostic **Query failures** sensor lists affected query IDs. A full Loki outage remains visible.
- **Rule-based Loki alerts**: configurable threshold/window/cooldown matching with stored checkpoints, event-driven Home Assistant automations, and safe dry-run query previews.
- **Optional canonical severity for counters**: Grafana v1.9 label and inline-marker classification for built-in and host/service metrics. Existing severity settings remain unchanged during upgrade.
- **Native Loki log viewer**: a responsive Lovelace card backed by an admin-only WebSocket API, cascading Host → Source → Service → Program suggestions, Grafana v1.9 canonical severity, IP-host suggestion hiding, configurable labels, and older-page navigation.
- `loki_monitor.query_logs` response-returning Home Assistant action for bounded, on-demand log retrieval.
- Polling 60–3600 seconds (default 120) and multiple Loki instances.

Not yet implemented: live tailing or Grafana deep links for alert events. This is an early prototype; please report bugs in the repository issue tracker.

## Configuration

When adding the integration, enter the **Loki HTTP server root**, e.g. `http://<loki-host>:3100`. **Do not** use the Grafana UI URL (`https://grafana.home`) or append `/loki/api/v1`. Choose `none` authentication for an internal instance with no proxy auth, or configure your secured proxy.

### Built-in severity counting (Grafana parity)

Starting in v0.1.2, the default counters and Last error sensor use Loki's `detected_level` structured metadata, **not** the indexed `level` stream label. This matches the original Grafana dashboard v1.5 semantics, including a non-empty `host` stream selector and the dashboard's All source/service/program filters. With empty text search and regex `.*`, the built-in warning counter's effective LogQL is:

```logql
sum(count_over_time({host=~".+",source=~".*",service_name=~".*",syslog_identifier=~".*"} | detected_level=~"(?i)warn"[24h])) or vector(0)
```

The error pattern matches `error|critical|fatal` (case-insensitive), exactly like the dashboard. The 1h/24h metric sensors and 24h Last error sensor all use the same classification. Only the selected query window differs.

The host label name remains configurable in **Configure → Polling and label mapping**. Streams without a nonempty host label are excluded in detected mode, as on the dashboard.

If you need the previous behavior, change **Built-in severity counting mode** to **Indexed level label (previous behavior)**. The existing `level`, `error,err,critical,fatal`, and `warn,warning` options are preserved and apply only in that indexed mode. Custom LogQL sensors are never modified by this option.

For comparing HA and Grafana, use the same time range, instant evaluation time, Loki tenant, and dashboard filters. Grafana can be a little ahead of the HA poll.

### Adding a custom metric sensor

Under **Settings → Devices & services → Grafana Loki Monitor → Configure → Add LogQL metric sensor**, choose a name and a LogQL expression that returns one numerical value. For example:

```logql
sum(count_over_time({host="CoreELEC",level=~"(?i)(error|fatal)"}[1h])) or vector(0)
```

Use actual host/label values shown in your Grafana Explore instance. A query returning multiple vector series is rejected: aggregate with `sum`, `max`, etc. A valid query returning an empty vector leaves the custom sensor unavailable until data appears.

### Host/service monitoring (v0.2)

Open **Settings → Devices & services → Grafana Loki Monitor → Configure → Add host/service monitor**.

1. Enter a monitor name (for example *CoreELEC Kodi*).
2. Choose the **Host** from recently observed Loki label values, or enter your own exact host value.
3. Optionally choose **Service name** (matches Loki's indexed `service_name` label). Leave it blank for all services on that host.
4. Save. Four sensors are created: **Errors (1h)**, **Errors (24h)**, **Warnings (1h)** and **Warnings (24h)**.

These sensors use the same detected-severity mode as the dashboard, or the optional indexed mode configured under Polling and label mapping. The monitor is matched by indexed `host` (or your configured host label) and optionally `service_name`; there is no automatic text recognition of service names. Unknown host/service combinations legitimately report zero. A maximum of 12 monitors can be configured to limit expensive 24h queries; polling is shared across all monitors and concurrent requests are bounded.

To modify an existing monitor, use **Edit host/service monitor**. Its UUID and sensor unique IDs remain stable across edits. **Remove host/service monitor** also cleans up the four associated sensor registrations automatically when the integration reloads. Previously orphaned monitor entities are cleaned up on the next setup as well.

### Custom LogQL query management (v0.2)

**Edit LogQL metric sensor** allows modifying an existing query, display name, unit, or icon without changing its unique ID. **Test LogQL metric query** executes a numeric instant query and displays the resulting value without creating an entity. Invalid or multiple-series outputs are reported as errors. Existing custom sensors are preserved during the v0.2 update. Removing a custom sensor also removes its entity registry record on the next integration setup.

### Partial-query failures (v0.2)

If a single built-in count, monitor, custom query, or label lookup fails, only affected data is marked unavailable; other sensors continue updating. When **every** query fails, the coordinator reports an outage and the `API available` binary sensor goes Off. Auth failures still trigger the reauthentication flow. Known-host counts refer to discovered Loki label values, not an active host heartbeat.

### Rule-based alerts and Home Assistant events (v0.4)

In **Settings → Devices & services → Grafana Loki Monitor → Configure**, use:

- **Add alert rule**: a named, enabled/disabled rule using the same Grafana v1.9 canonical severity classifier as the log viewer. Scope by exact host, source, service or program; filter by severity, contained text, and/or log-line regex. Set **minimum matching logs**, **time window in minutes**, and **cooldown minutes**. `3 matches / 5 minutes / 15-minute cooldown` is a good initial configuration.
- **Edit alert rule**: retain its rule ID and persisted checkpoint.
- **Remove alert rule**: delete the definition; its saved checkpoint is pruned when other rules are next initialized.
- **Test alert rule**: preview the number of matching entries in its time window. **This never fires an event or saves a rule.**

The engine shares the integration's existing polling interval (default **120 seconds**, no new scheduler). On a poll it checks the newest matching log, and **only if a newer match exists** checks whether the rolling window contains at least the configured threshold. It fires an HA bus event of type **`loki_monitor_alert`**, and remembers the seen timestamp and cooldown in private Home Assistant storage. This avoids triggering on the same logs repeatedly or replaying old entries after a restart. **New rules start from creation time:** their existing matching logs are ignored until a new matching entry arrives. Choose a rule window at least as long as the polling interval; a very short window can miss activity between polls. Longer outages can also miss alerts that fall outside the rule window. The engine is deliberately best-effort, not a guaranteed-delivery SIEM.

The event contains `entry_id`, `rule_id`, `name`, `severity`, `host`, `service`, `source`, `program`, `count`, `threshold`, `window_minutes`, and `last_match_ns`. **`message` is only included if you opt into "Include last matching log line"**, since logs may contain tokens or private content. The engine writes the checkpoint **before** emitting an event to minimize duplicates; this chooses at-most-once delivery, so a shutdown at exactly that moment can lose an event. Alert rule query failures are isolated from other sensors and surfaced through the diagnostic **Alert rule failures** sensor (with `failed_rules` attribute). There is a maximum of **12 rules per Loki connection**, with three concurrent rule queries.

Example Home Assistant event-trigger automation (replace the notification action with your phone's actual notify entity/service):

```yaml
alias: Loki - critical log notifications
triggers:
  - trigger: event
    event_type: loki_monitor_alert
conditions: []
actions:
  - action: notify.notify
    data:
      title: "Loki alert: {{ trigger.event.data.name }}"
      message: >-
        {{ trigger.event.data.count }} matching log entries in
        {{ trigger.event.data.window_minutes }} minutes.
        Host: {{ trigger.event.data.host or 'all' }}.
mode: queued
```

If you want different actions for different rules, add an event `event_data` match on `rule_id`, or use a template condition. An automation does not have to be created until you actually want notifications. **Creating rules by itself never sends notifications.**

### Canonical severity for metrics (v0.4)

Under **Configure → Polling and label mapping → Built-in severity counting mode**, select **Grafana v1.9 canonical severity** to opt into the same classification as the log viewer for your built-in 1h/24h counters, Last Error sensor, and each host/service monitor. The Grafana v1.9 classifier prioritizes the indexed `level`, then `detected_level`, then explicit line markers such as `[emerg]` and `[fatal]`. The older **Detected** and **Indexed** options remain available; **v0.4 keeps each existing connection's previous setting until you change it**. This is more expensive in Loki because each count needs to evaluate a line-formatting pipeline; start with your current polling interval and check resource use before adding many monitors.

### Native log viewer (v0.3.2)

The viewer is a **read-only Lovelace custom card** supplied by the integration. HACS installs the JavaScript, but Home Assistant needs its resource registered once.

1. Install/update v0.4.0 in HACS and restart Home Assistant Core.
2. Keep your existing **JavaScript module** resource URL `/loki_monitor/loki-log-viewer-card.js?v=0.3.2` unchanged; the frontend file was not modified in v0.4.0.
3. Add a **Manual card**. This is a full example of the supported settings:

```yaml
type: custom:loki-log-viewer-card
minutes: 60
page_size: 50
severity: all
severity_mode: canonical
cascading_filters: true
hide_ip_hosts: true
hidden_hosts: []
label_map:
  source: source
  service: service_name
  program: syslog_identifier
grafana_url: https://grafana.home
# Optional initial selections:
# host: CoreELEC
# source: journal
# service: coreelec-system
# program: systemd
# text: failed
# regex: '404|500'
# entry_id: <HA Loki config entry ID, only for a preselected server>
```

**Grafana v1.9 parity:** The default `canonical` mode copies the exact `label_format severity` logic from **Homelab Logs / Loki v1.9**. It classifies indexed `level` before `detected_level`, then recognizes explicit line markers such as `[emerg]`, `[fatal]`, `[warn]`; normalizes aliases (`crit`, `err`, `4`, etc.); and exposes Emergency, Alert, Critical, Fatal, Error, Warning, Notice, Info, Debug, Trace, and Unknown as filter levels. The **High severity** preset groups Emergency through Error, matching Grafana. Choose `severity_mode: detected` for the older, simpler `detected_level` behavior. This changes the viewer only; existing built-in HA metric sensors keep their current query configuration.

**Mobile-friendly multi-select filters (v0.3.2):** Host, Source, Service, and Program now use touch-friendly pop-up selectors rather than browser datalists. Search within the list, select up to 24 exact values per field (matching any of those values), use Clear / All, or add a custom exact value. Legacy scalar YAML defaults (e.g. `host: CoreELEC`) remain supported; you can also write `host: [CoreELEC, proxmox]` or `service: [kodi, systemd]`. The pop-up is a modal bottom sheet on phones, with a Cancel and Apply workflow. Time range, severity, and the other single-value settings retain their browser-native select controls. Multi-value LogQL selectors are escaped and anchored by the backend, not interpreted as user-authored regex.

**Cascading filters:** With `cascading_filters: true` (default), the suggestions mirror Grafana's Host → Source → Service → Program variable chain using Loki's scoped label-values API. Applying a changed upstream selection clears selections below it and reloads the relevant suggestions. `false` fetches independent/global suggestions. Press **Search / refresh** to fetch logs; filter browsing alone doesn't continuously query log lines. Suggestions use the selected time range; Loki may internally use a wider indexing window.

**Host cleanup:** `hide_ip_hosts: true` (default) hides IPv4/IPv6-looking values from the *host suggestion list*, and `hidden_hosts` hides any specified exact hostnames. Neither setting deletes Loki data or silently filters all log rows. Manually entered IP hosts remain searchable. If addresses are being indexed as hosts, investigate their original Alloy/syslog label mapping; the viewer doesn't generate them.

**Custom label names:** `label_map` changes the *indexed Loki labels* used for source, service, and program in both filter discovery and actual queries. The host label is inherited from the integration's configured host label. Invalid/duplicate label names are rejected on the backend. The configuration need not match this exact example if your Grafana dashboard variables target different labels.

**Transfer changes from Grafana:** Export your Grafana dashboard as JSON, then from a checkout of this repository run:

```bash
python tools/grafana_dashboard_profile.py homelab-loki-dashboard.json --grafana-url https://grafana.home
```

This prints a Lovelace YAML card profile populated from the JSON's Host/Source/Service/Program variable definitions and default time range. It **checks the dashboard Logs panel's canonical severity template against the integration** and warns if they differ. For a future changed template, run `python tools/grafana_dashboard_profile.py homelab-loki-dashboard.json --sync-severity` in your local source checkout, review the resulting `viewer.py` change, and publish a new HACS release. This tool does not import Grafana's separate timeseries/stat panels; only viewer-relevant settings and severity are translated. It does not contact Grafana or execute dashboard code.

**Safety:** All three viewer WebSocket commands require a Home Assistant admin session; the browser has no Loki credentials. Backend filters generate bounded, escaped LogQL, with a maximum seven-day window and 100 rows per page. The card has manual refresh, no live tail, and does not store raw logs in the HA recorder. Paging can skip logs sharing identical nanosecond timestamps at a page boundary; Loki retains the originals.

### On-demand log queries

Developer Tools → Actions → `loki_monitor.query_logs`, example:

```yaml
action: loki_monitor.query_logs
data:
  query: '{level="error"}'
  minutes: 60
  limit: 20
response_variable: loki_results
```

For testing from Developer Tools, inspect the action response. A log stream query like `{host="CoreELEC"} |= "error"` is supported. The action returns up to 200 newest-first entries, each with `timestamp_ns`, `labels`, `line` (truncated to 8 KiB) and optionally `metadata`. This action is intended for administrators and service-to-service automation use; logs may include sensitive information.

### Credentials and permissions

- Loki itself doesn't implement authorization by default. Use reverse proxy credentials or tenant headers as required by your setup.
- The integration requests only GET endpoints (`query`, `query_range`, `labels`, `label/.../values`). It never pushes, deletes, or updates logs.
- HA stores credentials as part of the config entry. Avoid exposing `.storage` backups to untrusted users.
- Query action is rejected for non-admin HA users. Automations running without a user context can call it; protect automations from untrusted inputs.
- High-cost LogQL queries may increase Loki CPU/disk work; start with 120s polling and use aggregated, selective queries.
- When the coordinator cannot reach Loki, metric sensors become unavailable. The API connectivity binary sensor reports a failed refresh.


## Development

Run `python -m pip install -r requirements-dev.txt` and `python -m pytest -q`. GitHub Actions runs the tests, HACS validator, and Home Assistant hassfest on changes. See [PUBLISHING.md](PUBLISHING.md) for creating the repository and publishing updates.

## Changelog

- **0.4.0:** Grafana v1.9 canonical severity as an opt-in mode for existing counters and host/service monitors; bounded alert rules with add/edit/remove/test flows, Loki newest-entry checkpoints, rolling-window thresholds, persistent cooldown state, HA event `loki_monitor_alert`, private optional log message, and failure diagnostics. No new reload mechanism; v0.3.2 viewer JavaScript unchanged.

- **0.3.2:** Mobile-friendly searchable multi-select popup filters for host/source/service/program. Backward-compatible string/list WebSocket schemas, exact escaped multi-value LogQL matchers, cascading contextual suggestions and no changes to existing metrics.

- **0.3.1:** Align native viewer with Grafana Homelab Logs / Loki v1.9 canonical severity, implement scoped cascading host/source/service/program suggestions, hide IP hosts from suggestions, make label mapping/cascade/severity configurable, and provide a dashboard-export profile converter. Retain v0.2.2 entity cleanup and options reload behavior.

- **0.3.0:** Admin-only WebSocket read APIs for log sources, filter suggestions and bounded paginated log streams; integrated responsive Lovelace log viewer card; validation, escaping, stable query-window cursors, and frontend resource documentation. Existing monitors, sensors, options, and automatic reloads unchanged.


- **0.2.2:** Remove obsolete custom query and monitor sensor entities from the Home Assistant entity registry during sensor platform setup. Also cleans orphaned registry records left behind by v0.2.0. Keeps the existing automatic options reload behavior unchanged; v0.2.1 reload patch is not required.

- **0.2.0:** Host/service scoped warning/error counters (1h and 24h), host/service discovery with custom values, add/edit/remove monitor UI, edit and test numeric LogQL queries, query-failure isolation, bounded parallel queries, and stronger options-flow regression tests. Existing entry IDs and custom query IDs retained.

- **0.1.5:** Fix options flow form step mismatch when opening the Add LogQL metric sensor form. Add flow-step contract regression tests.

- **0.1.4:** Refreshed numeric LogQL form translation key to avoid stale cached malformed text; corrected repository links and enabled GitHub Actions workflows. No sensor configuration migration required.

- **0.1.3:** HACS-ready repository metadata, documentation, CI, versioned GitHub release workflow. No behavioral changes to the Loki API or sensors.
- **0.1.2:** Grafana-aligned `detected_level` severity mode and fallback to indexed labels.
- **0.1.1:** Fixed a malformed translation placeholder in numeric LogQL sensor options.
- **0.1.0:** Initial read-only Loki integration with query sensors and on-demand logs.

MIT License.
