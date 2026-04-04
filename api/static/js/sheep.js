window.SheepApp = window.SheepApp || {};

((app) => {
  if (app.initialized) {
    return;
  }

  app.initialized = true;

  const SPRITE_SHEET_URL = '/static/vendor/sheep/rsc/sheep.png';
  const SPRITE_COLUMNS = 16;
  const SPRITE_ROWS = 11;
  const FRAMES = Object.freeze({
    idle: 3,
    walkA: 2,
    walkB: 3
  });

  const DEFAULTS = Object.freeze({
    minSpeed: 40,
    maxSpeed: 66,
    restMinMs: 900,
    restMaxMs: 2200,
    minTravelDistance: 120
  });

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const state = {
    x: 0,
    y: 0,
    direction: 1,
    mode: 'rest',
    speed: DEFAULTS.minSpeed,
    target: null,
    restUntil: 0,
    currentFrame: null,
    lastTimestamp: 0,
    animationFrame: 0,
    walkFrameElapsed: 0,
    reducedMotion: prefersReducedMotion.matches,
    modalOpen: false
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

  function setFrame(frame, force = false) {
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

  function pickTarget(bounds = getBounds()) {
    const attempts = 8;

    for (let index = 0; index < attempts; index += 1) {
      const candidate = {
        x: randomBetween(bounds.minX, bounds.maxX),
        y: randomBetween(bounds.minY, bounds.maxY)
      };

      if (Math.hypot(candidate.x - state.x, candidate.y - state.y) >= DEFAULTS.minTravelDistance) {
        return candidate;
      }
    }

    return {
      x: randomBetween(bounds.minX, bounds.maxX),
      y: randomBetween(bounds.minY, bounds.maxY)
    };
  }

  function didHitActiveBound(hitBounds, dx, dy) {
    return (hitBounds.left && dx < 0)
      || (hitBounds.right && dx > 0)
      || (hitBounds.top && dy < 0)
      || (hitBounds.bottom && dy > 0);
  }

  function retargetFromBounds(hitBounds) {
    const bounds = getBounds();
    const horizontalInset = Math.min(
      (bounds.maxX - bounds.minX) / 2,
      Math.max(DEFAULTS.minTravelDistance * 0.6, 48)
    );
    const verticalInset = Math.min(
      (bounds.maxY - bounds.minY) / 2,
      Math.max(DEFAULTS.minTravelDistance * 0.45, 40)
    );

    const targetBounds = {
      minX: hitBounds.left ? Math.min(bounds.maxX, bounds.minX + horizontalInset) : bounds.minX,
      maxX: hitBounds.right ? Math.max(bounds.minX, bounds.maxX - horizontalInset) : bounds.maxX,
      minY: hitBounds.top ? Math.min(bounds.maxY, bounds.minY + verticalInset) : bounds.minY,
      maxY: hitBounds.bottom ? Math.max(bounds.minY, bounds.maxY - verticalInset) : bounds.maxY
    };

    state.target = pickTarget(targetBounds);
    state.walkFrameElapsed = 0;
    setFrame(FRAMES.walkA);
  }

  function scheduleNextWalk(timestamp) {
    state.mode = 'rest';
    state.target = null;
    state.restUntil = timestamp + randomBetween(DEFAULTS.restMinMs, DEFAULTS.restMaxMs);
    state.walkFrameElapsed = 0;
    setFrame(FRAMES.idle);
  }

  function ensureWalkingState() {
    if (state.target) {
      return;
    }

    state.mode = 'walk';
    state.speed = randomBetween(DEFAULTS.minSpeed, DEFAULTS.maxSpeed);
    state.target = pickTarget();
    state.walkFrameElapsed = 0;
    setFrame(FRAMES.walkA);
  }

  function tick(timestamp) {
    state.animationFrame = 0;
    let movementDx = 0;
    let movementDy = 0;

    if (state.modalOpen || state.reducedMotion || !sprite) {
      state.lastTimestamp = 0;
      return;
    }

    if (!state.lastTimestamp) {
      state.lastTimestamp = timestamp;
    }

    const deltaSeconds = Math.min(0.05, (timestamp - state.lastTimestamp) / 1000);
    state.lastTimestamp = timestamp;

    if (state.mode === 'rest') {
      setFrame(FRAMES.idle);
      if (timestamp >= state.restUntil) {
        ensureWalkingState();
      }
    }

    if (state.mode === 'walk' && state.target) {
      state.walkFrameElapsed += deltaSeconds;

      if (state.walkFrameElapsed >= 0.18) {
        state.walkFrameElapsed = 0;
        setFrame(state.currentFrame === FRAMES.walkA ? FRAMES.walkB : FRAMES.walkA);
      }

      const dx = state.target.x - state.x;
      const dy = state.target.y - state.y;
      const distance = Math.hypot(dx, dy);

      if (distance <= 2) {
        state.x = state.target.x;
        state.y = state.target.y;
        scheduleNextWalk(timestamp);
      } else {
        const step = Math.min(distance, state.speed * deltaSeconds);
        movementDx = dx;
        movementDy = dy;
        state.direction = dx < 0 ? 1 : -1;
        state.x += (dx / distance) * step;
        state.y += (dy / distance) * step;
      }
    }

    const hitBounds = clampPosition();

    if (state.mode === 'walk' && state.target && didHitActiveBound(hitBounds, movementDx, movementDy)) {
      retargetFromBounds(hitBounds);
    }

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
      setFrame(FRAMES.idle);
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
    setFrame(state.currentFrame ?? FRAMES.idle, true);
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
    scheduleNextWalk(performance.now());
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
