# Grafana Loki Monitor for Home Assistant

**Version 0.6.0 · HACS custom integration · Prometheus + Loki + Grafana Alloy**

A read-only Home Assistant integration for an Alloy-based logging stack. **Prometheus supplies all automatic log-event counters**; **Loki provides raw log inspection and narrowly scoped, message-based alerting**. Alloy collects and normalizes logs before they reach either backend. The integration does not ingest, push, or modify logs.

> This release requires the indexed Loki label `severity` and the Prometheus metric `homelab_log_service_events_total`. It is intended for the homelab pipeline using the Prometheus scrape job `homelab-alloy-logs`. It is **not** a generic Loki-only integration.

## Architecture and query costs

| Component | Purpose | Automatic workload |
| --- | --- | --- |
| Grafana Alloy | Collects logs, normalizes `severity`, emits counters | Runs on collectors outside this integration |
| Prometheus | 1h/24h counts per fleet, host, and service | **Two grouped instant queries** per integration poll |
| Loki | Full log lines, filter suggestions, manually requested searches | No scheduled log queries unless custom query polling or alert rules are enabled |
| Home Assistant | Compact summary sensors, explorer and popup cards | Default poll interval **120 seconds** (configurable 60–3600 seconds) |

The metric used is `homelab_log_service_events_total{job="homelab-alloy-logs", host, service_name, severity_group}`. The four `severity_group` values are `high`, `warning`, `normal`, and `unknown`. The high group covers `emergency`, `alert`, `critical`, `fatal`, and `error`. Normal covers `notice`, `info`, `debug`, and `trace`.

The integration issues one PromQL query for `1h` and one for `24h`, grouped by `host`, `service_name`, and `severity_group`; it computes the fleet/host/service summaries from those two responses. Its counter expression uses `increase()`, `min_over_time()` and a first-observed-sample correction compatible with the current Grafana dashboard v2.5.3. **Counts are estimates** across counter resets, gaps, sparse series, or startup. Prometheus ingestion counters can disagree with Loki when Loki deduplicates identical timestamp/stream/line entries. The two systems are not interchangeable event stores.

## Installation

1. In **HACS → Custom repositories**, add `https://github.com/tommygun333/loki_ha_integration` as an **Integration**, and download **Grafana Loki Monitor**.
2. Restart Home Assistant, then go to **Settings → Devices & services → Add integration → Grafana Loki Monitor**.
3. Supply the **Loki base URL** (for example `http://192.168.1.13:3100`) and **Prometheus base URL** (for example `http://192.168.1.13:9090`). These are examples for the documented homelab; they must be reachable **from Home Assistant**. Use the server root, not Grafana's UI or `/api/v1/query`.
4. Set Loki authentication (none, HTTP Basic, or bearer token), optional tenant ID, and TLS verification as needed. Prometheus currently expects a directly accessible API on a trusted network; it does not have separate authentication options.
5. Go to **Configure → Prometheus and advanced queries** to set the polling interval, Prometheus URL, Loki host-label name and optional custom LogQL polling.

A Prometheus connection test checks that the Alloy service-counter metric exists. If Prometheus is down, **count sensors become unavailable**; the integration never silently falls back to expensive Loki counts. Keep Prometheus's HTTP API on a trusted network, not publicly exposed.

For an existing manual installation, the `loki_monitor` domain stays the same. HACS can replace the files in `/config/custom_components/loki_monitor`; restart Home Assistant. Back up your Home Assistant configuration first. A v0.5.x → v0.6.0 upgrade intentionally removes obsolete four-per-monitor count sensors and built-in error/warning/last-error sensors, so automations referring to their entity IDs must be updated.

## Compact monitoring entities

- **One fleet summary sensor** and **one sensor per watched host or host/service monitor**. A monitor may have an optional `service_name` filter.
- Primary sensor state: **high-severity count over the past 1 hour**.
- Sensor attributes: `high_1h`, `high_24h`, `warning_1h`, `warning_24h`, `normal_1h`, `normal_24h`, `unknown_1h`, `unknown_24h`, `total_1h`, `total_24h`, plus `monitor_type`, `host`, `service_name`, `loki_entry_id`, `count_source`.
- Diagnostics: **Query failures**, **Alert rule failures**, and **Counter backend available** binary sensor.
- Custom numeric LogQL sensors remain available only if **Poll custom user-authored LogQL sensors** is enabled; it is **off by default**.

