/* Browser-independent regression tests for Home Assistant's frequent hass updates. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const src = fs.readFileSync('custom_components/loki_monitor/frontend/loki-monitor-cards.js', 'utf8');
const registry = new Map();
class FakeElement {
  constructor(tagName = '') {
    this.tagName = tagName;
    this.attrs = {};
    this.children = [];
    this.listeners = new Map();
    this.value = '';
    this.textContent = '';
    this.hidden = false;
  }
  addEventListener(event, fn) { this.listeners.set(event, fn); }
  change() { this.listeners.get('change')?.({target: this}); }
  click() { this.listeners.get('click')?.({target: this}); }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = [...elements]; }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelector(sel) { return this.nodes?.get(sel) || null; }
  set innerHTML(html) {
    this.html = html;
    this.rebuilds = (this.rebuilds || 0) + 1;
    this.nodes = new Map();
    for(const sel of ['#window', '#exact-controls', '#exact-from', '#exact-to',
      '#apply-exact', '#full-explorer', '#refresh-preset', '#exact-message', 'tbody']) {
      this.nodes.set(sel, new FakeElement(sel));
    }
  }
}
class FakeHTMLElement {
  attachShadow() { this.shadowRoot = new FakeElement('shadow-root'); return this.shadowRoot; }
}
const ctx = {
  HTMLElement: FakeHTMLElement,
  document: {createElement: tag => new FakeElement(tag)},
  window: {customCards: [], addEventListener: () => {}},
  customElements: {get: n => registry.get(n), define: (n, c) => registry.set(n, c)},
  BigInt, Date, console,
};
vm.runInNewContext(src, ctx);
const Overview = registry.get('loki-monitor-overview-card');
assert.ok(Overview);
const card = new Overview();
card.setConfig({type: 'custom:loki-monitor-overview-card', minutes:60});
const requests = [];
const connection = {sendMessagePromise: async msg => {
  requests.push(msg);
  return {fleet: {high: 2, warning: 3}, monitors: [{host: 'CoreELEC', service_name: '', high: 1, warning: 1}]};
}};
const states = {
  'sensor.fleet_summary': {entity_id:'sensor.fleet_summary', state:'1', attributes: {
    monitor_type:'fleet', high_1h:1, high_24h:5, warning_1h:2, warning_24h:6, loki_entry_id:'abc'}},
  'sensor.coreelec_summary': {entity_id:'sensor.coreelec_summary', state:'1', attributes: {
    monitor_type:'host', host:'CoreELEC', high_1h:1, high_24h:4,
    warning_1h:1, warning_24h:2, loki_entry_id:'abc'}},
};
const hass = (newStates = states) => ({states: newStates, connection});
(async () => {
  card.hass = hass();
  const root = card.shadowRoot;
  const dropdown = root.querySelector('#window');
  assert.equal(root.rebuilds, 1);
  assert.equal(dropdown.value, '60');
  const firstTbody = root.querySelector('tbody');
  assert.equal(firstTbody.children.length, 2);

  // Frequent Home Assistant updates must NOT destroy the select or re-create rows.
  for(let i=0;i<50;i++) card.hass = hass();
  assert.equal(root.rebuilds, 1);
  assert.equal(root.querySelector('#window'), dropdown);
  assert.equal(root.querySelector('tbody'), firstTbody);
  assert.equal(firstTbody.children.length, 2);
  // Relevant counts may change; updating rows still must not recreate the select.
  const changed = {...states,
    'sensor.coreelec_summary': {...states['sensor.coreelec_summary'],
      attributes: {...states['sensor.coreelec_summary'].attributes, high_1h: 9}}};
  card.hass = hass(changed);
  assert.equal(root.rebuilds, 1);
  assert.equal(root.querySelector('#window'), dropdown);
  assert.equal(root.querySelector('tbody').children.length, 2);
  card.hass = hass();

  // A user-selected 3h preset triggers precisely one on-demand Prometheus request.
  dropdown.value = '180'; dropdown.change();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].type, 'loki_monitor/range_counts');
  const interval = (BigInt(requests[0].range_end_ns) - BigInt(requests[0].range_start_ns))/60000000000n;
  assert.equal(interval, 180n);
  assert.equal(root.rebuilds, 1);
  assert.equal(dropdown.value, '180');
  for(let i=0;i<50;i++) card.hass = hass();
  assert.equal(requests.length, 1);
  assert.equal(root.rebuilds, 1);
  assert.equal(dropdown.value, '180');

  // Additional presets do not require backend changes; only intentional selections fetch.
  for(const [duration,label] of [[360,'6 hours'],[720,'12 hours'],[2880,'2 days'],[10080,'7 days']]) {
    assert.ok(src.includes(`<option value="${duration}">${label}</option>`));
  }
  dropdown.value = '720'; dropdown.change();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(requests.length, 2);
  assert.equal((BigInt(requests[1].range_end_ns) - BigInt(requests[1].range_start_ns))/60000000000n, 720n);

  // Built-in 24h/1h values read existing HA entities; never fetch historical range.
  assert.equal(root.querySelector('#refresh-preset').hidden, false);
  root.querySelector('#refresh-preset').click();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(requests.length, 3);
  dropdown.value = '1440'; dropdown.change();
  assert.equal(requests.length, 3);
  assert.equal(root.querySelector('#refresh-preset').hidden, true);
  assert.equal(root.rebuilds, 1);

  // Input values survive repeated HA updates, and editing doesn't rebuild inputs.
  dropdown.value = 'custom'; dropdown.change();
  const from = root.querySelector('#exact-from');
  from.value = '2026-10-28T16:00:25';from.change();
  for(let i=0;i<20;i++) card.hass = hass();
  assert.equal(root.rebuilds, 1);
  assert.equal(root.querySelector('#exact-from'), from);
  assert.equal(from.value, '2026-10-28T16:00:25');
  console.log('Overview dropdown stability + historical preset smoke passed');
})().catch(err=>{console.error(err); process.exitCode = 1;});
