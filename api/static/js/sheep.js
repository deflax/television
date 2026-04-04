window.SheepApp = window.SheepApp || {};

((app) => {
  if (app.initialized) {
    return;
  }

  app.initialized = true;

  const SPRITE_SHEET_URL = '/static/vendor/sheep/rsc/sheep.png';
  const SPRITE_COLUMNS = 16;
  const SPRITE_ROWS = 11;
  const NEUTRAL_FRAME = 3;
  const ACTIONS = Object.freeze({
    walk: Object.freeze({
      frames: Object.freeze([2, 3]),
      frameMs: 180,
      loop: true
    }),
    sleep: Object.freeze({
      frames: Object.freeze([0, 1]),
      frameMs: 420,
      loop: true
    }),
    run: Object.freeze({
      frames: Object.freeze([4, 5]),
      frameMs: 110,
      loop: true
    }),
    direction: Object.freeze({
      frames: Object.freeze([3, 9, 10, 11, 3]),
      frameMs: 90,
      loop: false
    }),
    directionBack: Object.freeze({
      frames: Object.freeze([3, 12, 13, 14, 3]),
      frameMs: 90,
      loop: false
    }),
    bump: Object.freeze({
      frames: Object.freeze([62, 63, 64, 65, 66, 67, 68, 69, 70, 63]),
      frameMs: 72,
      loop: false
    })
  });

  const DEFAULTS = Object.freeze({
    minWalkSpeed: 40,
    maxWalkSpeed: 66,
    minRunSpeed: 92,
    maxRunSpeed: 126,
    minWalkDistance: 120,
    minRunDistance: 176,
    sleepMinMs: 1200,
    sleepMaxMs: 3000,
    edgeRetargetMinInset: 48,
    edgeRetargetVerticalInset: 40,
    runChance: 0.2,
    sleepChance: 0.42
  });

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const state = {
    x: 0,
    y: 0,
    direction: 1,
    currentFrame: null,
    lastTimestamp: 0,
    animationFrame: 0,
    reducedMotion: prefersReducedMotion.matches,
    modalOpen: false,
    activeAction: null,
    actionQueue: [],
    lastTurnAction: 'directionBack'
  };

  let layer = null;
  let sprite = null;

  function randomBetween(min, max) {
    return min + (Math.random() * (max - min));
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function readCssPixels(name, fallback) {
    const rootStyles = getComputedStyle(document.documentElement);
    const value = parseFloat(rootStyles.getPropertyValue(name));
    return Number.isFinite(value) ? value : fallback;
  }

  function getBounds() {
    const spriteWidth = sprite ? (sprite.offsetWidth || 72) : 72;
    const spriteHeight = sprite ? (sprite.offsetHeight || 50) : 50;
    const edgePadding = readCssPixels('--sheep-edge-padding', 16);
    const safeTop = readCssPixels('--sheep-safe-top', 68);
    const safeBottom = readCssPixels('--sheep-safe-bottom', 16);

    const minX = edgePadding;
    const minY = Math.max(edgePadding, safeTop);
    const maxX = Math.max(minX, window.innerWidth - spriteWidth - edgePadding);
    const maxY = Math.max(minY, window.innerHeight - spriteHeight - safeBottom);

    return { minX, minY, maxX, maxY };
  }

  function clampPosition() {
    const bounds = getBounds();
    const nextX = clamp(state.x, bounds.minX, bounds.maxX);
    const nextY = clamp(state.y, bounds.minY, bounds.maxY);
    const hitBounds = {
      left: nextX === bounds.minX && state.x < bounds.minX,
      right: nextX === bounds.maxX && state.x > bounds.maxX,
      top: nextY === bounds.minY && state.y < bounds.minY,
      bottom: nextY === bounds.maxY && state.y > bounds.maxY
    };

    state.x = nextX;
    state.y = nextY;

    return hitBounds;
  }

  function applyPosition() {
    if (!sprite) {
      return;
    }

    sprite.style.transform = `translate3d(${state.x}px, ${state.y}px, 0) scaleX(${state.direction})`;
  }

  function setFrame(frame, force) {
    if (!sprite) {
      return;
    }

    if (!force && state.currentFrame === frame) {
      return;
    }

    state.currentFrame = frame;

    const frameSize = sprite.offsetWidth || 72;
    const column = frame % SPRITE_COLUMNS;
    const row = Math.floor(frame / SPRITE_COLUMNS);

    sprite.style.backgroundPosition = `${-column * frameSize}px ${-row * frameSize}px`;
  }

  function buildAction(name, overrides) {
    const definition = ACTIONS[name];
    const options = overrides || {};

    return {
      name: name,
      frames: definition.frames.slice(),
      frameMs: definition.frameMs,
      loop: definition.loop,
      durationMs: options.durationMs ?? null,
      speed: options.speed ?? 0,
      target: options.target ?? null,
      direction: typeof options.direction === 'number' ? options.direction : null,
      onComplete: typeof options.onComplete === 'function' ? options.onComplete : null
    };
  }

  function queueAction(name, overrides) {
    state.actionQueue.push(buildAction(name, overrides));
  }

  function clearQueuedActions() {
    state.actionQueue.length = 0;
  }

  function startNextAction() {
    if (state.activeAction || !state.actionQueue.length) {
      return;
    }

    const action = state.actionQueue.shift();
    state.activeAction = {
      name: action.name,
      frames: action.frames,
      frameMs: action.frameMs,
      loop: action.loop,
      durationMs: action.durationMs,
      speed: action.speed,
      target: action.target,
      direction: action.direction,
      onComplete: action.onComplete,
      frameIndex: 0,
      frameElapsedMs: 0,
      elapsedMs: 0,
      lastVector: { dx: 0, dy: 0 }
    };

    setFrame(state.activeAction.frames[0], true);
  }

  function finishActiveAction(timestamp, reason) {
    const action = state.activeAction;

    if (!action) {
      return;
    }

    state.activeAction = null;

    if (typeof action.onComplete === 'function') {
      action.onComplete(action, timestamp, reason);
    }
  }

  function cancelActiveAction() {
    state.activeAction = null;
  }

  function pickTarget(bounds, minDistance) {
    const targetBounds = bounds || getBounds();
    const requiredDistance = minDistance ?? DEFAULTS.minWalkDistance;
    const attempts = 8;

    for (let index = 0; index < attempts; index += 1) {
      const candidate = {
        x: randomBetween(targetBounds.minX, targetBounds.maxX),
        y: randomBetween(targetBounds.minY, targetBounds.maxY)
      };

      if (Math.hypot(candidate.x - state.x, candidate.y - state.y) >= requiredDistance) {
        return candidate;
      }
    }

    return {
      x: randomBetween(targetBounds.minX, targetBounds.maxX),
      y: randomBetween(targetBounds.minY, targetBounds.maxY)
    };
  }

  function pickTurnAction(forceBackTurn) {
    if (forceBackTurn) {
      state.lastTurnAction = 'directionBack';
      return 'directionBack';
    }

    state.lastTurnAction = state.lastTurnAction === 'direction' ? 'directionBack' : 'direction';
    return state.lastTurnAction;
  }

  function queueTurn(targetDirection, forceBackTurn) {
    if (targetDirection === state.direction) {
      return;
    }

    queueAction(pickTurnAction(forceBackTurn), {
      direction: targetDirection,
      onComplete: (action) => {
        if (typeof action.direction === 'number') {
          state.direction = action.direction;
        }
      }
    });
  }

  function queueSleep(durationMs) {
    queueAction('sleep', {
      durationMs: durationMs ?? randomBetween(DEFAULTS.sleepMinMs, DEFAULTS.sleepMaxMs)
    });
  }

  function getTravelSpeed(mode) {
    if (mode === 'run') {
      return randomBetween(DEFAULTS.minRunSpeed, DEFAULTS.maxRunSpeed);
    }

    return randomBetween(DEFAULTS.minWalkSpeed, DEFAULTS.maxWalkSpeed);
  }

  function queueTravel(mode, target, options) {
    const overrides = options || {};
    const travelTarget = target || pickTarget(
      null,
      mode === 'run' ? DEFAULTS.minRunDistance : DEFAULTS.minWalkDistance
    );
    const nextDirection = travelTarget.x < state.x ? 1 : -1;

    queueTurn(nextDirection, overrides.forceBackTurn);
    queueAction(mode, {
      speed: getTravelSpeed(mode),
      target: travelTarget
    });
  }

  function queueAutonomousPlan() {
    if (state.activeAction || state.actionQueue.length) {
      return;
    }

    if (Math.random() < DEFAULTS.sleepChance) {
      queueSleep();
      return;
    }

    queueTravel(Math.random() < DEFAULTS.runChance ? 'run' : 'walk');
  }

  function moveAction(action, deltaSeconds, timestamp) {
    if (!action.target) {
      finishActiveAction(timestamp, 'missing-target');
      return;
    }

    const dx = action.target.x - state.x;
    const dy = action.target.y - state.y;
    const distance = Math.hypot(dx, dy);

    action.lastVector = { dx: dx, dy: dy };

    if (distance <= 2) {
      state.x = action.target.x;
      state.y = action.target.y;
      finishActiveAction(timestamp, 'arrived');
      return;
    }

    state.direction = dx < 0 ? 1 : -1;

    const step = Math.min(distance, action.speed * deltaSeconds);
    state.x += (dx / distance) * step;
    state.y += (dy / distance) * step;
  }

  function animateAction(action, deltaMs, timestamp) {
    action.elapsedMs += deltaMs;
    action.frameElapsedMs += deltaMs;

    if (action.name === 'walk' || action.name === 'run') {
      moveAction(action, deltaMs / 1000, timestamp);

      if (!state.activeAction) {
        return;
      }
    }

    while (action.frameElapsedMs >= action.frameMs) {
      action.frameElapsedMs -= action.frameMs;

      if (action.loop) {
        action.frameIndex = (action.frameIndex + 1) % action.frames.length;
        setFrame(action.frames[action.frameIndex]);
        continue;
      }

      if (action.frameIndex < action.frames.length - 1) {
        action.frameIndex += 1;
        setFrame(action.frames[action.frameIndex]);
      }
    }

    if (action.loop && action.durationMs !== null && action.elapsedMs >= action.durationMs) {
      finishActiveAction(timestamp, 'timeout');
      return;
    }

    if (!action.loop && action.elapsedMs >= action.frames.length * action.frameMs) {
      finishActiveAction(timestamp, 'complete');
    }
  }

  function didHitActiveBound(hitBounds, dx, dy) {
    return (hitBounds.left && dx < 0)
      || (hitBounds.right && dx > 0)
      || (hitBounds.top && dy < 0)
      || (hitBounds.bottom && dy > 0);
  }

  function pickEscapeTarget(hitBounds, mode) {
    const bounds = getBounds();
    const minimumDistance = mode === 'run' ? DEFAULTS.minRunDistance : DEFAULTS.minWalkDistance;
    const horizontalInset = Math.min(
      (bounds.maxX - bounds.minX) / 2,
      Math.max(minimumDistance * 0.6, DEFAULTS.edgeRetargetMinInset)
    );
    const verticalInset = Math.min(
      (bounds.maxY - bounds.minY) / 2,
      Math.max(minimumDistance * 0.45, DEFAULTS.edgeRetargetVerticalInset)
    );
    const targetBounds = {
      minX: hitBounds.left ? Math.min(bounds.maxX, bounds.minX + horizontalInset) : bounds.minX,
      maxX: hitBounds.right ? Math.max(bounds.minX, bounds.maxX - horizontalInset) : bounds.maxX,
      minY: hitBounds.top ? Math.min(bounds.maxY, bounds.minY + verticalInset) : bounds.minY,
      maxY: hitBounds.bottom ? Math.max(bounds.minY, bounds.maxY - verticalInset) : bounds.maxY
    };

    return pickTarget(targetBounds, minimumDistance);
  }

  function queueBoundRecovery(action, hitBounds) {
    const recoveryMode = action.name === 'run' ? 'run' : 'walk';

    cancelActiveAction();
    clearQueuedActions();
    queueAction('bump');
    queueTravel(recoveryMode, pickEscapeTarget(hitBounds, recoveryMode), {
      forceBackTurn: true
    });
  }

  function tick(timestamp) {
    state.animationFrame = 0;

    if (state.modalOpen || state.reducedMotion || !sprite) {
      state.lastTimestamp = 0;
      return;
    }

    if (!state.lastTimestamp) {
      state.lastTimestamp = timestamp;
    }

    const deltaMs = Math.min(50, timestamp - state.lastTimestamp);
    state.lastTimestamp = timestamp;

    queueAutonomousPlan();
    startNextAction();

    if (state.activeAction) {
      animateAction(state.activeAction, deltaMs, timestamp);
    }

    const hitBounds = clampPosition();
    const action = state.activeAction;

    if (
      action?.target
      && didHitActiveBound(hitBounds, action.lastVector.dx, action.lastVector.dy)
    ) {
      queueBoundRecovery(action, hitBounds);
    }

    queueAutonomousPlan();
    startNextAction();
    applyPosition();
    state.animationFrame = window.requestAnimationFrame(tick);
  }

  function startLoop() {
    if (state.animationFrame || state.modalOpen || state.reducedMotion) {
      return;
    }

    state.lastTimestamp = 0;
    state.animationFrame = window.requestAnimationFrame(tick);
  }

  function stopLoop() {
    if (!state.animationFrame) {
      return;
    }

    window.cancelAnimationFrame(state.animationFrame);
    state.animationFrame = 0;
    state.lastTimestamp = 0;
  }

  function syncPresentation() {
    if (!layer || !sprite) {
      return;
    }

    const shouldSuspend = state.modalOpen || state.reducedMotion;
    layer.classList.toggle('is-suspended', shouldSuspend);

    if (shouldSuspend) {
      stopLoop();
      setFrame(NEUTRAL_FRAME, true);
      return;
    }

    startLoop();
  }

  function updateModalState(isOpen) {
    state.modalOpen = isOpen;
    syncPresentation();
  }

  function onModalShown(event) {
    const modal = event.target;

    if (!(modal instanceof HTMLElement)) {
      return;
    }

    if (modal.querySelector('.modal-dialog.modal-fullscreen')) {
      updateModalState(true);
    }
  }

  function onModalHidden(event) {
    const modal = event.target;

    if (!(modal instanceof HTMLElement)) {
      return;
    }

    if (!modal.querySelector('.modal-dialog.modal-fullscreen')) {
      return;
    }

    const fullscreenModalStillOpen = Boolean(document.querySelector('.modal.show .modal-dialog.modal-fullscreen'));
    updateModalState(fullscreenModalStillOpen);
  }

  function onReducedMotionChange(event) {
    state.reducedMotion = event.matches;
    syncPresentation();
  }

  function onResize() {
    clampPosition();
    setFrame(state.currentFrame ?? NEUTRAL_FRAME, true);
    applyPosition();
  }

  function createLayer() {
    layer = document.createElement('div');
    layer.className = 'sheep-layer';
    layer.setAttribute('aria-hidden', 'true');

    sprite = document.createElement('div');
    sprite.className = 'sheep-layer__sprite';
    sprite.setAttribute('aria-hidden', 'true');
    sprite.style.backgroundImage = `url('${SPRITE_SHEET_URL}')`;
    sprite.style.backgroundSize = `calc(var(--sheep-size) * ${SPRITE_COLUMNS}) calc(var(--sheep-size) * ${SPRITE_ROWS})`;

    layer.appendChild(sprite);
    document.body.appendChild(layer);
  }

  function seedInitialPosition() {
    const bounds = getBounds();

    state.x = bounds.maxX;
    state.y = randomBetween(bounds.minY, bounds.maxY);
    state.direction = 1;
    queueSleep(randomBetween(900, 2000));
    startNextAction();
    clampPosition();
    applyPosition();
  }

  function bindEvents() {
    document.addEventListener('show.bs.modal', onModalShown);
    document.addEventListener('hidden.bs.modal', onModalHidden);
    window.addEventListener('resize', onResize, { passive: true });

    if (typeof prefersReducedMotion.addEventListener === 'function') {
      prefersReducedMotion.addEventListener('change', onReducedMotionChange);
      return;
    }

    if (typeof prefersReducedMotion.addListener === 'function') {
      prefersReducedMotion.addListener(onReducedMotionChange);
    }
  }

  function init() {
    if (!document.body) {
      return;
    }

    if (document.querySelector('.sheep-layer')) {
      return;
    }

    createLayer();
    seedInitialPosition();
    bindEvents();
    syncPresentation();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})(window.SheepApp);
