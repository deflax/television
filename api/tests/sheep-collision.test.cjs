const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

function createWorld() {
  const window = { SheepInternals: {} };
  vm.runInNewContext(
    readFileSync(path.join(__dirname, '../static/js/sheep/collision-world.js'), 'utf8'),
    { window }
  );
  return window.SheepInternals.createCollisionWorld();
}

function sheep(id, x, y = 100, targetX = null) {
  return {
    sheepId: id,
    state: {
      x, y, enabled: true, sheepVisible: true,
      activeAction: targetX === null ? null : {
        target: { x: targetX, y }, speed: 60, motionEnabled: true
      }
    },
    refs: {
      sprite: { hidden: false, isConnected: true },
      layer: { hidden: false },
      propSprite: { hidden: false },
      secondaryPropSprite: { hidden: false }
    },
    services: { presentation: { getSpriteMetrics: () => ({ width: 50, height: 50 }) } }
  };
}

test('swept bodies stop a fast mover before it tunnels through another sheep', () => {
  const world = createWorld();
  const left = sheep(1, 0, 100, 200);
  const right = sheep(2, 100, 100, 0);
  world.register(left);
  world.register(right);
  const result = world.resolveMovement(left, { x: 0, y: 100 }, { x: 200, y: 100 }, 1000);
  assert.equal(result.contact.otherId, 2);
  assert.equal(result.contact.soft, false);
  assert.equal(result.contact.normal.x, -1);
  assert.ok(Math.abs(result.contact.timeOfImpact - 0.35) < 1e-9);
  assert.ok(result.x < 70 && result.x > 69);
});

test('higher ID yields head-on before body contact while the lower ID keeps moving', () => {
  const world = createWorld();
  const left = sheep(1, 0, 100, 200);
  const right = sheep(2, 100, 100, 0);
  world.register(left);
  world.register(right);
  const yieldResult = world.resolveMovement(right, { x: 100, y: 100 }, { x: 0, y: 100 }, 1000);
  assert.equal(yieldResult.contact.soft, true);
  assert.equal(yieldResult.contact.normal.x, 1);
  assert.ok(yieldResult.x > 30);
  const proceed = world.resolveMovement(left, { x: 0, y: 100 }, { x: 20, y: 100 }, 1000);
  assert.equal(proceed.contact, null);
  assert.equal(proceed.x, 20);
  right.state.x = yieldResult.x;
  right.state.activeAction = null;
  const waiting = world.resolveMovement(left, { x: 0, y: 100 }, { x: 100, y: 100 }, 1100);
  assert.equal(waiting.contact.wait, true);
  assert.equal(waiting.contact.soft, false);
  assert.ok(waiting.x + 40 <= right.state.x + 10);
});

test('moving sheep yield to a stationary sheep regardless of ID', () => {
  const world = createWorld();
  const mover = sheep(1, 0, 100, 200);
  const resting = sheep(2, 100);
  world.register(mover);
  world.register(resting);
  const result = world.resolveMovement(mover, { x: 0, y: 100 }, { x: 100, y: 100 }, 1000);
  assert.equal(result.contact.soft, true);
  assert.ok(result.x < 70);
});

test('overlapping bodies can escape but cannot move deeper into each other', () => {
  const world = createWorld();
  const left = sheep(1, 0);
  const right = sheep(2, 10);
  world.register(left);
  world.register(right);
  const escape = world.resolveMovement(left, { x: 0, y: 100 }, { x: -30, y: 100 }, 1000);
  assert.equal(escape.contact, null);
  assert.equal(escape.x, -30);
  const deeper = world.resolveMovement(left, { x: 0, y: 100 }, { x: 20, y: 100 }, 1001);
  assert.equal(deeper.contact.soft, false);
  assert.equal(deeper.x, 0);
});

test('hidden, abducted and disabled sheep do not obstruct movement', () => {
  for (const state of [{ sheepVisible: false }, { abducted: true }, { enabled: false }, { destroyed: true }]) {
    const world = createWorld();
    const mover = sheep(1, 0, 100, 200);
    const inactive = sheep(2, 100);
    Object.assign(inactive.state, state);
    world.register(mover);
    world.register(inactive);
    const result = world.resolveMovement(mover, { x: 0, y: 100 }, { x: 200, y: 100 }, 1000);
    assert.equal(result.contact, null);
    assert.equal(result.x, 200);
  }
});

test('decorative prop sprites cannot be registered as sheep bodies', () => {
  const world = createWorld();
  const mover = sheep(1, 0, 100, 200);
  const visitor = sheep(2, 100);
  visitor.refs.sprite = visitor.refs.propSprite;
  world.register(mover);
  assert.equal(world.register(visitor), false);
  const result = world.resolveMovement(mover, { x: 0, y: 100 }, { x: 200, y: 100 }, 1000);
  assert.equal(result.contact, null);
  assert.equal(result.x, 200);
});

test('spawn placement separates bodies and refuses a fully occupied platform', () => {
  const world = createWorld();
  const first = sheep(1, 0);
  const second = sheep(2, 0);
  world.register(first);
  world.register(second);
  const surface = { id: 'ground', minX: 0, maxX: 200, landY: 100 };
  assert.ok(world.placeOnSurface(second, surface));
  assert.ok(second.state.x >= 34);
  const third = sheep(3, 0);
  world.register(third);
  assert.equal(world.placeOnSurface(third, { ...surface, maxX: 10 }), null);
  assert.equal(third.state.x, 0);
});
