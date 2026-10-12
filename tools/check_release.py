"""Check packaging and optional Git tag against the Home Assistant version."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "custom_components/loki_monitor/manifest.json").read_text())
hacs = json.loads((root / "hacs.json").read_text())
assert manifest["domain"] == "loki_monitor"
assert manifest["name"] == "Grafana Loki Monitor"
assert manifest["version"] and "name" in hacs
assert (root / "custom_components/loki_monitor/__init__.py").is_file()
assert (root / "custom_components/loki_monitor/brand/icon.png").is_file()
assert manifest.get("issue_tracker", "").startswith("https://github.com/")
if len(sys.argv) > 1:
    tag = sys.argv[1]
    assert tag == "v" + manifest["version"], (tag, manifest["version"])
print("HACS repository layout and version OK:", manifest["version"])
