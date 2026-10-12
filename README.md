# Grafana Loki Monitor for Home Assistant

**Version 0.3.0 — HACS custom repository**

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
- **Native Loki log viewer**: a responsive Lovelace card backed by an admin-only Home Assistant WebSocket query API; no Loki credentials exposed to the browser. Includes filter suggestions, severity/host/service/source/program/text/regex filters, and older-page navigation.
- `loki_monitor.query_logs` response-returning Home Assistant action for bounded, on-demand log retrieval.
- Polling 60–3600 seconds (default 120) and multiple Loki instances.

Not yet implemented: an event/rule engine, live tailing, or Grafana deep links. This is an early prototype; please report bugs in the repository issue tracker.

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

### Native log viewer (v0.3)

The viewer is a **read-only Lovelace custom card** supplied by the integration. HACS installs the JavaScript alongside the Python backend, but Home Assistant still needs its Lovelace resource added **once** (this is separate from installing the integration):

1. After installing v0.3.0 through HACS, **restart Home Assistant** to register the backend API and static card resource.
2. Open **Settings → Dashboards → Resources** (three-dot menu, depending on UI version). Add a **JavaScript module** resource with URL:
   `/loki_monitor/loki-log-viewer-card.js?v=0.3.0`
3. Add a **Manual card** to your Lovelace dashboard with:

```yaml
type: custom:loki-log-viewer-card
minutes: 60
page_size: 50
severity: errors
# Optional defaults:
# host: CoreELEC
# service: kodi
# source: journal
# program: systemd
# text: failed
# regex: '404|500'
# entry_id: <HA Loki config entry ID, only necessary if choosing a default>
```

The card will discover all loaded Loki connections and let you choose one. If you have just one Loki server, no `entry_id` is needed. It presents **host, source, service, program, severity, plain-text and regex filtering**, time presets of 15 minutes through seven days, a 25/50/100 page size, and **Load older logs**. An admin may expand individual entries to see indexed labels and Loki structured metadata. Updating the card JavaScript in a future version requires updating the `?v=` resource query (or using a cache-busting refresh).

**Safety and behavior:** Only **Home Assistant administrators** may call `loki_monitor/sources`, `loki_monitor/filters`, or `loki_monitor/query_logs`. The browser authenticates to Home Assistant's WebSocket and never connects directly to Loki; all API credentials stay server-side. The viewer generates fixed, escaped LogQL expressions rather than executing arbitrary unbounded LogQL from the card, bounds its date range (maximum seven days) and result pages (maximum 100 rows), and does **not** create sensors or insert raw logs into HA's recorder database. Results display as plain text, not HTML, and refresh only when requested (no live tail). Regex filters are evaluated by Loki; use selective ranges for expensive expressions. Like most time-based log pagination, entries at an identical nanosecond timestamp at a page boundary might be skipped; this is not a loss of stored Loki data.

A successful frontend query requires the integration to be loaded and Loki to be reachable. For `source` and `service`, the viewer uses indexed Loki labels (`source` and `service_name`); the severity filter uses Loki's `detected_level` like your Grafana dashboard. After opening a new dashboard or updating the resource, refresh the browser if the card is initially listed as unknown.

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
