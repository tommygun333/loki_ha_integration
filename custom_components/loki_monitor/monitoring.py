"""Validated monitor definitions and lightweight indexed stream selectors."""
from __future__ import annotations
import json
from .logic import validate_label_name

MAX_MONITORS = 64
CONF_MONITORS = 'monitors'
CONF_MONITOR_ID = 'monitor_id'
CONF_MONITOR_NAME = 'monitor_name'
CONF_MONITOR_HOST = 'monitor_host'
CONF_MONITOR_SERVICE = 'monitor_service'
SERVICE_LABEL = 'service_name'


def clean_monitor(name: str, host: str, service: str = '') -> dict[str, str]:
    if any('\n' in value or '\r' in value for value in (name, host, service)):
        raise ValueError('Monitor values must not contain newlines')
    name, host, service = name.strip(), host.strip(), service.strip()
    if not name or not host or any(len(value) > 128 for value in (name, host, service)):
        raise ValueError('Monitor name and host are required (max 128 characters)')
    if any('\n' in value or '\r' in value for value in (name,host,service)):
        raise ValueError('Monitor values must not contain newlines')
    return {'name':name,'host':host,'service':service}


def monitor_selector(monitor: dict[str,str], *,host_label: str='host',severity: str='error',**_unused) -> str:
    """Only indexed Alloy severity; used for validation and narrow investigations."""
    host_label=validate_label_name(host_label)
    values=[f'{host_label}={json.dumps(monitor["host"])}']
    if monitor.get('service'):
        values.append(f'{SERVICE_LABEL}={json.dumps(monitor["service"])}')
    if severity == 'error':
        values.append('severity=~"^(emergency|alert|critical|fatal|error)$"')
    elif severity == 'warn':
        values.append('severity="warning"')
    else:
        raise ValueError('Unsupported severity')
    return '{'+','.join(values)+'}'
