"""UI configuration and custom metric management for Loki Monitor."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import LokiAuthError, LokiClient, LokiError, LokiQueryError, validate_url
from .const import (
    AUTH_BASIC,
    AUTH_BEARER,
    AUTH_NONE,
    AUTH_TYPES,
    CONF_AUTH_TYPE,
    CONF_CUSTOM_QUERIES,
    CONF_COUNTER_BACKEND,
    CONF_PROMETHEUS_URL,
    CONF_ERROR_LEVELS,
    CONF_HOST_LABEL,
    CONF_ICON,
    CONF_LEVEL_LABEL,
    CONF_PASSWORD,
    CONF_POLL_SECONDS,
    CONF_QUERY,
    CONF_QUERY_NAME,
    CONF_SEVERITY_SOURCE,
    CONF_TENANT_ID,
    CONF_TOKEN,
    CONF_UNIT,
    CONF_URL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    CONF_WARN_LEVELS,
    DEFAULT_ERROR_LEVELS,
    DEFAULT_HOST_LABEL,
    DEFAULT_LEVEL_LABEL,
    DEFAULT_POLL_SECONDS,
    DEFAULT_SEVERITY_SOURCE,
    DEFAULT_WARN_LEVELS,
    DOMAIN,
    SEVERITY_SOURCE_DETECTED,
    SEVERITY_SOURCE_INDEXED,
    SEVERITY_SOURCE_CANONICAL,
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
)
from .logic import count_query, extract_number, level_selector, validate_label_name
from .prometheus import PrometheusClient, PrometheusError, validate_prometheus_url
from .alerts import (
    CONF_ALERT_RULES, MAX_ALERT_RULES, DEFAULT_RULE, RULE_FIELDS, clean_rule, test_alert_rule,
)
from .monitoring import (
    CONF_MONITORS, CONF_MONITOR_ID, CONF_MONITOR_NAME, CONF_MONITOR_HOST,
    CONF_MONITOR_SERVICE, MAX_MONITORS, SERVICE_LABEL, clean_monitor, monitor_selector,
)

_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


def connection_schema(data: dict[str, Any] | None = None) -> vol.Schema:
    """Connection form; unused authentication fields aren't persisted."""
    data = data or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=data.get(CONF_URL, "http://localhost:3100")): str,
            vol.Required(CONF_PROMETHEUS_URL, default=data.get(CONF_PROMETHEUS_URL, "")): str,
            vol.Required(CONF_AUTH_TYPE, default=data.get(CONF_AUTH_TYPE, AUTH_NONE)): vol.In(AUTH_TYPES),
            vol.Optional(CONF_USERNAME): str,
            vol.Optional(CONF_PASSWORD): _PASSWORD_SELECTOR,
            vol.Optional(CONF_TOKEN): _PASSWORD_SELECTOR,
            vol.Optional(CONF_TENANT_ID, default=data.get(CONF_TENANT_ID, "")): str,
            vol.Required(CONF_VERIFY_SSL, default=data.get(CONF_VERIFY_SSL, True)): bool,
        }
    )


