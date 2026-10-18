window.SheepInternals = window.SheepInternals || {};

((internals) => {
  function createRuntimeEngine(context) {
    const { window, state, helpers, effects, services } = context;
    const { randomBetween, clamp, composeCallbacks } = helpers;
    let lastSideEffectActionTimestamp = null;
    let collisionCooldownUntil = 0;

    function getDefaults() {
      return services.actionCatalog.DEFAULTS;
    }

    function getCurrentFrameDuration(action) {
      return action.frameDurations?.[action.frameIndex] ?? action.frameMs;
    }

    function buildAction(name, overrides) {
      const definition = services.actionCatalog.getActionDefinition(name);
      const options = overrides || {};

      return {
        name,
        frames: definition.frames.slice(),
        frameMs: definition.frameMs,
        frameDurations: definition.frameDurations ? definition.frameDurations.slice() : null,
        frameEvents: definition.frameEvents ? { ...definition.frameEvents } : null,
        loop: definition.loop,
        durationMs: options.durationMs ?? null,
        speed: options.speed ?? 0,
        target: options.target ?? null,
        path: options.path ? options.path.map((point) => ({ x: point.x, y: point.y })) : null,
        direction: typeof options.direction === 'number' ? options.direction : null,
        motionEnabled: options.motionEnabled ?? true,
        arrivalPoint: options.arrivalPoint ?? null,
        arrivalSurfaceId: options.arrivalSurfaceId ?? null,
        keepPlayingOnArrival: options.keepPlayingOnArrival ?? definition.keepPlayingOnArrival,
        waitForLanding: options.waitForLanding ?? definition.waitForLanding,
        waitForMotionCompletion: options.waitForMotionCompletion === true,
        capReset: options.capReset === true,
        onStart: composeCallbacks(definition.onStart, options.onStart),
        onComplete: composeCallbacks(definition.onComplete, options.onComplete),
        onCancel: options.onCancel
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
        frameDurations: action.frameDurations,
        frameEvents: action.frameEvents,
        loop: action.loop,
        durationMs: action.durationMs,
        speed: action.speed,
        target: action.target,
        path: action.path,
        direction: action.direction,
        motionEnabled: action.motionEnabled,
        arrivalPoint: action.arrivalPoint,
        arrivalSurfaceId: action.arrivalSurfaceId,
        keepPlayingOnArrival: action.keepPlayingOnArrival,
        waitForLanding: action.waitForLanding,
        waitForMotionCompletion: action.waitForMotionCompletion,
        capReset: action.capReset,
        onStart: action.onStart,
        onComplete: action.onComplete,
        onCancel: action.onCancel,
        frameIndex: 0,
        frameElapsedMs: 0,
        elapsedMs: 0,
        lastVector: { dx: 0, dy: 0 }
      };

      if (typeof state.activeAction.onStart === 'function') {
        state.activeAction.onStart(state.activeAction);
      }

      if (!state.activeAction.target && state.activeAction.path?.length) {
        state.activeAction.target = state.activeAction.path.shift();
      }

      services.presentation.enterActionFrame(state.activeAction, true);
    }

    function finishActiveAction(timestamp, reason) {
      const action = state.activeAction;

      if (!action) {
        return;
      }

      state.activeAction = null;

      if (action.arrivalSurfaceId) {
        state.currentSurfaceId = action.arrivalSurfaceId;
      }

      if (action.arrivalPoint) {
        state.x = action.arrivalPoint.x;
        state.y = action.arrivalPoint.y;
      }

      if (typeof action.onComplete === 'function') {
        action.onComplete(action, timestamp, reason);
      }

      services.presentation.applyPosition();

      if (action.name === 'alienVisit' && typeof effects.handleAlienVisitComplete === 'function') {
        effects.handleAlienVisitComplete({ capReset: action.capReset });
      }
    }

    function cancelActiveAction(reason = 'cancelled') {
      const action = state.activeAction;
      state.activeAction = null;

      if (!action) {
        return;
      }

      services.presentation.hideProp();
      services.presentation.hideSecondaryProp();

      if (typeof action.onCancel === 'function') {
        action.onCancel(action, state.lastTimestamp, reason);
      }
    }

    function pickTarget(surface, minDistance) {
      const targetSurface = surface || services.surfacePlanner.getCurrentSurface();
      const defaults = getDefaults();
      const availableDistance = Math.max(24, targetSurface.maxX - targetSurface.minX);
      const requiredDistance = Math.min(minDistance ?? defaults.minWalkDistance, availableDistance);
      const attempts = 8;

      for (let index = 0; index < attempts; index += 1) {
        const candidate = {
          x: randomBetween(targetSurface.minX, targetSurface.maxX),
          y: targetSurface.landY
        };

        if (Math.abs(candidate.x - state.x) >= requiredDistance) {
          return candidate;
        }
      }

      return {
        x: randomBetween(targetSurface.minX, targetSurface.maxX),
        y: targetSurface.landY
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
      const defaults = getDefaults();

      queueAction('sleep', {
        durationMs: durationMs ?? randomBetween(defaults.sleepMinMs, defaults.sleepMaxMs)
      });
    }

    function pickWeightedAction(entries) {
      const totalWeight = entries.reduce((sum, entry) => sum + entry.weight, 0);
      let cursor = Math.random() * totalWeight;

      for (let index = 0; index < entries.length; index += 1) {
        cursor -= entries[index].weight;

        if (cursor <= 0) {
          return entries[index].name;
        }
      }

      return entries[entries.length - 1].name;
    }

    function queueRollAction() {
      const defaults = getDefaults();
      const surface = services.surfacePlanner.getCurrentSurface();
      const leftSpace = Math.max(0, state.x - surface.minX);
      const rightSpace = Math.max(0, surface.maxX - state.x);
      const preferredDirection = state.direction === 1
        ? (leftSpace >= defaults.minRollDistance ? 1 : -1)
        : (rightSpace >= defaults.minRollDistance ? -1 : 1);
      const targetDirection = preferredDirection === 1 && leftSpace >= rightSpace
        ? 1
        : preferredDirection === -1 && rightSpace >= leftSpace
          ? -1
          : (leftSpace >= rightSpace ? 1 : -1);
      const availableDistance = targetDirection === 1 ? leftSpace : rightSpace;
      const travelDistance = Math.min(defaults.rollTravelDistance, availableDistance);

      if (travelDistance < defaults.minRollDistance) {
        queueTravel('walk');
        return;
      }

      queueTurn(targetDirection);
      queueAction('roll', {
        speed: travelDistance / (defaults.rollTravelMs / 1000),
        target: {
          x: state.x + (targetDirection === 1 ? -travelDistance : travelDistance),
          y: surface.landY
        }
      });
    }

    function queueSpecialActionPlan(timestamp) {
      const actionName = pickWeightedAction(services.actionCatalog.getSpecialActions());

      if (actionName === 'roll') {
        queueRollAction();
        return;
      }

      if (actionName === 'spawn') {
        effects.spawnManualSheep();
        lastSideEffectActionTimestamp = timestamp;
        return;
      }

      queueAction(actionName);
    }

    function isMenuAction(name) {
      return services.actionCatalog.getMenuActions().some((entry) => entry.name === name);
    }

    function canTriggerSpecialAction(name) {
      if (!isMenuAction(name)) {
        return false;
      }

      if (
        !state.enabled
        || state.modalOpen
        || state.reducedMotion
        || !services.presentation.hasSprite()
      ) {
        return false;
      }

      return true;
    }

    function canTriggerMenuAction(name) {
      return name !== 'spawn' && canTriggerSpecialAction(name);
    }

    function triggerSpecialAction(name) {
      if (!canTriggerSpecialAction(name)) {
        return false;
      }

      if (name === 'spawn') {
        const spawnResult = effects.spawnManualSheep();

        if (!spawnResult.spawned) {
          return false;
        }

        return true;
      }

      return triggerMenuAction(name);
    }

    function triggerMenuAction(name, overrides) {
      if (!canTriggerMenuAction(name)) {
        return false;
      }

      const actionOptions = overrides || {};

      cancelActiveAction();
      clearQueuedActions();
      services.surfacePlanner.refreshSurfaces();
      services.surfacePlanner.snapToCurrentSurface();
      clampPosition();

      state.abducted = false;
      state.abductedReturnAt = 0;
      services.presentation.showSheep();
      services.presentation.hideProp();
      services.presentation.hideSecondaryProp();

      if (name === 'roll') {
        queueRollAction();
      } else {
        queueAction(name, actionOptions);
      }

      startNextAction();
      services.presentation.applyPosition();
      services.presentation.syncPresentation();
      return true;
    }

    function queueMarkedSurfaceDwellPlan(timestamp) {
      const defaults = getDefaults();
      const surface = services.surfacePlanner.getCurrentSurface();
      const availableDistance = Math.max(0, surface.maxX - surface.minX);

      if (availableDistance >= defaults.minSurfaceWidth && Math.random() < 0.35) {
        queueTravel('walk', pickTarget(surface, Math.min(defaults.markedSurfaceMinWalkDistance, availableDistance * 0.35)));
        return;
      }

      queueSpecialActionPlan(timestamp);
    }

    function getTravelSpeed(mode) {
      const defaults = getDefaults();

      if (mode === 'run') {
        return randomBetween(defaults.minRunSpeed, defaults.maxRunSpeed);
      }

      return randomBetween(defaults.minWalkSpeed, defaults.maxWalkSpeed);
    }

    function queueTravel(mode, target, options) {
      const defaults = getDefaults();
      const overrides = options || {};
      const travelTarget = target || pickTarget(
        services.surfacePlanner.getCurrentSurface(),
        mode === 'run' ? defaults.minRunDistance : defaults.minWalkDistance
      );
      const horizontalDistance = travelTarget.x - state.x;
      const nextDirection = Math.abs(horizontalDistance) <= 1
        ? state.direction
        : (horizontalDistance < 0 ? 1 : -1);

      queueTurn(nextDirection, overrides.forceBackTurn);
      queueAction(mode, {
        speed: getTravelSpeed(mode),
        target: travelTarget
      });
    }

    function queueAutonomousPlan(timestamp) {
      const defaults = getDefaults();

      if (lastSideEffectActionTimestamp === timestamp) {
        return;
      }

      if (state.activeAction || state.actionQueue.length) {
        return;
      }

      if (state.abducted) {
        if (timestamp < state.abductedReturnAt) {
          return;
        }

        queueAction('meteor');
        return;
      }

      services.surfacePlanner.refreshSurfaces();

      const currentSurface = services.surfacePlanner.getCurrentSurface();

      if (services.surfacePlanner.isMarkedSurface(currentSurface) && Math.random() < defaults.markedSurfaceDwellChance) {
        queueMarkedSurfaceDwellPlan(timestamp);
        return;
      }

      if (
        Math.random() < defaults.surfaceActionChance
        && services.surfacePlanner.queueSurfaceTraversalPlan(services.surfacePlanner.pickSurfaceTraversalPlan())
      ) {
        return;
      }

      if (Math.random() < defaults.specialActionChance) {
        queueSpecialActionPlan(timestamp);
        return;
      }

      queueTravel(Math.random() < defaults.runChance ? 'run' : 'walk');
    }

    function queueFall(targetX = state.x, bounce = false) {
      queueAction('jumpDown', {
        onStart: (fall) => {
          const ground = services.surfacePlanner.getGroundSurface();
          fall.motionEnabled = true;
          const landing = {
            x: services.surfacePlanner.clampXToSurface(ground, targetX),
            y: ground.landY
          };
          if (bounce) {
            const bounds = services.surfacePlanner.getBounds();
            const lift = services.presentation.getSpriteMetrics().height * 0.5;
            fall.target = { x: landing.x, y: Math.max(bounds.minY, state.y - lift) };
            fall.path = [landing];
          } else {
            fall.target = landing;
          }
          fall.speed = 140;
          fall.waitForLanding = true;
          fall.waitForMotionCompletion = true;
        }
      });
    }

    function recoverFromContact(action, contact, timestamp) {
      if (contact.wait || timestamp < collisionCooldownUntil) {
        return;
      }

      collisionCooldownUntil = timestamp + 1000;
      const airborne = Boolean(action.arrivalSurfaceId || action.waitForLanding);
      cancelActiveAction('collision');
      clearQueuedActions();

      if (airborne) {
        const width = services.presentation.getSpriteMetrics().width;
        const direction = contact.normal.x || (state.direction === 1 ? 1 : -1);
        const ground = services.surfacePlanner.getGroundSurface();
        let targetX = services.surfacePlanner.clampXToSurface(ground, state.x + direction * width);
        if (Math.abs(targetX - state.x) < width * 0.5) {
          targetX = services.surfacePlanner.clampXToSurface(ground, state.x - direction * width);
        }
        queueFall(targetX, contact.normal.y < 0);
        return;
      }

      const surface = services.surfacePlanner.getCurrentSurface();
      const distance = services.presentation.getSpriteMetrics().width * 1.5;
      const away = contact.normal.x || (state.direction === 1 ? 1 : -1);
      queueAction(contact.soft ? 'sleep' : 'bump', contact.soft ? { durationMs: 320 } : undefined);
      queueTravel('walk', {
        x: services.surfacePlanner.clampXToSurface(surface, state.x + away * distance),
        y: surface.landY
      });
    }

    function moveAction(action, deltaSeconds, timestamp) {
      function completeLanding(fromX, fromY, toX, toY) {
        const landingResult = services.surfacePlanner.findLandingSurfaceBetween(
          fromX, fromY, toX, toY,
          action.traversal ? state.currentSurfaceId : undefined
        );

        if (!landingResult) {
          return false;
        }

        const { surface: landingSurface, landingX } = landingResult;

        action.arrivalSurfaceId = landingSurface.id;
        action.arrivalPoint = {
          x: services.surfacePlanner.clampXToSurface(landingSurface, landingX),
          y: landingSurface.landY
        };
        finishActiveAction(timestamp, 'arrived');
        return true;
      }

      if (action.waitForLanding && !action.traversal && !action.path?.length && action.target) {
        action.target.y = services.surfacePlanner.getGroundSurface().landY;
      }

      if (!action.target) {
        if (action.waitForLanding) {
          return;
        }

        finishActiveAction(timestamp, 'missing-target');
        return;
      }

      const dx = action.target.x - state.x;
      const dy = action.target.y - state.y;
      const distance = Math.hypot(dx, dy);

      action.lastVector = { dx, dy };

      if (distance <= 2) {
        const previousX = state.x;
        const previousY = state.y;
        const result = services.collisionWorld.resolveMovement(
          context, { x: state.x, y: state.y }, action.target, timestamp
        );
        state.x = result.x;
        state.y = result.y;

        if (
          state.y > previousY
          && (action.waitForLanding || action.traversal)
          && completeLanding(previousX, previousY, state.x, state.y)
        ) {
          return;
        }

        if (result.contact) {
          recoverFromContact(action, result.contact, timestamp);
          return;
        }

        if (action.path?.length) {
          action.target = action.path.shift();
          action.lastVector = { dx: 0, dy: 0 };
          return;
        }

        if (action.waitForLanding && !action.traversal && completeLanding(state.x, state.y - 0.01, state.x, state.y)) {
          return;
        }

        if (action.keepPlayingOnArrival) {
          action.target = null;
          action.lastVector = { dx: 0, dy: 0 };
          return;
        }

        finishActiveAction(timestamp, 'arrived');
        return;
      }

      if (Math.abs(dx) > 0.01) {
        state.direction = dx < 0 ? 1 : -1;
      } else if (action.climbFacingDirection) {
        state.direction = action.climbFacingDirection;
      }

      const step = Math.min(distance, action.speed * deltaSeconds);
      const previousX = state.x;
      const previousY = state.y;
      const proposed = {
        x: state.x + (dx / distance) * step,
        y: state.y + (dy / distance) * step
      };
      const result = services.collisionWorld.resolveMovement(
        context, { x: previousX, y: previousY }, proposed, timestamp
      );
      state.x = result.x;
      state.y = result.y;

      if (
        state.y > previousY
        && (action.waitForLanding || action.traversal)
        && completeLanding(previousX, previousY, state.x, state.y)
      ) {
        return;
      }

      if (result.contact) {
        recoverFromContact(action, result.contact, timestamp);
      }
    }

    function animateAction(action, deltaMs, timestamp) {
      if (action.traversal && !services.surfacePlanner.refreshActiveTraversal(action)) {
        cancelActiveAction('surface-removed');
        clearQueuedActions();
        queueFall();
        return;
      }

      action.elapsedMs += deltaMs;
      action.frameElapsedMs += deltaMs;

      if (action.target && action.speed > 0 && action.motionEnabled !== false) {
        moveAction(action, deltaMs / 1000, timestamp);

        if (state.activeAction !== action) {
          return;
        }
      }

      while (action.frameElapsedMs >= getCurrentFrameDuration(action)) {
        const currentFrameDuration = getCurrentFrameDuration(action);

        action.frameElapsedMs -= currentFrameDuration;

        if (action.loop) {
          action.frameIndex = (action.frameIndex + 1) % action.frames.length;
          services.presentation.enterActionFrame(action, false);
          continue;
        }

        if (
          action.frameIndex === action.motionLoopEndIndex
          && (action.target || action.path?.length)
        ) {
          action.frameIndex = action.motionLoopStartIndex;
          services.presentation.enterActionFrame(action, false);
          continue;
        }

        if (action.frameIndex < action.frames.length - 1) {
          action.frameIndex += 1;
          services.presentation.enterActionFrame(action, false);
          continue;
        }

        if (action.waitForLanding || (action.waitForMotionCompletion && (action.target || action.path?.length))) {
          action.frameElapsedMs = 0;
          return;
        }

        finishActiveAction(timestamp, 'complete');
        return;
      }

      if (action.loop && action.durationMs !== null && action.elapsedMs >= action.durationMs) {
        finishActiveAction(timestamp, 'timeout');
      }
    }

    function didHitActiveBound(hitBounds, dx, dy) {
      return (hitBounds.left && dx < 0)
        || (hitBounds.right && dx > 0)
        || (hitBounds.top && dy < 0)
        || (hitBounds.bottom && dy > 0);
    }

    function pickEscapeTarget(hitBounds, mode) {
      const defaults = getDefaults();
      const currentSurface = services.surfacePlanner.getCurrentSurface();
      const minimumDistance = mode === 'run' ? defaults.minRunDistance : defaults.minWalkDistance;
      const horizontalInset = Math.min(
        (currentSurface.maxX - currentSurface.minX) / 2,
        Math.max(minimumDistance * 0.6, defaults.edgeRetargetMinInset)
      );

      return pickTarget({
        minX: hitBounds.left
          ? Math.min(currentSurface.maxX, currentSurface.minX + horizontalInset)
          : currentSurface.minX,
        maxX: hitBounds.right
          ? Math.max(currentSurface.minX, currentSurface.maxX - horizontalInset)
          : currentSurface.maxX,
        landY: currentSurface.landY
      }, minimumDistance);
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

    function clampPosition() {
      const bounds = services.surfacePlanner.getBounds();
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

    function syncSupport() {
      const action = state.activeAction;
      if (state.abducted || action?.traversal || action?.waitForLanding) {
        return;
      }

      const planner = services.surfacePlanner;
      const previousSurfaceId = state.currentSurfaceId;
      planner.refreshSurfaces();
      const surface = planner.getCurrentSurface();

      if (previousSurfaceId !== context.config.GROUND_SURFACE_ID
        && surface.id === context.config.GROUND_SURFACE_ID) {
        cancelActiveAction('surface-removed');
        clearQueuedActions();
        queueFall();
        return;
      }

      state.x = planner.clampXToSurface(surface, state.x);
      state.y = surface.landY;
      if (action?.target) {
        action.target.x = planner.clampXToSurface(surface, action.target.x);
        action.target.y = surface.landY;
      }
    }

    function tick(timestamp) {
      state.animationFrame = 0;

      if (!state.enabled || state.destroyed || state.modalOpen || state.reducedMotion || !services.presentation.hasSprite()) {
        state.lastTimestamp = 0;
        return;
      }

      if (!state.lastTimestamp) {
        state.lastTimestamp = timestamp;
      }

      const deltaMs = Math.min(50, timestamp - state.lastTimestamp);
      state.lastTimestamp = timestamp;
      syncSupport();

      queueAutonomousPlan(timestamp);
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

      queueAutonomousPlan(timestamp);
      startNextAction();
      services.presentation.applyPosition();
      services.collisionWorld.renderDebug();

      if (state.enabled && !state.destroyed && services.presentation.hasSprite()) {
        state.animationFrame = window.requestAnimationFrame(tick);
      }
    }

    function startLoop() {
      if (state.animationFrame || !state.enabled || state.destroyed || state.modalOpen || state.reducedMotion) {
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

    return Object.freeze({
      cancelActiveAction,
      clampPosition,
      clearQueuedActions,
      finishActiveAction,
      queueAction,
      queueSleep,
      queueTravel,
      queueTurn,
      triggerMenuAction,
      triggerSpecialAction,
      syncSupport,
      startLoop,
      startNextAction,
      stopLoop
    });
  }

  internals.createRuntimeEngine = createRuntimeEngine;
})(window.SheepInternals);
