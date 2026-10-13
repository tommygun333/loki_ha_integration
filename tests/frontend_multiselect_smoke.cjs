/* Browser-independent regression smoke tests for the Lovelace multiselect logic. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const src = fs.readFileSync('custom_components/loki_monitor/frontend/loki-log-viewer-card.js','utf8');
const registered = new Map();
const context = {
  HTMLElement: class {},
  customElements: {get:name=>registered.get(name), define:(name,cls)=>registered.set(name,cls)},
  window: {customCards:[]},
  URL,
};
vm.runInNewContext(src, context);
const Card = registered.get('loki-log-viewer-card');
assert.ok(Card);
const card = new Card();
assert.deepEqual(Array.from(card._initialSelection('CoreELEC')), ['CoreELEC']);
assert.deepEqual(Array.from(card._initialSelection(['CoreELEC','pve','CoreELEC'])), ['CoreELEC','pve']);
assert.equal(card._initialSelection(Array(30).fill('x').map((x,i)=>x+i)).length,24);
card._filters = {host:['CoreELEC'], source:['journal'], service:['kodi'], program:['systemd']};
let reloads = 0;
card._cascadingFilters = true;
card._loadSuggestions = async () => {reloads++;};
card._updateSelectionButtons = () => {};
let closes = 0;
card.shadowRoot = {getElementById:()=>({close:()=>{closes++;}})};
card._picker = {key:'host', selected:new Set(['CoreELEC','pve'])};
(async()=>{
  await card._applyPicker();
  assert.deepEqual(Array.from(card._filters.host),['CoreELEC','pve']);
  for(const key of ['source','service','program']) assert.equal(card._filters[key].length,0);
  assert.equal(reloads,1);
  card._filters.service=['kodi'];
  card._picker={key:'service',selected:new Set(['kodi'])};
  await card._applyPicker();
  assert.equal(reloads,1); // no cascade on unchanged values
  assert.equal(closes,2);
  console.log('frontend multi-select smoke OK');
})().catch(err=>{console.error(err);process.exitCode=1;});
