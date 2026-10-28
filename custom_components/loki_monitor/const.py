"""Constants for Loki Monitor."""

from datetime import timedelta

DOMAIN = "loki_monitor"
PLATFORMS = ("sensor", "binary_sensor")
DEFAULT_POLL_SECONDS = 120
MIN_POLL_SECONDS = 60
MAX_POLL_SECONDS = 3600
DEFAULT_HOST_LABEL = "host"
DEFAULT_LEVEL_LABEL = "level"
DEFAULT_ERROR_LEVELS = "error,err,critical,fatal"
DEFAULT_WARN_LEVELS = "warn,warning"
CONF_URL = "url"
CONF_AUTH_TYPE = "auth_type"
CONF_TOKEN = "token"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"
CONF_TENANT_ID = "tenant_id"
CONF_POLL_SECONDS = "poll_seconds"
CONF_HOST_LABEL = "host_label"
CONF_LEVEL_LABEL = "level_label"
CONF_SEVERITY_SOURCE = "severity_source"
SEVERITY_SOURCE_DETECTED = "detected"
SEVERITY_SOURCE_INDEXED = "indexed"
SEVERITY_SOURCE_CANONICAL = "canonical"
DEFAULT_SEVERITY_SOURCE = SEVERITY_SOURCE_DETECTED
CONF_ERROR_LEVELS = "error_levels"
CONF_WARN_LEVELS = "warn_levels"
CONF_CUSTOM_QUERIES = "custom_queries"
CONF_COUNTER_BACKEND = "counter_backend"
CONF_PROMETHEUS_URL = "prometheus_url"
COUNTER_BACKEND_LOKI = "loki"
COUNTER_BACKEND_PROMETHEUS = "prometheus"
CONF_QUERY_NAME = "query_name"
CONF_QUERY = "query"
CONF_UNIT = "unit"
CONF_ICON = "icon"
AUTH_NONE = "none"
AUTH_BASIC = "basic"
AUTH_BEARER = "bearer"
AUTH_TYPES = (AUTH_NONE, AUTH_BASIC, AUTH_BEARER)
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=DEFAULT_POLL_SECONDS)
