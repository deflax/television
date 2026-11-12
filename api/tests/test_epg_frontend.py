import os
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for isolated frontend consumer tests')
class WeeklyEpgFrontendTest(unittest.TestCase):
    def run_frontend(self, assertions, timezone='UTC'):
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
  'epg-day-buttons', 'epg-day-controls', 'epg-day-date', 'epg-day-programmes', 'visitor-number']
  .map(id => [id, new Element('div')]));
const document = {
  getElementById: id => elements[id],
  createElement: tag => new Element(tag),
  createTextNode: text => ({textContent: text}),
};
let now = Date.parse('2026-11-01T23:30:00Z');
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return now; }
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
"""
        result = subprocess.run(
            ['node', '-e', script + assertions, str(scripts)],
            capture_output=True, text=True, env={**os.environ, 'TZ': timezone},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_weekly_guide_and_manual_playhead_labels(self):
        self.run_frontend(r"""
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
const visiblePage = () => programmes.children.find(page => page.attributes['aria-hidden'] === 'false');
const currentDay = () => {
  const day = visiblePage().dataset.day;
  assert.equal(elements['epg-day-date'].dateTime, day);
  assert.match(elements['epg-day-date'].textContent, new RegExp(day));
  return day;
};
const previous = () => buttons.children[0].listeners.click();
const next = () => buttons.children[1].listeners.click();
const chooseDay = day => {
  while (currentDay() !== day) {
    const before = currentDay();
    (before < day ? next : previous)();
    assert.notEqual(currentDay(), before, 'Navigation must reach the requested day');
  }
};
assert.equal(currentDay(), guide.start_day);
assert.equal(buttons.children[0].disabled, true);
previous();
assert.equal(currentDay(), guide.start_day);
assert.equal(elements['epg-day-controls'].hidden, false);
assert.doesNotMatch(visiblePage().textContent, /Offline <show>/);
assert.equal(programmes.children.filter(page => !page.inert).length, 1);
assert.equal(visiblePage().inert, false);
next();
assert.equal(currentDay(), '2026-11-02');
assert.equal(buttons.children[0].disabled, false);
assert.match(visiblePage().textContent, /12:00–24:00 Offline <show>/);
assert.match(visiblePage().textContent, /Episode & details/);
assert.doesNotMatch(visiblePage().textContent, /Manual excluded-day stream/);
assert.equal(programmes.children.filter(page => !page.inert).length, 1);
source.listeners.playhead({data: JSON.stringify({id: 'manual', metadata: {stream_title: 'Current track'}})});
assert.equal(elements['epg-now-name'].textContent, 'Current track');
assert.equal(currentDay(), '2026-11-02');
// Incoming schedule updates retain the chosen date and use fresh programme data.
guide.programmes[0].title = 'Updated offline show';
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.match(visiblePage().textContent, /Updated offline show/);
next();
assert.equal(currentDay(), '2026-11-03');
assert.doesNotMatch(visiblePage().textContent, /Updated offline show/);
chooseDay('2026-11-07');
assert.equal(buttons.children[1].disabled, true);
next();
assert.equal(currentDay(), '2026-11-07');
previous();
assert.equal(currentDay(), '2026-11-06');
assert.equal(buttons.children[1].disabled, false);
// Returning to Today follows the browser date when the guide rolls over.
chooseDay(guide.start_day);
guide.start_day = '2026-11-02';
now = Date.parse('2026-11-02T12:00:00Z');
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-02');
assert.equal(buttons.children[0].disabled, true);
assert.match(visiblePage().textContent, /Updated offline show/);
// An explicitly selected date that leaves the guide resets to the new Today.
chooseDay('2026-11-03');
guide.start_day = '2026-11-04';
now = Date.parse('2026-11-04T12:00:00Z');
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-04');
assert.equal(buttons.children[0].disabled, true);
assert.doesNotMatch(visiblePage().textContent, /Updated offline show/);
// Dates still within the rolling window retain their selection at a new position.
chooseDay('2026-11-06');
guide.start_day = '2026-11-05';
now = Date.parse('2026-11-05T12:00:00Z');
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(currentDay(), '2026-11-06');
previous();
assert.equal(currentDay(), '2026-11-05');
guide.days = 1;
source.listeners.epg({data: JSON.stringify(guide)});
assert.equal(buttons.children[0].disabled, true);
assert.equal(buttons.children[1].disabled, true);
""")

    def test_local_dates_and_daylight_saving_navigation(self):
        self.run_frontend(r"""
