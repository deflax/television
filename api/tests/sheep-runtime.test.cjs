const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function scene(elements = []) {
  let timestamp = 1000;
  let nextFrame = 0;
  const frames = new Map();
  const style = () => ({ display: 'block', visibility: 'visible', opacity: '1', getPropertyValue: () => '' });
  const window = {
    SheepInternals: {}, innerWidth: 1000, innerHeight: 800,
    getComputedStyle: style,
    requestAnimationFrame(callback) { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame(id) { frames.delete(id); }
  };
  const document = { documentElement: {}, querySelectorAll: () => elements };
  const sandbox = vm.createContext({ window, document, getComputedStyle: style, Math: Object.assign(Object.create(Math), { random: () => 0.9 }) });
  for (const file of ['action-catalog.js', 'surface-planner.js', 'collision-world.js', 'runtime-engine.js']) {
    vm.runInContext(readFileSync(path.join(__dirname, '../static/js/sheep', file), 'utf8'), sandbox);
  }
  const internals = window.SheepInternals;
  const world = internals.createCollisionWorld();
  function createSheep(id, x, y) {
    const state = {
      x, y, direction: 1, enabled: true, sheepVisible: true, lastTimestamp: 0,
      animationFrame: 0, activeAction: null, actionQueue: [], currentSurfaceId: 'ground',
      prop: { visible: false }, secondaryProp: { visible: false }
    };
    const context = {
      window, document, sheepId: id, state,
      refs: { sprite: { hidden: false, isConnected: true }, layer: { hidden: false } },
      config: { GROUND_SURFACE_ID: 'ground', SURFACE_SELECTOR: '.sheep-surface' },
      helpers: {
        clamp: (v, min, max) => Math.min(max, Math.max(min, v)),
        randomBetween: (min, max) => (min + max) / 2,
        composeCallbacks: (...callbacks) => (...args) => callbacks.forEach(fn => { if (fn) fn(...args); })
      },
      services: { collisionWorld: world }, effects: {}
    };
    context.services.presentation = {
      getSpriteMetrics: () => ({ width: 50, height: 50 }), hasSprite: () => true,
      applyPosition() {},
      enterActionFrame(action) {
        state.currentFrame = action.frames[action.frameIndex];
        action.frameEvents?.[action.frameIndex]?.(action);
      },
      showSheep() { state.sheepVisible = true; },
      hideSheep() { state.sheepVisible = false; },
      hideProp() { state.prop.visible = false; },
      hideSecondaryProp() { state.secondaryProp.visible = false; }
    };
    context.effects = {
      getBounds: () => context.services.surfacePlanner.getBounds(),
      queueAction: (...args) => context.services.runtimeEngine.queueAction(...args),
      queueSleep: (...args) => context.services.runtimeEngine.queueSleep(...args),
      ...context.services.presentation
    };
    context.services.actionCatalog = internals.createActionCatalog(context);
    context.services.surfacePlanner = internals.createSurfacePlanner(context);
    context.services.runtimeEngine = internals.createRuntimeEngine(context);
    context.services.surfacePlanner.refreshSurfaces();
    world.register(context);
    return context;
  }
  function step() {
    timestamp += 16;
    const callbacks = [...frames.values()];
    frames.clear();
    callbacks.forEach(fn => fn(timestamp));
  }
  function until(predicate, maxFrames = 600) {
    for (let i = 0; i < maxFrames; i++) {
      if (predicate()) return true;
      step();
    }
    return predicate();
  }
  return { createSheep, step, until };
}

function wall() {
  return {
    id: 'wall', hidden: false,
    getBoundingClientRect: () => ({ left: 150, right: 750, top: 140, bottom: 690, width: 600, height: 550 })
  };
}

for (const approach of [1, -1]) {
  test(`ground encounters turn once and retreat when approaching ${approach === 1 ? 'right' : 'left'}`, () => {
    const simulation = scene();
    const startX = approach === 1 ? 300 : 500;
    const mover = simulation.createSheep(1, startX, 734);
    simulation.createSheep(2, startX + approach * 110, 734);
    const runtime = mover.services.runtimeEngine;
    const { ACTIONS } = mover.services.actionCatalog;
    const turnFrames = new Set([...ACTIONS.direction.frames, ...ACTIONS.directionBack.frames]);
    runtime.queueAction('walk', { target: { x: startX + approach * 200, y: 734 }, speed: 100 });
    runtime.startNextAction();
    runtime.startLoop();
    assert.ok(simulation.until(() => mover.state.activeAction?.name !== 'walk'));
    const contactX = mover.state.x;
    const contactY = mover.state.y;
    const stationaryReactions = new Set();
    let previousDirection = mover.state.direction;
    let directionChanges = 0;
    for (let i = 0; i < 120 && (mover.state.x - contactX) * approach >= -10; i++) {
      const action = mover.state.activeAction;
      if (action && !action.loop && !action.target) {
        stationaryReactions.add(action);
        assert.equal(turnFrames.has(mover.state.currentFrame), true);
        assert.equal(mover.state.x, contactX);
      }
      assert.equal(mover.state.y, contactY);
      simulation.step();
      if (mover.state.direction !== previousDirection) {
        directionChanges += 1;
        previousDirection = mover.state.direction;
      }
    }
    assert.equal(stationaryReactions.size, 1);
    assert.equal(directionChanges, 1);
    assert.equal(mover.state.direction, approach);
    assert.ok((mover.state.x - contactX) * approach < -10);
  });
}

for (const [location, x] of [['mid-platform', 450], ['at the viewport edge', 934]]) {
  test(`a falling sheep bounces aside ${location} instead of stacking`, () => {
    const simulation = scene();
    const falling = simulation.createSheep(1, x, 600);
    const groundY = falling.services.surfacePlanner.getGroundSurface().landY;
    simulation.createSheep(2, x, groundY);
    const runtime = falling.services.runtimeEngine;
    runtime.queueAction('jumpDown', {
      onStart(action) {
        action.motionEnabled = true;
        action.speed = 200;
        action.target = { x, y: groundY };
        action.waitForLanding = true;
        action.waitForMotionCompletion = true;
      }
    });
    runtime.startNextAction();
    runtime.startLoop();
    assert.ok(simulation.until(() => falling.state.y === groundY && !falling.state.activeAction?.waitForLanding));
    assert.ok(Math.abs(falling.state.x - x) >= 30);
  });
}

test('long side climbs keep moving after the finite sprite sequence would have ended', () => {
  const simulation = scene([wall()]);
  const climber = simulation.createSheep(1, 725, 734);
  const { runtimeEngine: runtime, surfacePlanner: planner } = climber.services;
  assert.equal(planner.queueSurfaceTraversalPlan({ name: 'climbUp', edge: 'right', targetSurfaceId: 'surface:wall' }), true);
  runtime.startNextAction();
  runtime.startLoop();
  for (let i = 0; i < 100; i++) simulation.step();
  assert.equal(climber.state.activeAction.name, 'climbUp');
  assert.notEqual(climber.state.currentSurfaceId, 'surface:wall');
  assert.ok(climber.state.y > 90);
  assert.ok(simulation.until(() => climber.state.currentSurfaceId === 'surface:wall'));
  assert.equal(climber.state.y, 90);
});

test('removing a wall mid-climb cancels arrival and falls without teleporting', () => {
  const element = wall();
  const simulation = scene([element]);
  const climber = simulation.createSheep(1, 725, 734);
  const { runtimeEngine: runtime, surfacePlanner: planner } = climber.services;
  planner.queueSurfaceTraversalPlan({ name: 'climbUp', edge: 'right', targetSurfaceId: 'surface:wall' });
  runtime.startNextAction();
  runtime.startLoop();
  for (let i = 0; i < 80; i++) simulation.step();
  const beforeRemoval = climber.state.y;
  element.hidden = true;
  simulation.step();
  assert.equal(climber.state.y, beforeRemoval);
  assert.notEqual(climber.state.activeAction?.arrivalSurfaceId, 'surface:wall');
  const groundY = planner.getGroundSurface().landY;
  assert.ok(simulation.until(() => climber.state.y === groundY));
  assert.equal(climber.state.currentSurfaceId, 'ground');
});

test('resting sheep follow a reflowed support and fall when that support disappears', () => {
  const element = wall();
  const simulation = scene([element]);
  const resting = simulation.createSheep(1, 450, 90);
  const { runtimeEngine: runtime, surfacePlanner: planner } = resting.services;
  planner.setCurrentSurface(planner.resolveSurfaceById('surface:wall'));
  runtime.queueAction('yawn');
  runtime.startNextAction();
  runtime.startLoop();
  simulation.step();
  const originalRect = element.getBoundingClientRect();
  element.getBoundingClientRect = () => ({ ...originalRect, top: 180, bottom: 730 });
  simulation.step();
  assert.equal(resting.state.y, 130);
  element.hidden = true;
  simulation.step();
  const groundY = planner.getGroundSurface().landY;
  assert.ok(resting.state.y > 130 && resting.state.y < groundY);
  assert.equal(resting.state.activeAction.waitForLanding, true);
  assert.ok(simulation.until(() => resting.state.y === groundY));
});
