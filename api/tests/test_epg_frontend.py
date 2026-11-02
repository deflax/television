import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for isolated frontend consumer tests')
class WeeklyEpgFrontendTest(unittest.TestCase):
    def test_weekly_utc_guide_and_manual_playhead_labels(self):
        scripts = Path(__file__).resolve().parents[1] / 'static' / 'js'
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.text = ''; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.text = ''; this.children = []; }
  addEventListener() {}
}
const elements = Object.fromEntries(['epg-now-playing', 'epg-now-name', 'epg-schedule', 'visitor-number']
  .map(id => [id, new Element('div')]));
const document = {
  getElementById: id => elements[id],
  createElement: tag => new Element(tag),
  createTextNode: text => ({textContent: text}),
};
class FixedDate extends Date {
  static now() { return Date.parse('2026-11-01T23:30:00Z'); }
}
let source;
class EventSource {
  constructor() { this.listeners = {}; source = this; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
}
const context = vm.createContext({window: {}, document, Date: FixedDate, Intl, EventSource, console});
for (const filename of ['epg.js', 'sse.js']) {
  vm.runInContext(fs.readFileSync(path.join(process.argv[1], filename), 'utf8'), context);
}
const guide = {
  start_day: '2026-11-01', days: 7,
  catalogue: {
    offline: {name: 'Offline show'},
    manual: {name: 'Manual excluded-day stream'},
  },
  programmes: [{
    id: 'offline', start: '2026-11-02T12:00:00+00:00', stop: '2026-11-03T00:00:00+00:00',
    title: 'Offline <show>', desc: 'Episode & details', prio: 0,
  }],
};
source.listeners.playhead({data: JSON.stringify({id: 'manual', metadata: {}})});
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(elements['epg-now-name'].textContent, 'Manual excluded-day stream');
assert.equal(elements['epg-now-playing'].style.display, 'block');
const sections = elements['epg-schedule'].children;
assert.equal(sections.length, 7);
assert.match(sections[0].children[0].textContent, /Sun.*Nov.*1.*2026.*UTC/);
assert.match(sections[6].children[0].textContent, /Sat.*Nov.*7.*2026.*UTC/);
assert.match(sections[0].textContent, /No scheduled streams/);
assert.match(sections[1].textContent, /12:00–24:00 Offline <show>/);
assert.match(sections[1].textContent, /Episode & details/);
assert.doesNotMatch(elements['epg-schedule'].textContent, /Manual excluded-day stream/);
assert.equal(sections[1].children[1].children[0].children[1].textContent, ' Offline <show>');
source.listeners.playhead({data: JSON.stringify({id: 'manual', metadata: {stream_title: 'Current track'}})});
assert.equal(elements['epg-now-name'].textContent, 'Current track');
// A single scheduled channel still has a visible seven-date guide.
delete guide.catalogue.manual;
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(elements['epg-schedule'].children.length, 7);
assert.match(elements['epg-schedule'].textContent, /Offline <show>/);
"""
        result = subprocess.run(['node', '-e', script, str(scripts)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