now = Date.parse('2026-11-01T03:30:00Z'); // Still October 31 in New York.
const guide = {
  start_day: '2026-11-01', days: 7, catalogue: {},
  programmes: [
    {id: 'overnight', title: 'Overnight', start: '2026-11-01T03:00:00Z', stop: '2026-11-01T07:00:00Z'},
    {id: 'fallback', title: 'Fallback', start: '2026-11-01T05:30:00Z', stop: '2026-11-01T07:30:00Z'},
    {id: 'last', title: 'Last UTC day', start: '2026-11-07T23:00:00Z', stop: '2026-11-08T00:00:00Z'},
  ],
};
const app = context.window.StreamApp;
const visible = () => elements['epg-day-programmes'].children.find(page => !page.inert);
const buttons = elements['epg-day-buttons'];
const next = () => buttons.children[1].listeners.click();
const previous = () => buttons.children[0].listeners.click();
app.renderEpg(guide, 'overnight');
assert.match(elements['epg-day-date'].textContent, /Today, 2026-10-31 \(America\/New_York\)/);
assert.match(visible().textContent, /23:00–02:00 \(2026-11-01\) Overnight/);
assert.equal(visible().children[0].children[0].style.color, 'var(--epg-active-purple)');
assert.equal(buttons.children[0].disabled, true);
next();
assert.equal(visible().dataset.day, '2026-11-01');
assert.match(elements['epg-day-date'].textContent, /Tomorrow/);
assert.match(visible().textContent, /01:30–02:30 Fallback/);
assert.doesNotMatch(visible().textContent, /Overnight/);
next(); // A 25-hour local day must not skip or repeat a date.
assert.equal(visible().dataset.day, '2026-11-02');
previous();
assert.equal(visible().dataset.day, '2026-11-01');
now = Date.parse('2026-11-02T04:30:00Z');
guide.start_day = '2026-11-02';
app.renderEpg(guide, null);
assert.equal(visible().dataset.day, '2026-11-01', 'Retain selected local date across UTC rollover');
assert.match(elements['epg-day-date'].textContent, /Today/);
for (let i = 0; i < 6; i++) next();
assert.equal(visible().dataset.day, '2026-11-07');
assert.match(visible().textContent, /18:00–19:00 Last UTC day/);
next();
assert.equal(visible().dataset.day, '2026-11-08');
assert.equal(buttons.children[1].disabled, true);
// Spring-forward also advances by calendar dates, not fixed 24-hour intervals.
now = Date.parse('2026-03-08T12:00:00Z');
guide.start_day = '2026-03-08';
guide.programmes = [
  {id: 'spring', title: 'Spring', start: '2026-03-08T06:30:00Z', stop: '2026-03-08T07:30:00Z'},
];
app.renderEpg(guide, null);
assert.equal(visible().dataset.day, '2026-03-08');
assert.match(visible().textContent, /01:30–03:30 Spring/);
next();
assert.equal(visible().dataset.day, '2026-03-09');
previous();
assert.equal(visible().dataset.day, '2026-03-08');
""", 'America/New_York')

    def test_positive_fractional_timezone_and_final_partial_day(self):
        self.run_frontend(r"""
const guide = {
  start_day: '2026-11-01', days: 7, catalogue: {},
  programmes: [
    {id: 'shifted', title: 'Shifted', start: '2026-11-01T23:00:00Z', stop: '2026-11-02T00:00:00Z'},
    {id: 'final', title: 'Final', start: '2026-11-07T23:00:00Z', stop: '2026-11-08T00:00:00Z'},
  ],
};
const app = context.window.StreamApp;
const visible = () => elements['epg-day-programmes'].children.find(page => !page.inert);
const buttons = elements['epg-day-buttons'];
app.renderEpg(guide, null);
assert.equal(visible().dataset.day, '2026-11-02', 'Default to local today, not UTC today');
assert.match(visible().textContent, /04:45–05:45 Shifted/);
buttons.children[0].listeners.click();
assert.equal(visible().dataset.day, '2026-11-01');
assert.doesNotMatch(visible().textContent, /Shifted/);
assert.equal(buttons.children[0].disabled, true);
for (let i = 0; i < 7; i++) buttons.children[1].listeners.click();
assert.equal(visible().dataset.day, '2026-11-08');
assert.match(visible().textContent, /04:45–05:45 Final/);
assert.equal(buttons.children[1].disabled, true);
""", 'Asia/Kathmandu')


if __name__ == '__main__':
    unittest.main()