def scrub_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and retain only fields needed for selected authentication."""
    cleaned = {
        CONF_URL: validate_url(data[CONF_URL]),
        CONF_PROMETHEUS_URL: validate_prometheus_url(data[CONF_PROMETHEUS_URL]),
        CONF_AUTH_TYPE: data[CONF_AUTH_TYPE],
        CONF_VERIFY_SSL: data[CONF_VERIFY_SSL],
    }
    if tenant := data.get(CONF_TENANT_ID, "").strip():
        cleaned[CONF_TENANT_ID] = tenant
    if data[CONF_AUTH_TYPE] == AUTH_BASIC:
        if not data.get(CONF_USERNAME) or not data.get(CONF_PASSWORD):
            raise ValueError("Missing basic credentials")
        cleaned[CONF_USERNAME] = data[CONF_USERNAME]
        cleaned[CONF_PASSWORD] = data[CONF_PASSWORD]
    elif data[CONF_AUTH_TYPE] == AUTH_BEARER:
        if not data.get(CONF_TOKEN):
            raise ValueError("Missing bearer token")
        cleaned[CONF_TOKEN] = data[CONF_TOKEN]
    return cleaned


async def _check_loki(hass, data: dict[str, Any]) -> str | None:
    """Return a UI error code for failed connection checks."""
    client = LokiClient(
        async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL]), data
    )
    try:
        await client.async_test_connection()
    except LokiAuthError:
        return "invalid_auth"
    except LokiQueryError:
        return "invalid_response"
    except LokiError:
        return "cannot_connect"
    try:
        await PrometheusClient(
            async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL]),
            data[CONF_PROMETHEUS_URL],
        ).async_test_connection()
    except (PrometheusError, ValueError):
        return "invalid_prometheus"
    return None


class LokiMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create and update a Loki connection."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = scrub_config(user_input)
            except ValueError:
                errors["base"] = "invalid_configuration"
            else:
                if any(
                    entry.data.get(CONF_URL) == settings[CONF_URL]
                    for entry in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                error = await _check_loki(self.hass, settings)
                if error:
                    errors["base"] = error
                else:
                    return self.async_create_entry(title=f"Loki ({settings[CONF_URL]})", data=settings)
        return self.async_show_form(
            step_id="user", data_schema=connection_schema(), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Credentials have stopped working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                settings = scrub_config(user_input)
            except ValueError:
                errors["base"] = "invalid_configuration"
            else:
                if settings[CONF_URL] != entry.data[CONF_URL]:
                    errors["base"] = "server_changed"
                elif error := await _check_loki(self.hass, settings):
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=settings)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=connection_schema(dict(entry.data)),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the Loki URL or credentials explicitly through Reconfigure."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                # Preserve secrets if user leaves optional secret fields empty.
                merged = dict(entry.data)
                merged.update({k: v for k, v in user_input.items() if v != ""})
                settings = scrub_config(merged)
            except ValueError:
                errors["base"] = "invalid_configuration"
            else:
                if any(
                    other.entry_id != entry.entry_id
                    and other.data.get(CONF_URL) == settings[CONF_URL]
                    for other in self._async_current_entries()
                ):
                    errors["base"] = "already_configured"
                elif error := await _check_loki(self.hass, settings):
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=settings)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=connection_schema(dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return LokiOptionsFlow()


class LokiOptionsFlow(config_entries.OptionsFlowWithReload):
    """Manage connection settings, metric queries and scoped monitoring."""

    def _client(self) -> LokiClient:
        return LokiClient(
            async_get_clientsession(
                self.hass, verify_ssl=self.config_entry.data.get(CONF_VERIFY_SSL, True)
            ),
            self.config_entry.data,
        )

    def _save(self, key: str, items: list[dict]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, key: items})

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        choices = ["settings", "add_metric", "test_metric", "add_monitor", "add_hosts", "add_alert", "test_alert"]
        if self.config_entry.options.get(CONF_ALERT_RULES):
            choices.extend(["select_alert", "remove_alert"])
        if self.config_entry.options.get(CONF_CUSTOM_QUERIES):
            choices.append("select_metric")
            choices.append("remove_query")
        if self.config_entry.options.get(CONF_MONITORS):
            choices.append("select_monitor")
            choices.append("remove_monitor")
        return self.async_show_menu(step_id="init", menu_options=choices)

    async def async_step_settings(self, user_input=None) -> ConfigFlowResult:
        """Update Prometheus address and Loki-only severity/search preferences."""
        opts = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validate_label_name(user_input[CONF_HOST_LABEL])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_ERROR_LEVELS])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_WARN_LEVELS])
            except (ValueError, KeyError):
                errors["base"] = "invalid_labels"
            else:
                try:
                    url = validate_prometheus_url(user_input.get(CONF_PROMETHEUS_URL, ""))
                    await PrometheusClient(
                        async_get_clientsession(
                            self.hass, verify_ssl=self.config_entry.data.get(CONF_VERIFY_SSL, True)
                        ), url,
                    ).async_test_connection()
                    user_input[CONF_PROMETHEUS_URL] = url
                except (PrometheusError, ValueError):
                    errors["base"] = "invalid_prometheus"
                else:
                    # Do not persist obsolete backend choice; all event counters
                    # now use Prometheus, irrespective of v0.5.0 options.
                    updated = {**opts, **user_input}
                    updated.pop(CONF_COUNTER_BACKEND, None)
                    return self.async_create_entry(data=updated)
        schema = vol.Schema({
            vol.Required(CONF_PROMETHEUS_URL, default=opts.get(
                CONF_PROMETHEUS_URL, self.config_entry.data.get(CONF_PROMETHEUS_URL, "")
            )): str,
            vol.Required(CONF_POLL_SECONDS, default=opts.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS)),
            vol.Required(CONF_SEVERITY_SOURCE, default=opts.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE)):
                vol.In({
                    SEVERITY_SOURCE_DETECTED: "Loki detected level (last-error search only)",
                    SEVERITY_SOURCE_INDEXED: "Indexed Loki label (last-error search only)",
                    SEVERITY_SOURCE_CANONICAL: "Canonical severity (last-error search only)",
                }),
            vol.Required(CONF_LEVEL_LABEL, default=opts.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL)): str,
            vol.Required(CONF_HOST_LABEL, default=opts.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)): str,
            vol.Required(CONF_ERROR_LEVELS, default=opts.get(CONF_ERROR_LEVELS, DEFAULT_ERROR_LEVELS)): str,
            vol.Required(CONF_WARN_LEVELS, default=opts.get(CONF_WARN_LEVELS, DEFAULT_WARN_LEVELS)): str,
        })
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    def _metric_schema(self, current: dict | None = None) -> vol.Schema:
        current = current or {}
        return vol.Schema({
            vol.Required(CONF_QUERY_NAME, default=current.get("name", "")): str,
            vol.Required(CONF_QUERY, default=current.get("query", "")):
                selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional(CONF_UNIT, default=current.get(CONF_UNIT, "")): str,
            vol.Optional(CONF_ICON, default=current.get(CONF_ICON, "")): str,
        })

    async def _validate_metric(self, query: str) -> str | None:
        try:
            result = await self._client().async_query(query)
            extract_number(result)
        except LokiAuthError:
            return "invalid_auth"
        except LokiError:
            return "invalid_query"
        except (ValueError, TypeError):
            return "not_numeric"
        return None

    async def async_step_add_metric(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            query = user_input[CONF_QUERY].strip()
            name = user_input[CONF_QUERY_NAME].strip()
            existing = list(self.config_entry.options.get(CONF_CUSTOM_QUERIES, []))
            if not query or not name or len(name) > 128 or any(
                item["name"].casefold() == name.casefold() for item in existing
            ):
                errors["base"] = "duplicate_or_empty"
            elif error := await self._validate_metric(query):
                errors["base"] = error
            else:
                new = {"id": uuid4().hex, "name": name, "query": query}
                for key in (CONF_UNIT, CONF_ICON):
                    if value := user_input.get(key, "").strip():
                        new[key] = value
                return self._save(CONF_CUSTOM_QUERIES, existing + [new])
        return self.async_show_form(
            step_id="add_metric", data_schema=self._metric_schema(user_input), errors=errors
        )

    async def async_step_select_metric(self, user_input=None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_CUSTOM_QUERIES, [])
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            self._editing_metric_id = user_input["query_id"]
            return await self.async_step_edit_metric()
        return self.async_show_form(step_id="select_metric", data_schema=vol.Schema({
            vol.Required("query_id"): vol.In({item["id"]: item["name"] for item in current})
        }))

    async def async_step_edit_metric(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_CUSTOM_QUERIES, []))
        item = next((q for q in current if q["id"] == getattr(self, "_editing_metric_id", None)), None)
        if item is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_QUERY_NAME].strip()
            query = user_input[CONF_QUERY].strip()
            if not name or not query or len(name) > 128 or any(
                q["id"] != item["id"] and q["name"].casefold() == name.casefold() for q in current
            ):
                errors["base"] = "duplicate_or_empty"
            elif error := await self._validate_metric(query):
                errors["base"] = error
            else:
                updated = {"id": item["id"], "name": name, "query": query}
                for key in (CONF_UNIT, CONF_ICON):
                    if value := user_input.get(key, "").strip():
                        updated[key] = value
                return self._save(CONF_CUSTOM_QUERIES, [
                    updated if q["id"] == item["id"] else q for q in current
                ])
        return self.async_show_form(
            step_id="edit_metric", data_schema=self._metric_schema(user_input or item), errors=errors
        )

    async def async_step_test_metric(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        result_display = "Not tested"
        if user_input is not None:
            query = user_input[CONF_QUERY].strip()
            if not query:
                errors["base"] = "invalid_query"
            else:
                try:
                    result = await self._client().async_query(query)
                    value = extract_number(result)
                except LokiAuthError:
                    errors["base"] = "invalid_auth"
                except LokiError:
                    errors["base"] = "invalid_query"
                except (TypeError, ValueError):
                    errors["base"] = "not_numeric"
                else:
                    result_display = "No data" if value is None else f"{value:g}"
        return self.async_show_form(
            step_id="test_metric",
            data_schema=vol.Schema({
                vol.Required(CONF_QUERY, default=(user_input or {}).get(CONF_QUERY, "vector(42)")):
                    selector.TextSelector(selector.TextSelectorConfig(multiline=True))
            }),
            errors=errors,
            description_placeholders={"result": result_display},
        )

    async def async_step_remove_query(self, user_input=None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_CUSTOM_QUERIES, [])
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            return self._save(CONF_CUSTOM_QUERIES, [
                item for item in current if item["id"] != user_input["query_id"]
            ])
        return self.async_show_form(step_id="remove_query", data_schema=vol.Schema({
            vol.Required("query_id"): vol.In({item["id"]: item["name"] for item in current})
        }))

    async def _monitor_schema(self, current: dict | None = None) -> vol.Schema:
        """Suggest Loki hosts and services without excluding inactive ones."""
        current = current or {}
        try:
            host_label = self.config_entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)
            hosts, services = await asyncio.gather(
                self._client().async_label_values(host_label, "24h"),
                self._client().async_label_values(SERVICE_LABEL, "24h"),
                return_exceptions=True,
            )
        except (LokiError, ValueError):
            hosts, services = [], []

        def field(values):
            if isinstance(values, list) and values:
                return selector.SelectSelector(selector.SelectSelectorConfig(
                    options=sorted(set(values))[:250],
                    mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True,
                ))
            return str

        host_marker = (
            vol.Required(CONF_MONITOR_HOST, default=current["host"])
            if current.get("host") else vol.Required(CONF_MONITOR_HOST)
        )
        service_marker = (
            vol.Optional(CONF_MONITOR_SERVICE, default=current["service"])
            if current.get("service") else vol.Optional(CONF_MONITOR_SERVICE)
        )
        return vol.Schema({
            vol.Required(CONF_MONITOR_NAME, default=current.get("name", "")): str,
            host_marker: field(hosts),
            service_marker: field(services),
        })

    async def _validate_monitor(self, monitor: dict) -> str | None:
        """Validate a selector without issuing expensive Loki count queries.

        A service can legitimately have no samples in the current Prometheus
        window, so it is accepted without a forced nonzero query.
        """
        try:
            clean_monitor(monitor["name"], monitor["host"], monitor.get("service", ""))
        except (KeyError, TypeError, ValueError):
            return "invalid_monitor"
        return None

    async def async_step_add_monitor(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        monitors = list(self.config_entry.options.get(CONF_MONITORS, []))
        if user_input is not None:
            try:
                monitor = clean_monitor(
                    user_input[CONF_MONITOR_NAME], user_input[CONF_MONITOR_HOST],
                    user_input.get(CONF_MONITOR_SERVICE, ""),
                )
            except (KeyError, ValueError):
                errors["base"] = "invalid_monitor"
            else:
                if len(monitors) >= MAX_MONITORS:
                    errors["base"] = "monitor_limit"
                elif any(
                    m["name"].casefold() == monitor["name"].casefold()
                    or (m["host"], m.get("service", "")) == (monitor["host"], monitor["service"])
                    for m in monitors
                ):
                    errors["base"] = "duplicate_monitor"
                elif error := await self._validate_monitor(monitor):
                    errors["base"] = error
                else:
                    return self._save(CONF_MONITORS, monitors + [{"id": uuid4().hex, **monitor}])
        return self.async_show_form(
            step_id="add_monitor", data_schema=await self._monitor_schema(user_input), errors=errors
        )

    async def async_step_add_hosts(self, user_input=None) -> ConfigFlowResult:
        """Add discovered host-only monitors in a single options commit/reload."""
        current = list(self.config_entry.options.get(CONF_MONITORS, []))
        monitored = {m["host"] for m in current if not m.get("service")}
        errors: dict[str, str] = {}
        try:
            names = await self._client().async_label_values(
                self.config_entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL), "30d"
            )
            available = sorted({h for h in names if isinstance(h, str) and h.strip()
                                and len(h) <= 128 and h not in monitored})
        except (LokiError, ValueError):
            available = []

        if user_input is not None:
            requested = user_input.get("monitor_hosts", [])
            if not isinstance(requested, list) or not requested or any(
                not isinstance(h, str) or h not in available for h in requested
            ):
                errors["base"] = "invalid_monitor"
            else:
                chosen = sorted(set(requested))
                if len(current) + len(chosen) > MAX_MONITORS:
                    errors["base"] = "monitor_limit"
                else:
                    taken_names = {m["name"].casefold() for m in current}
                    new_monitors = []
                    for host in chosen:
                        name = host
                        if name.casefold() in taken_names:
                            name = f"{host} (host)"
                        suffix = 2
                        while name.casefold() in taken_names:
                            name = f"{host} ({suffix})"
                            suffix += 1
                        try:
                            monitor = clean_monitor(name, host)
                        except ValueError:
                            errors["base"] = "invalid_monitor"
                            break
                        # No expensive LogQL request per host; values come from
                        # Loki label discovery and the monitored host is quoted.
                        taken_names.add(name.casefold())
                        new_monitors.append({"id": uuid4().hex, **monitor})
                    if not errors:
                        return self._save(CONF_MONITORS, current + new_monitors)
        if not available and user_input is None:
            errors["base"] = "no_hosts"
        return self.async_show_form(
            step_id="add_hosts",
            data_schema=vol.Schema({
                vol.Required("monitor_hosts"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=available[:250],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }),
            errors=errors,
        )

    async def async_step_select_monitor(self, user_input=None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_MONITORS, [])
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            self._editing_monitor_id = user_input[CONF_MONITOR_ID]
            return await self.async_step_edit_monitor()
        return self.async_show_form(step_id="select_monitor", data_schema=vol.Schema({
            vol.Required(CONF_MONITOR_ID): vol.In({m["id"]: m["name"] for m in current})
        }))

    async def async_step_edit_monitor(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_MONITORS, []))
        old = next((m for m in current if m["id"] == getattr(self, "_editing_monitor_id", None)), None)
        if old is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                monitor = clean_monitor(
                    user_input[CONF_MONITOR_NAME], user_input[CONF_MONITOR_HOST],
                    user_input.get(CONF_MONITOR_SERVICE, ""),
                )
            except (KeyError, ValueError):
                errors["base"] = "invalid_monitor"
            else:
                if any(m["id"] != old["id"] and (
                    m["name"].casefold() == monitor["name"].casefold()
                    or (m["host"], m.get("service", "")) == (monitor["host"], monitor["service"])
                ) for m in current):
                    errors["base"] = "duplicate_monitor"
                elif error := await self._validate_monitor(monitor):
                    errors["base"] = error
                else:
                    return self._save(CONF_MONITORS, [
                        {"id": old["id"], **monitor} if m["id"] == old["id"] else m
                        for m in current
                    ])
        return self.async_show_form(
            step_id="edit_monitor", data_schema=await self._monitor_schema(user_input or old),
            errors=errors,
        )

    async def async_step_remove_monitor(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_MONITORS, []))
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            return self._save(CONF_MONITORS, [
                m for m in current if m["id"] != user_input[CONF_MONITOR_ID]
            ])
        return self.async_show_form(step_id="remove_monitor", data_schema=vol.Schema({
            vol.Required(CONF_MONITOR_ID): vol.In({m["id"]: m["name"] for m in current})
        }))

    def _alert_schema(self, current: dict | None = None) -> vol.Schema:
        current = {**DEFAULT_RULE, **(current or {})}
        fields = {
            vol.Required("name", default=current.get("name", "")): str,
            vol.Required("enabled", default=current["enabled"]): bool,
            vol.Optional("host", default=current["host"]): str,
            vol.Optional("source", default=current["source"]): str,
            vol.Optional("service", default=current["service"]): str,
            vol.Optional("program", default=current["program"]): str,
            vol.Required("severity", default=current["severity"]): vol.In({
                "all": "All levels", "errors": "High severity (emergency to error)",
                "warnings": "Warnings", "emergency": "Emergency", "alert": "Alert",
                "critical": "Critical", "fatal": "Fatal", "error": "Error", "warning": "Warning",
                "notice": "Notice", "info": "Info", "debug": "Debug", "trace": "Trace", "unknown": "Unknown",
            }),
            vol.Optional("text", default=current["text"]): str,
            vol.Optional("regex", default=current["regex"]): str,
            vol.Required("threshold", default=current["threshold"]):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=10000)),
            vol.Required("window_minutes", default=current["window_minutes"]):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
            vol.Required("cooldown_minutes", default=current["cooldown_minutes"]):
                vol.All(vol.Coerce(int), vol.Range(min=0, max=10080)),
            vol.Required("include_message", default=current["include_message"]): bool,
        }
        return vol.Schema(fields)

    async def async_step_add_alert(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_ALERT_RULES, []))
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                new = clean_rule(user_input)
                if len(current) >= MAX_ALERT_RULES or any(
                    item["name"].casefold() == new["name"].casefold() for item in current
                ):
                    errors["base"] = "duplicate_or_limit_alert"
                else:
                    return self._save(CONF_ALERT_RULES, current + [{"id": uuid4().hex, **new}])
            except (ValueError, TypeError):
                errors["base"] = "invalid_alert"
        return self.async_show_form(
            step_id="add_alert", data_schema=self._alert_schema(user_input), errors=errors
        )

    async def async_step_select_alert(self, user_input=None) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_ALERT_RULES, [])
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            self._editing_alert_id = user_input["rule_id"]
            return await self.async_step_edit_alert()
        return self.async_show_form(step_id="select_alert", data_schema=vol.Schema({
            vol.Required("rule_id"): vol.In({r["id"]: r["name"] for r in current})
        }))

    async def async_step_edit_alert(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_ALERT_RULES, []))
        previous = next((r for r in current if r["id"] == getattr(self, "_editing_alert_id", None)), None)
        if previous is None:
            return await self.async_step_init()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated = clean_rule(user_input)
                if any(r["id"] != previous["id"] and r["name"].casefold() == updated["name"].casefold()
                       for r in current):
                    errors["base"] = "duplicate_or_limit_alert"
                else:
                    return self._save(CONF_ALERT_RULES, [
                        {"id": previous["id"], **updated} if r["id"] == previous["id"] else r
                        for r in current
                    ])
            except (TypeError, ValueError):
                errors["base"] = "invalid_alert"
        return self.async_show_form(step_id="edit_alert", data_schema=self._alert_schema(user_input or previous), errors=errors)

    async def async_step_remove_alert(self, user_input=None) -> ConfigFlowResult:
        current = list(self.config_entry.options.get(CONF_ALERT_RULES, []))
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            return self._save(CONF_ALERT_RULES, [r for r in current if r["id"] != user_input["rule_id"]])
        return self.async_show_form(step_id="remove_alert", data_schema=vol.Schema({
            vol.Required("rule_id"): vol.In({r["id"]: r["name"] for r in current})
        }))

    async def async_step_test_alert(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        count_text = "Not tested"
        if user_input is not None:
            try:
                rule = clean_rule(user_input)
                count = await test_alert_rule(
                    self._client(), rule,
                    self.config_entry.options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL),
                )
                count_text = str(int(count))
            except LokiAuthError:
                errors["base"] = "invalid_auth"
            except (LokiError, TypeError, ValueError):
                errors["base"] = "invalid_alert"
        return self.async_show_form(
            step_id="test_alert", data_schema=self._alert_schema(user_input),
            errors=errors, description_placeholders={"count": count_text},
        )
