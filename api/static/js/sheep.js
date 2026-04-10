window.SheepApp = window.SheepApp || {};
window.SheepInternals = window.SheepInternals || {};

((app, internals) => {
  if (app.initialized) {
    return;
  }

  if (
    typeof internals.createActionCatalog !== 'function'
    || typeof internals.createSurfacePlanner !== 'function'
    || typeof internals.createRuntimeEngine !== 'function'
    || typeof internals.createPresentation !== 'function'
  ) {
    console.warn('Sheep internals are missing required factories.');
    return;
  }

  app.initialized = true;

  const config = Object.freeze({
    SPRITE_SHEET_URL: '/static/vendor/sheep/rsc/sheep.png',
    SPRITE_COLUMNS: 16,
    SPRITE_ROWS: 11,
    NEUTRAL_FRAME: 3,
    SURFACE_SELECTOR: '.sheep-surface',
    GROUND_SURFACE_ID: 'ground'
  });
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const preferenceStorage = window.StreamApp && window.StreamApp.preferences;
  const sheepPreferenceKey = preferenceStorage && preferenceStorage.keys
    ? preferenceStorage.keys.sheepEnabled
    : 'stream.sheepEnabled';
  const state = {
    x: 0,
    y: 0,
    direction: 1,
    currentFrame: null,
    lastTimestamp: 0,
    animationFrame: 0,
    reducedMotion: prefersReducedMotion.matches,
    modalOpen: false,
    enabled: true,
    sheepVisible: true,
    abducted: false,
    abductedReturnAt: 0,
    activeAction: null,
    actionQueue: [],
    lastTurnAction: 'directionBack',
    currentSurfaceId: config.GROUND_SURFACE_ID,
    prop: {
      visible: false,
      currentFrame: null,
      offsetX: 0,
      offsetY: 0,
      attachToFacing: false,
      flipWithDirection: false
    },
    secondaryProp: {
      visible: false,
      currentFrame: null,
      offsetX: 0,
      offsetY: 0,
      attachToFacing: false,
      flipWithDirection: false
    }
  };
  const refs = {
    layer: null,
    sprite: null,
    propSprite: null,
    secondaryPropSprite: null
  };

  function randomBetween(min, max) {
    return min + (Math.random() * (max - min));
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function composeCallbacks(...callbacks) {
    const activeCallbacks = callbacks.filter((callback) => typeof callback === 'function');

    if (!activeCallbacks.length) {
      return;
    }

    return (...args) => {
      activeCallbacks.forEach((callback) => {
        callback(...args);
      });
    };
  }

  const context = {
    window,
    document,
    app,
    config,
    state,
    refs,
    prefersReducedMotion,
    preferenceStorage,
    sheepPreferenceKey,
    helpers: {
      clamp,
      composeCallbacks,
      randomBetween
    },
    effects: {},
    services: {}
  };

  context.effects = Object.freeze({
    queueAction(name, overrides) {
      context.services.runtimeEngine.queueAction(name, overrides);
    },
    queueSleep(durationMs) {
      context.services.runtimeEngine.queueSleep(durationMs);
    },
    getBounds() {
      return context.services.surfacePlanner.getBounds();
    },
    showProp(frame, preset) {
      context.services.presentation.showProp(frame, preset);
    },
    showSecondaryProp(frame, preset) {
      context.services.presentation.showSecondaryProp(frame, preset);
    },
    showSheep() {
      context.services.presentation.showSheep();
    },
    hideSheep() {
      context.services.presentation.hideSheep();
    },
    hideProp() {
      context.services.presentation.hideProp();
    },
    hideSecondaryProp() {
      context.services.presentation.hideSecondaryProp();
    }
  });

  const presentation = internals.createPresentation(context);
  context.services.presentation = presentation;

  const actionCatalog = internals.createActionCatalog(context);
  context.services.actionCatalog = actionCatalog;

  const surfacePlanner = internals.createSurfacePlanner(context);
  context.services.surfacePlanner = surfacePlanner;

  const runtimeEngine = internals.createRuntimeEngine(context);
  context.services.runtimeEngine = runtimeEngine;

  function readStoredEnabledPreference() {
    if (preferenceStorage && typeof preferenceStorage.getBoolean === 'function') {
      const storedValue = preferenceStorage.getBoolean(sheepPreferenceKey);
      return storedValue === null ? true : storedValue;
    }

    try {
      const storedValue = window.localStorage.getItem(sheepPreferenceKey);

      if (storedValue === 'true') {
        return true;
      }

      if (storedValue === 'false') {
        return false;
      }
    } catch (error) {
      console.warn('Sheep preference read failed:', error);
    }

    return true;
  }

  function writeStoredEnabledPreference(enabled) {
    if (preferenceStorage && typeof preferenceStorage.setBoolean === 'function') {
      preferenceStorage.setBoolean(sheepPreferenceKey, enabled);
      return;
    }

    try {
      window.localStorage.setItem(sheepPreferenceKey, enabled ? 'true' : 'false');
    } catch (error) {
      console.warn('Sheep preference write failed:', error);
    }
  }

  function seedInitialPosition() {
    const bounds = surfacePlanner.getBounds();
    const groundSurface = surfacePlanner.getGroundSurface(bounds);

    state.x = groundSurface.maxX;
    state.y = groundSurface.landY;
    state.direction = 1;
    surfacePlanner.setCurrentSurface(groundSurface);
    runtimeEngine.startNextAction();
    runtimeEngine.clampPosition();
    presentation.applyPosition();
  }

  function ensureInitialized() {
    const layerState = presentation.ensureLayer();

    if (!layerState) {
      return;
    }

    if (!layerState.created) {
      presentation.bindEvents();
      presentation.syncPresentation();
      return;
    }

    surfacePlanner.refreshSurfaces();
    seedInitialPosition();
    presentation.bindEvents();
    presentation.syncPresentation();
  }

  function setEnabled(enabled) {
    state.enabled = Boolean(enabled);
    writeStoredEnabledPreference(state.enabled);

    if (state.enabled) {
      ensureInitialized();
    }

    presentation.syncPresentation();
    return state.enabled;
  }

  function init() {
    state.enabled = readStoredEnabledPreference();

    if (!state.enabled) {
      return;
    }

    ensureInitialized();
  }

  app.enable = function enableSheep() {
    return setEnabled(true);
  };

  app.disable = function disableSheep() {
    return setEnabled(false);
  };

  app.toggle = function toggleSheep() {
    return setEnabled(!state.enabled);
  };

  app.isEnabled = function isSheepEnabled() {
    return state.enabled;
  };

  app.refreshSurfaces = function refreshSheepSurfaces() {
    surfacePlanner.refreshSurfaces();

    if (!state.activeAction) {
      surfacePlanner.snapToCurrentSurface();
      runtimeEngine.clampPosition();
      presentation.applyPosition();
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})(window.SheepApp, window.SheepInternals);
