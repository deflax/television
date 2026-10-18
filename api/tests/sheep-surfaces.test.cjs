const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function platform(id, left, top, width, height) {
  return {
    id, hidden: false,
    getBoundingClientRect: () => ({ left, top, width, height, right: left + width, bottom: top + height })
  };
}

function setup(elements, width = 1000, height = 800, size = 50) {
  const styles = () => ({ display: 'block', visibility: 'visible', opacity: '1', getPropertyValue: () => '' });
  const window = { SheepInternals: {}, innerWidth: width, innerHeight: height, getComputedStyle: styles };
  const document = { documentElement: {}, querySelectorAll: () => elements };
  const sandbox = vm.createContext({ window, document, getComputedStyle: styles, Math });
  for (const file of ['action-catalog.js', 'surface-planner.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '../static/js/sheep', file), 'utf8'), sandbox);
  }
  const queued = [];
  const context = {
    window, document,
    config: { GROUND_SURFACE_ID: 'ground', SURFACE_SELECTOR: '.sheep-surface' },
    state: { x: 100, y: 0, direction: 1, currentSurfaceId: 'ground' },
    effects: {},
    helpers: { clamp: (v, min, max) => Math.min(max, Math.max(min, v)) },
    services: {
      presentation: { getSpriteMetrics: () => ({ width: size, height: size }) },
      runtimeEngine: {
        queueTurn() {}, queueTravel() {},
        queueAction(name, options) { queued.push({ name, ...options }); }
      }
    }
  };
  context.services.actionCatalog = window.SheepInternals.createActionCatalog(context);
  const planner = window.SheepInternals.createSurfacePlanner(context);
  context.services.surfacePlanner = planner;
  planner.refreshSurfaces();
  return { planner, context, queued };
}

test('platform tops catch downward swept crossings, not upward or horizontal movement', () => {
  const { planner } = setup([platform('ledge', 100, 200, 300, 30)]);
  const hit = planner.findLandingSurfaceBetween(150, 100, 150, 300);
  assert.equal(hit.surface.id, 'surface:ledge');
  assert.equal(hit.surface.lineY, 200);
  assert.equal(hit.timeOfImpact, 0.25);
  assert.equal(planner.findLandingSurfaceBetween(150, 300, 150, 100), null);
  assert.equal(planner.findLandingSurfaceBetween(150, 150, 250, 150), null);
  assert.equal(planner.findLandingSurfaceBetween(600, 100, 600, 300), null);
});

test('the first crossed top wins and departure platforms can be excluded', () => {
  const { planner } = setup([
    platform('upper', 100, 200, 300, 30),
    platform('lower', 100, 300, 300, 30)
  ]);
  assert.equal(planner.findLandingSurfaceBetween(150, 100, 150, 400).surface.id, 'surface:upper');
  assert.equal(planner.findLandingSurfaceBetween(150, 150, 150, 400, 'surface:upper').surface.id, 'surface:lower');
});

test('offscreen and hidden tops are not fabricated at the viewport boundary', () => {
  const hidden = platform('hidden', 100, 300, 300, 30);
  hidden.hidden = true;
  const { planner } = setup([platform('offscreen', 100, 20, 300, 300), hidden]);
  assert.equal(planner.getSurfaces().filter(s => s.type === 'marked').length, 0);
});

test('the video top remains a real landing surface beside the navigation', () => {
  const { planner } = setup([platform('media', 120, 66, 1000, 570)], 1248, 771, 52);
  const media = planner.resolveSurfaceById('surface:media');
  assert.equal(media.id, 'surface:media');
  assert.equal(media.landY + 52, 66);
  const hit = planner.findLandingSurfaceBetween(500, 8, 500, 40);
  assert.equal(hit.surface.id, 'surface:media');
  assert.equal(hit.surface.lineY, 66);
});

test('a tall marked rectangle is reachable by its side without an oversized jump', () => {
  const { planner, context, queued } = setup([platform('wall', 150, 140, 600, 550)]);
  context.state.y = planner.getGroundSurface().landY;
  const plan = planner.pickSurfaceTraversalPlan();
  assert.equal(plan.name, 'climbUp');
  assert.equal(plan.targetSurfaceId, 'surface:wall');
  assert.equal(planner.queueSurfaceTraversalPlan(plan), true);
  const action = queued.at(-1);
  action.onStart(action);
  assert.equal(action.arrivalPoint.y, 90);
  assert.ok(action.path.some(point => point.phase === 'climb'));
  assert.ok(action.speed < 200);
});

test('narrow-screen sheep can climb from a footer onto the video rim', () => {
  const { planner, context } = setup([
    platform('media', 16, 110, 358, 208),
    platform('footer', 16, 441, 358, 292)
  ], 390, 844, 40);
  const footer = planner.resolveSurfaceById('surface:footer');
  planner.setCurrentSurface(footer);
  context.state.x = 180;
  context.state.y = footer.landY;
  const plan = planner.pickSurfaceTraversalPlan();
  assert.equal(plan.name, 'climbUp');
  assert.equal(plan.targetSurfaceId, 'surface:media');
});

test('removing a destination invalidates traversal rather than committing its stale landing', () => {
  const wall = platform('wall', 150, 140, 600, 550);
  const { planner, context, queued } = setup([wall]);
  context.state.y = planner.getGroundSurface().landY;
  planner.queueSurfaceTraversalPlan(planner.pickSurfaceTraversalPlan());
  const action = queued.at(-1);
  action.onStart(action);
  wall.hidden = true;
  assert.equal(planner.refreshActiveTraversal(action), false);
  assert.equal(action.arrivalPoint, null);
  assert.equal(action.arrivalSurfaceId, null);
  assert.equal(action.target, null);
});
