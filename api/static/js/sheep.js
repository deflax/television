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

  const SHEEP_SPAWN_INTERVAL_MS = 20 * 60 * 1000;
  const MAX_SHEEP_COUNT = 3;
  const MANUAL_MAX_SHEEP_COUNT = 6;
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
  const manager = {
    enabled: true,
    instances: [],
    nextSheepId: 1,
    manualCapResetPending: false,
    spawnTimer: 0
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
      return undefined;
    }

    return (...args) => {
      activeCallbacks.forEach((callback) => {
        callback(...args);
      });
    };
  }

  function createSheepState() {
    return {
      x: 0,
      y: 0,
      direction: 1,
      currentFrame: null,
      lastTimestamp: 0,
      animationFrame: 0,
      reducedMotion: prefersReducedMotion.matches,
      modalOpen: false,
      menuOpen: false,
      enabled: manager.enabled,
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
  }

  function createSheepRefs() {
    return {
      layer: null,
      sprite: null,
      propSprite: null,
      secondaryPropSprite: null,
      menu: null
    };
  }

  function createSheepInstance(id) {
    const state = createSheepState();
    const refs = createSheepRefs();
    const context = {
      window,
      document,
      app,
      config,
      sheepId: id,
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
      spawnSheep: (options) => spawnSheep(options),
      spawnManualSheep: spawnManualSheep,
      resetSheepInstancesToPrimary: resetSheepInstancesToPrimary,
      triggerManualCapAlienVisitReset: triggerManualCapAlienVisitReset,
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

      if (state.enabled) {
        ensureInitialized();
      }

      presentation.syncPresentation();
      return state.enabled;
    }

    function refreshSurfaces() {
      surfacePlanner.refreshSurfaces();

      if (!state.activeAction) {
        surfacePlanner.snapToCurrentSurface();
        runtimeEngine.clampPosition();
        presentation.applyPosition();
      }
    }

    function destroy() {
      runtimeEngine.stopLoop();
      runtimeEngine.cancelActiveAction();
      runtimeEngine.clearQueuedActions();
      presentation.destroy();
    }

    return Object.freeze({
      actionCatalog,
      destroy,
      ensureInitialized,
      refreshSurfaces,
      runtimeEngine,
      setEnabled,
      state
    });
  }

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

  function getPrimarySheep() {
    return manager.instances[0] || null;
  }

  function resetSheepInstancesToPrimary() {
    const primarySheep = getPrimarySheep();

    manager.instances.slice(1).forEach((sheep) => {
      sheep.destroy();
    });
    manager.instances = primarySheep ? [primarySheep] : [];
    manager.manualCapResetPending = false;

    if (manager.enabled) {
      ensurePrimarySheep();
      syncInstancesEnabled(true);
      startSpawnTimer();
    }
  }

  function triggerManualCapAlienVisitReset() {
    const sheepSnapshot = manager.instances.slice();
    let pendingCompletions = 0;
    let resetApplied = false;

    function resetAfterGroup() {
      if (resetApplied) {
        return;
      }

      resetApplied = true;
      resetSheepInstancesToPrimary();
    }

    sheepSnapshot.forEach((sheep) => {
      const triggered = sheep.runtimeEngine.triggerMenuAction('alienVisit', {
        onComplete: () => {
          pendingCompletions -= 1;

          if (pendingCompletions === 0) {
            resetAfterGroup();
          }
        }
      });

      if (triggered) {
        pendingCompletions += 1;
      }
    });

    if (pendingCompletions === 0) {
      resetAfterGroup();
    }
  }

  function spawnSheep(options) {
    const { maxCount = MAX_SHEEP_COUNT } = options || {};

    if (!manager.enabled || manager.instances.length >= maxCount) {
      return null;
    }

    const sheep = createSheepInstance(manager.nextSheepId);
    manager.nextSheepId += 1;
    manager.instances.push(sheep);
    sheep.ensureInitialized();

    if (manager.instances.length >= MAX_SHEEP_COUNT) {
      stopSpawnTimer();
    }

    return sheep;
  }

  function spawnManualSheep() {
    if (manager.manualCapResetPending) {
      return { spawned: false, reachedCap: false };
    }

    const sheep = spawnSheep({ maxCount: MANUAL_MAX_SHEEP_COUNT });
    const reachedCap = Boolean(sheep && manager.instances.length >= MANUAL_MAX_SHEEP_COUNT);

    if (reachedCap) {
      manager.manualCapResetPending = true;
    }

    return { spawned: Boolean(sheep), reachedCap };
  }

  function ensurePrimarySheep() {
    return getPrimarySheep() || spawnSheep();
  }

  function startSpawnTimer() {
    if (manager.spawnTimer || !manager.enabled || manager.instances.length >= MAX_SHEEP_COUNT) {
      return;
    }

    manager.spawnTimer = window.setInterval(spawnSheep, SHEEP_SPAWN_INTERVAL_MS);
  }

  function stopSpawnTimer() {
    if (!manager.spawnTimer) {
      return;
    }

    window.clearInterval(manager.spawnTimer);
    manager.spawnTimer = 0;
  }

  function syncInstancesEnabled(enabled) {
    manager.instances.forEach((sheep) => {
      sheep.setEnabled(enabled);
    });
  }

  function setEnabled(enabled) {
    manager.enabled = Boolean(enabled);
    writeStoredEnabledPreference(manager.enabled);

    if (manager.enabled) {
      ensurePrimarySheep();
      syncInstancesEnabled(true);
      startSpawnTimer();
      return true;
    }

    stopSpawnTimer();
    syncInstancesEnabled(false);
    return false;
  }

  function init() {
    manager.enabled = readStoredEnabledPreference();

    if (!manager.enabled) {
      return;
    }

    ensurePrimarySheep();
    startSpawnTimer();
  }

  app.enable = function enableSheep() {
    return setEnabled(true);
  };

  app.disable = function disableSheep() {
    return setEnabled(false);
  };

  app.toggle = function toggleSheep() {
    resetSheepInstancesToPrimary();
    return setEnabled(!manager.enabled);
  };

  app.isEnabled = function isSheepEnabled() {
    return manager.enabled;
  };

  app.refreshSurfaces = function refreshSheepSurfaces() {
    manager.instances.forEach((sheep) => {
      sheep.refreshSurfaces();
    });
  };

  app.getSpecialActions = function getSheepSpecialActions() {
    const sheep = getPrimarySheep();

    if (!sheep) {
      return [];
    }

    return sheep.actionCatalog.getSpecialActions().map((entry) => entry.name);
  };

  app.triggerSpecialAction = function triggerSheepSpecialAction(name) {
    const sheep = getPrimarySheep();

    if (!sheep) {
      return false;
    }
    return sheep.runtimeEngine.triggerSpecialAction(name);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }
})(window.SheepApp, window.SheepInternals);