The `high_1h` state is convenient for normal Home Assistant cards and automations. For example, read the other windows through a template: `{{ state_attr('sensor.your_host_log_summary', 'high_24h') }}`. Entity IDs depend on the names chosen and the HA entity registry; inspect Developer Tools → States to find the actual ID.

A successful Prometheus query with no samples for an individual host is treated as zero for that host. If a whole required query fails, values from that window are not reported as zero. A host's counts do **not** establish collector health or host reachability.

### Add hosts in one operation

Choose **Configure → Add multiple host monitors**, select any number of discovered host label values, then save **once**. The integration reloads once rather than after each host. Already monitored host-only values are excluded. You can also use **Add host/service monitor** for a specific host and optional `service_name`, or edit/remove existing monitors. The maximum is **64 monitors** per integration instance.

Adding an individual monitor creates **one** summary sensor, not four. Removing a monitor cleans up its obsolete entity-registry records during setup.

## Log explorer dashboard card

The bundled explorer uses the **indexed `severity` label from Alloy**. It does not recalculate severity using `detected_level`, line regexes or `label_format` templates. It supports exact/multiselect Host, Source, Service, and Program filters; severity group and individual severity selection; text/regex; 15m/1h/6h/24h/7d ranges; and bounded pagination. It requires a Home Assistant administrator session to retrieve raw logs.

Register **one JavaScript module resource** in **Settings → Dashboards → Resources**:

```text
/loki_monitor/loki-log-viewer-card.js?v=0.6.0
```

Add this Manual card anywhere on your dashboard:

```yaml
type: custom:loki-log-viewer-card
minutes: 60
page_size: 50
severity: all
cascading_filters: true
hide_ip_hosts: true
hidden_hosts: []
label_map:
  source: source
  service: service_name
  program: syslog_identifier
# Optional defaults:
# host: CoreELEC
# service: kodi
# text: failed
# regex: 'timeout|exception'
# entry_id: your_entry_id
```

The full explorer loads recent logs when the card opens and when the user searches or pages; it is **not** a live tail. `hide_ip_hosts` and `hidden_hosts` affect suggestions, not the underlying indexed data. The viewer supports up to 24 exact selections per filter, a maximum 7-day time range and up to 100 log lines per page. Filter suggestions query Loki's label API when the explorer is opened or changed; they are not included in periodic monitoring queries.

If using an older explorer resource URL, **update the existing resource** rather than registering a duplicate. Remove any legacy `severity_mode` YAML setting: only Alloy-indexed severity is supported now.

## Reusable log-popup buttons anywhere

Also register this **second JavaScript module resource** in Dashboard Resources:

```text
/loki_monitor/loki-monitor-cards.js?v=0.6.0
```

This resource provides a standalone popup button that can be placed on **any** Lovelace view, independent of the optional overview card. It does **not query Loki until clicked**:

```yaml
type: custom:loki-log-button-card
name: CoreELEC errors
host: CoreELEC
severity: errors
minutes: 60
# Optional: display the state of a summary entity on the button
# entity: sensor.coreelec_log_summary
```

For the entire fleet:

```yaml
type: custom:loki-log-button-card
name: All host errors
severity: errors
minutes: 1440
```

For a service on a host:

```yaml
type: custom:loki-log-button-card
name: Jellyfin service errors
host: jellyfin
service: jellyfin
severity: errors
minutes: 60
```

The exact `host` and `service` values must match your Alloy/Loki labels. `severity: errors` expands to emergency/alert/critical/fatal/error. `entry_id` can target a specific integration instance when multiple Loki connections exist.

**Optional custom button-card integration:** cards that support `fire-dom-event` can open the same popup using the client-local `ll-custom` action. Example for a compatible `custom:button-card`:

```yaml
type: custom:button-card
name: OPNsense error logs
tap_action:
  action: fire-dom-event
  loki_monitor:
    host: OPNsense.internal
    severity: errors
    minutes: 60
```

