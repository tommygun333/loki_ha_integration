# Grafana Loki Monitor for Home Assistant

**Version 0.1.3 — HACS custom repository release**

A **read-only** Home Assistant integration for Grafana Loki. It queries Loki's HTTP API and creates monitoring entities and custom LogQL metric sensors. **Grafana Alloy remains responsible for collecting logs.** No duplicate ingestion is performed.

## Install or migrate to HACS

**Repository:** `tommygun333/loki_ha_integration` — `https://github.com/tommygun333/loki_ha_integration`

1. In Home Assistant open **HACS → ⋮ → Custom repositories**.
2. Paste `https://github.com/tommygun333/loki_ha_integration` and choose **Integration**, then Add.
3. Find **Grafana Loki Monitor** in HACS and select **Download**.
4. Restart Home Assistant Core.
5. If this is your first installation, navigate to **Settings → Devices & services → Add integration → Grafana Loki Monitor** and connect to the Loki API (not the Grafana frontend URL).

**Already installed manually?** Keep the existing `loki_monitor` config entry, sensors, and custom queries. **Do not delete the integration in Devices & services.** HACS installs the same files to `/config/custom_components/loki_monitor`, replacing the manual files, then you restart HA. Existing config entries use the unchanged `loki_monitor` domain and remain intact. Keep a HA backup before migration.

Updates: HACS displays new GitHub releases. Update in HACS then restart HA. New releases are published by the repository's tag-triggered GitHub Actions workflow.

**Before adding to HACS:** The repository owner must publish this repository on GitHub. A local ZIP by itself is **not** a working HACS custom repository.

## Features

- Loki connection UI: HTTP Basic, Bearer token or no authentication, optional X-Scope-OrgID tenant, SSL verification.
- Built-in 1h / 24h error and warning sensors; default `detected_level` queries match Grafana dashboard v1.5.
- Last error timestamp, observed host list, and binary connectivity/label diagnostics.
- User-defined numeric LogQL sensors (add/remove in Configure).
- `loki_monitor.query_logs` response-returning Home Assistant action for bounded, on-demand log retrieval.
- Polling 60–3600 seconds (default 120) and multiple Loki instances.

Not yet implemented: a native log viewer card, event/rule engine, live tailing, per-service sensors, or Grafana deep links. This is an early prototype; please report bugs in the repository issue tracker.

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

- **0.1.3:** HACS-ready repository metadata, documentation, CI, versioned GitHub release workflow. No behavioral changes to the Loki API or sensors.
- **0.1.2:** Grafana-aligned `detected_level` severity mode and fallback to indexed labels.
- **0.1.1:** Fixed a malformed translation placeholder in numeric LogQL sensor options.
- **0.1.0:** Initial read-only Loki integration with query sensors and on-demand logs.

MIT License.
