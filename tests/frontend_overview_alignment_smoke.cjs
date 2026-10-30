/* Guard the Overview numeric column layout against header/number drift. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const js = fs.readFileSync('custom_components/loki_monitor/frontend/loki-monitor-cards.js', 'utf8');
const htmlStart = js.indexOf('class LokiMonitorOverviewCard');
const css = js.slice(htmlStart, js.indexOf('</style><ha-card>', htmlStart));
assert.ok(css.includes('table-layout:fixed'), 'Overview must use stable table columns');
for (const nth of [2, 3]) {
  const rule = new RegExp(`th:nth-child\\(${nth}\\),td:nth-child\\(${nth}\\)\\{width:[^;]+;text-align:right\\}`);
  assert.match(css, rule, `Header and numeric cells in column ${nth} must align`);
}
assert.match(css, /th:nth-child\(4\),td:nth-child\(4\)\{width:[^;]+;text-align:center\}/);
assert.ok(css.includes('th:first-child,td:first-child{overflow-wrap:anywhere}'));
assert.ok(js.includes('high.className = "numeric"'));
assert.ok(js.includes('warnings.className = "numeric"'));
console.log('Overview alignment static regression passed');
