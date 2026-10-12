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
    MAX_POLL_SECONDS,
    MIN_POLL_SECONDS,
)
from .logic import count_query, extract_number, level_selector, validate_label_name
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
        choices = ["settings", "add_metric", "test_metric", "add_monitor"]
        if self.config_entry.options.get(CONF_CUSTOM_QUERIES):
            choices.append("select_metric")
            choices.append("remove_query")
        if self.config_entry.options.get(CONF_MONITORS):
            choices.append("select_monitor")
            choices.append("remove_monitor")
        return self.async_show_menu(step_id="init", menu_options=choices)

    async def async_step_settings(self, user_input=None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validate_label_name(user_input[CONF_HOST_LABEL])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_ERROR_LEVELS])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_WARN_LEVELS])
            except ValueError:
                errors["base"] = "invalid_labels"
            else:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})
        opts = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_POLL_SECONDS, default=opts.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS)),
            vol.Required(CONF_SEVERITY_SOURCE, default=opts.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE)):
                vol.In({
                    SEVERITY_SOURCE_DETECTED: "Grafana Loki detected severity (dashboard-compatible)",
                    SEVERITY_SOURCE_INDEXED: "Indexed level label (previous behavior)",
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
        """Ensure monitor LogQL parses before saving it."""
        options = self.config_entry.options
        try:
            scoped = monitor_selector(
                monitor,
                host_label=options.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL),
                severity_source=options.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE),
                level_label=options.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL),
                severity_values=options.get(CONF_WARN_LEVELS, DEFAULT_WARN_LEVELS),
                severity="warn",
            )
            extract_number(await self._client().async_query(count_query(scoped, "1h")))
        except LokiAuthError:
            return "invalid_auth"
        except (LokiError, ValueError, TypeError):
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
