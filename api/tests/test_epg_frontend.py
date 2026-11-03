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
let focused = null;
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.style = {}; this.text = ''; this.listeners = {}; this.attributes = {}; }
  set textContent(value) { this.text = value; this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.text = ''; this.children = []; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { focused = this; }
}
const elements = Object.fromEntries(['epg-now-playing', 'epg-now-name', 'epg-schedule',
  'epg-day-prev', 'epg-day-next', 'epg-day-prev-label', 'epg-day-next-label',
  'epg-day-controls', 'epg-day-programmes', 'visitor-number']
  .map(id => [id, new Element('div')]));
elements['epg-day-prev'].appendChild(elements['epg-day-prev-label']);
elements['epg-day-next'].appendChild(elements['epg-day-next-label']);
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
const previous = elements['epg-day-prev'];
const next = elements['epg-day-next'];
const programmes = elements['epg-day-programmes'];
const currentDay = () => context.window.StreamApp.selectedEpgDay || context.window.StreamApp.currentEpgGuide.start_day;
const chooseDay = day => {
  let steps = 0;
  while (currentDay() !== day) {
    const button = currentDay() < day ? next : previous;
    assert.equal(button.hidden, false);
    button.listeners.click();
    assert.ok(++steps <= 6, 'Navigation must reach a date within the seven-day guide');
  }
};
assert.equal(currentDay(), guide.start_day);
assert.equal(previous.hidden, true);
assert.equal(next.hidden, false);
assert.equal(next.textContent, 'Tomorrow');
previous.listeners.click();
assert.equal(currentDay(), guide.start_day);
assert.equal(elements['epg-day-controls'].hidden, false);
assert.doesNotMatch(programmes.textContent, /Offline <show>/);
chooseDay('2026-11-02');
assert.match(programmes.textContent, /12:00–24:00 Offline <show>/);
assert.match(programmes.textContent, /Episode & details/);
assert.doesNotMatch(programmes.textContent, /Manual excluded-day stream/);
assert.equal(programmes.children[0].children[0].children[1].textContent, ' Offline <show>');
source.listeners.playhead({data: JSON.stringify({id: 'manual', metadata: {stream_title: 'Current track'}})});
assert.equal(elements['epg-now-name'].textContent, 'Current track');
assert.equal(currentDay(), '2026-11-02');
assert.equal(previous.textContent, 'Today');
assert.equal(next.textContent, 'Tuesday');
assert.equal(previous.hidden, false);
assert.equal(next.hidden, false);
// Incoming schedule updates retain the chosen date and use fresh programme data.
guide.programmes[0].title = 'Updated offline show';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.match(programmes.textContent, /Updated offline show/);
chooseDay('2026-11-03');
assert.equal(previous.textContent, 'Tomorrow');
assert.equal(next.textContent, 'Wednesday');
chooseDay('2026-11-07');
assert.equal(next.hidden, true);
assert.equal(previous.hidden, false);
assert.equal(focused, previous);
next.listeners.click();
assert.equal(currentDay(), '2026-11-07');
assert.equal(previous.textContent, 'Friday');
assert.doesNotMatch(programmes.textContent, /Updated offline show/);
// Returning to Today follows the UTC date when the guide rolls over.
chooseDay(guide.start_day);
assert.equal(focused, next);
guide.start_day = '2026-11-02';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.equal(next.textContent, 'Tomorrow');
assert.match(programmes.textContent, /Updated offline show/);
// An explicitly selected date that leaves the guide resets to the new Today.
chooseDay('2026-11-03');
guide.start_day = '2026-11-04';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-04');
assert.equal(next.textContent, 'Tomorrow');
assert.equal(previous.hidden, true);
assert.equal(next.hidden, false);
assert.doesNotMatch(programmes.textContent, /Updated offline show/);
"""
        result = subprocess.run(['node', '-e', script, str(scripts)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
