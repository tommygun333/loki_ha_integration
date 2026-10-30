# Fleet summary dashboard template repair (v0.6.3)

The error `ReadOnlyDict object has no attribute 'monitor_type'` originates
from a **dashboard Jinja template**, not from a Python integration component.
`states.sensor` contains many sensors without `attributes.monitor_type` and
`attributes.loki_entry_id`. Plain `selectattr(..., 'eq', ...)` fails on missing
attributes in Home Assistant's Jinja environment.

**Manual dashboard change required:** Replace the **existing** command-centre
fleet summary template with the complete contents of
[`fleet_summary_safe_template.jinja`](fleet_summary_safe_template.jinja).
That example adds `selectattr(..., 'defined')` guards before each equality
comparison and returns zero if the fleet entry ID is unavailable.

Applying a Git patch or installing this HACS update **does not automatically
modify Lovelace dashboard YAML or Browser Mod popup configuration**. If you
maintain the dashboard in a separate repository, also patch that copy.

The overview JS change applies a fixed table layout and consistent High /
Warnings column alignment, with compact numeric and View columns. This is
a frontend-only change; neither counters nor the popup width are changed.
After updating the integration and restarting HA, update the existing dashboard
module resource (do not add a duplicate) to:

```
/loki_monitor/loki-monitor-cards.js?v=0.6.3
```

Refresh the browser; keep the separate log viewer resource unchanged.
