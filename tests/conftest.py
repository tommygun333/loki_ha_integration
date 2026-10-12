"""Import pure API modules without requiring Home Assistant in this test environment."""

import sys
import types
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "custom_components" / "loki_monitor"
package = types.ModuleType("loki_monitor")
package.__path__ = [str(root)]
sys.modules.setdefault("loki_monitor", package)
