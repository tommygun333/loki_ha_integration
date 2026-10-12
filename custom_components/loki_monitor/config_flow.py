"""UI configuration and custom metric management for Loki Monitor."""

from __future__ import annotations

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
from .logic import extract_number, level_selector, validate_label_name

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
    """Tuning settings and adding/removing individual LogQL metric sensors."""

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        choices = ["settings", "add_metric"]
        if self.config_entry.options.get(CONF_CUSTOM_QUERIES):
            choices.append("remove_query")
        return self.async_show_menu(step_id="init", menu_options=choices)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                validate_label_name(user_input[CONF_HOST_LABEL])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_ERROR_LEVELS])
                level_selector(user_input[CONF_LEVEL_LABEL], user_input[CONF_WARN_LEVELS])
            except ValueError:
                errors["base"] = "invalid_labels"
            else:
                return self.async_create_entry(
                    data={**self.config_entry.options, **user_input}
                )
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_POLL_SECONDS, default=opts.get(CONF_POLL_SECONDS, DEFAULT_POLL_SECONDS)): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_POLL_SECONDS, max=MAX_POLL_SECONDS)
                ),
                vol.Required(CONF_SEVERITY_SOURCE, default=opts.get(CONF_SEVERITY_SOURCE, DEFAULT_SEVERITY_SOURCE)): vol.In(
                    {
                        SEVERITY_SOURCE_DETECTED: "Grafana Loki detected severity (dashboard-compatible)",
                        SEVERITY_SOURCE_INDEXED: "Indexed level label (previous behavior)",
                    }
                ),
                vol.Required(CONF_LEVEL_LABEL, default=opts.get(CONF_LEVEL_LABEL, DEFAULT_LEVEL_LABEL)): str,
                vol.Required(CONF_HOST_LABEL, default=opts.get(CONF_HOST_LABEL, DEFAULT_HOST_LABEL)): str,
                vol.Required(CONF_ERROR_LEVELS, default=opts.get(CONF_ERROR_LEVELS, DEFAULT_ERROR_LEVELS)): str,
                vol.Required(CONF_WARN_LEVELS, default=opts.get(CONF_WARN_LEVELS, DEFAULT_WARN_LEVELS)): str,
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    async def async_step_add_metric(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            query = user_input[CONF_QUERY].strip()
            name = user_input[CONF_QUERY_NAME].strip()
            existing = list(self.config_entry.options.get(CONF_CUSTOM_QUERIES, []))
            if not query or not name or any(item["name"].casefold() == name.casefold() for item in existing):
                errors["base"] = "duplicate_or_empty"
            else:
                client = LokiClient(
                    async_get_clientsession(
                        self.hass,
                        verify_ssl=self.config_entry.data.get(CONF_VERIFY_SSL, True),
                    ),
                    self.config_entry.data,
                )
                try:
                    result = await client.async_query(query)
                    extract_number(result)  # No data is acceptable, multiple series aren't.
                except LokiAuthError:
                    errors["base"] = "invalid_auth"
                except LokiError:
                    errors["base"] = "invalid_query"
                except (ValueError, TypeError):
                    errors["base"] = "not_numeric"
                else:
                    new_query = {"id": uuid4().hex, "name": name, "query": query}
                    for key in (CONF_UNIT, CONF_ICON):
                        if user_input.get(key, "").strip():
                            new_query[key] = user_input[key].strip()
                    existing.append(new_query)
                    return self.async_create_entry(
                        data={**self.config_entry.options, CONF_CUSTOM_QUERIES: existing}
                    )
        return self.async_show_form(
            step_id="add_metric",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_QUERY_NAME): str,
                    vol.Required(CONF_QUERY): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Optional(CONF_UNIT): str,
                    vol.Optional(CONF_ICON): str,
                }
            ),
            errors=errors,
        )

    async def async_step_remove_query(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        current = self.config_entry.options.get(CONF_CUSTOM_QUERIES, [])
        if not current:
            return await self.async_step_init()
        if user_input is not None:
            remaining = [item for item in current if item["id"] != user_input["query_id"]]
            return self.async_create_entry(
                data={**self.config_entry.options, CONF_CUSTOM_QUERIES: remaining}
            )
        return self.async_show_form(
            step_id="remove_query",
            data_schema=vol.Schema(
                {vol.Required("query_id"): vol.In({item["id"]: item["name"] for item in current})}
            ),
        )