This event hook depends on the third-party card firing `ll-custom`; test in your own dashboard. The standalone `custom:loki-log-button-card` is the supported fallback and requires no extra third-party card.

### Optional overview card

```yaml
type: custom:loki-monitor-overview-card
minutes: 60
# Optional entry_id: your_entry_id
```

The overview shows the existing fleet/host/service summary sensor states with a 1h/24h selector and per-row **View** buttons. It reads Home Assistant states and does not issue background Loki queries. **You do not need this card to use the popup buttons.**

## Alert rules (indexed severity, limited queries)

Use **Configure → Add alert rule** to scope by indexed host/source/service/program, severity, optional text or regex, matching threshold (1–100), window (1–1440 minutes), and cooldown (0–10080 minutes). Up to 12 rules are supported. **Test alert rule** previews up to the configured threshold of matching log rows; it does not fire an event.

Enabled rules check bounded Loki log pages at polling time. A rule fires only for newer matching log entries after a persisted checkpoint, and only when the configured threshold is met. This avoids unbounded historical log-count scans. An alert fires the Home Assistant event **`loki_monitor_alert`** with rule/host/severity/count metadata; inclusion of a raw message is opt-in. No notification is sent unless you create a Home Assistant automation listening for that event.

```yaml
alias: Loki alert notification
triggers:
  - trigger: event
    event_type: loki_monitor_alert
actions:
  - action: notify.notify
    data:
      title: "Loki alert: {{ trigger.event.data.name }}"
      message: >-
        {{ trigger.event.data.count }} matching log entries in
        {{ trigger.event.data.window_minutes }} minutes.
mode: queued
```

Alerts are **best-effort**: they run at the polling interval, and an outage longer than a rule window can cause missed alerts. Repeated identical Loki entries may be deduplicated. Checkpoints are saved before events are emitted, preferring no duplicate replays to guaranteed delivery. Rules with text/regex necessarily query Loki, but the queries are bounded and scoped.

## Advanced one-shot queries

The `loki_monitor.query_logs` Home Assistant action remains available for administrators. It accepts a validated LogQL stream query and returns a bounded set of recent log lines (not an automatic monitoring sensor):

```yaml
action: loki_monitor.query_logs
data:
  query: '{host="CoreELEC",severity=~"^(emergency|alert|critical|fatal|error)$"}'
  minutes: 60
  limit: 20
response_variable: loki_results
```

Custom LogQL metric sensors, when explicitly enabled, can still run user-authored queries. Their performance is your responsibility; **automatic fleet/host/service severity counters always come from Prometheus**.

## Troubleshooting and security

- **Counts differ from Grafana:** compare the same 1h/24h interval, scrape job and label scope. Counters are first-sample compensated approximations; Grafana/Loki may deduplicate repeated raw lines. A 2-minute HA polling delay can also shift the evaluation window.
- **No counters:** verify HA can reach Prometheus, confirm the scrape job and query `count(homelab_log_service_events_total{job="homelab-alloy-logs"})`. Do not confuse Prometheus and Loki URLs.
- **Explorer empty:** inspect actual Loki labels, especially `host`, `source`, `service_name`, `syslog_identifier`, and `severity`. Only labels present on the stream can match indexed filters.
- **Card not recognized:** restart HA after updating, ensure resources use **JavaScript module**, avoid duplicate resource URLs and refresh the browser cache/version query string.
- **Old entities linger:** restart/reload the integration after update; dynamic obsolete entities are cleaned on setup. Update automations and Lovelace references to the new summary sensors.
- **No automatic Loki queries expected:** leave custom query polling disabled and do not configure alert rules. The explorer runs only when opened/refreshed. Explicit service action calls are user-initiated.
- **Security:** viewer WebSocket commands require admin permission. Raw log lines might contain sensitive information. The integration never sends credentials to the browser. Keep the Loki and Prometheus HTTP endpoints behind trusted LAN/VPN access; avoid embedding tokens in dashboard YAML.

## Development

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Check GitHub Actions **Validate** and the Home Assistant/HACS validators before publishing. For a tagged release, keep `custom_components/loki_monitor/manifest.json` version and Git tag aligned. See [PUBLISHING.md](PUBLISHING.md).

