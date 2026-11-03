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
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.text = ''; this.listeners = {}; this.attributes = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.text = ''; this.children = []; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
}
const elements = Object.fromEntries(['epg-now-playing', 'epg-now-name', 'epg-schedule',
  'epg-day-buttons', 'epg-day-controls', 'epg-day-programmes', 'visitor-number']
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
const buttons = elements['epg-day-buttons'];
const programmes = elements['epg-day-programmes'];
const selectedButton = () => buttons.children.find(button => button.attributes['aria-pressed'] === 'true');
const visiblePage = () => programmes.children.find(page => page.attributes['aria-hidden'] === 'false');
const currentDay = () => selectedButton().dataset.day;
const chooseDay = day => buttons.children.find(button => button.dataset.day === day).listeners.click();
assert.equal(currentDay(), guide.start_day);
assert.deepEqual(buttons.children.map(button => button.textContent),
  ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
assert.equal(selectedButton().textContent, 'Sun');
assert.equal(elements['epg-day-controls'].hidden, false);
assert.doesNotMatch(visiblePage().textContent, /Offline <show>/);
assert.equal(programmes.children.filter(page => !page.inert).length, 1);
assert.equal(visiblePage().inert, false);
chooseDay('2026-11-02');
assert.equal(selectedButton().textContent, 'Mon');
assert.match(visiblePage().textContent, /12:00–24:00 Offline <show>/);
assert.match(visiblePage().textContent, /Episode & details/);
assert.doesNotMatch(visiblePage().textContent, /Manual excluded-day stream/);
assert.equal(visiblePage().children[0].children[0].children[1].textContent, ' Offline <show>');
assert.equal(buttons.children.filter(button => button.attributes['aria-pressed'] === 'true').length, 1);
assert.equal(programmes.children.filter(page => !page.inert).length, 1);
source.listeners.playhead({data: JSON.stringify({id: 'manual', metadata: {stream_title: 'Current track'}})});
assert.equal(elements['epg-now-name'].textContent, 'Current track');
assert.equal(currentDay(), '2026-11-02');
// Incoming schedule updates retain the chosen date and use fresh programme data.
guide.programmes[0].title = 'Updated offline show';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.match(visiblePage().textContent, /Updated offline show/);
chooseDay('2026-11-03');
assert.equal(selectedButton().textContent, 'Tue');
assert.doesNotMatch(visiblePage().textContent, /Updated offline show/);
chooseDay('2026-11-07');
assert.equal(selectedButton(), buttons.children[6]);
assert.equal(selectedButton().textContent, 'Sat');
// Returning to Today follows the UTC date when the guide rolls over.
chooseDay(guide.start_day);
guide.start_day = '2026-11-02';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.equal(selectedButton(), buttons.children[0]);
assert.equal(selectedButton().textContent, 'Mon');
assert.match(visiblePage().textContent, /Updated offline show/);
// An explicitly selected date that leaves the guide resets to the new Today.
chooseDay('2026-11-03');
guide.start_day = '2026-11-04';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-04');
assert.equal(selectedButton(), buttons.children[0]);
assert.equal(selectedButton().textContent, 'Wed');
assert.doesNotMatch(visiblePage().textContent, /Updated offline show/);
// Dates still within the rolling window retain their selection at a new position.
chooseDay('2026-11-06');
assert.equal(selectedButton(), buttons.children[2]);
guide.start_day = '2026-11-05';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-06');
assert.equal(selectedButton(), buttons.children[1]);
assert.equal(selectedButton().textContent, 'Fri');
"""
        result = subprocess.run(['node', '-e', script, str(scripts)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
