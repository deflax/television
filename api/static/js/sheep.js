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
  const SURFACE_SELECTOR = '.sheep-surface';
  const GROUND_SURFACE_ID = 'ground';
  const SURFACE_ACTION_CONFIG = Object.freeze({
    jumpTo: Object.freeze({
      motionStartIndex: 1,
      minimumSpeed: 220,
      arcLift: 92
    }),
    jumpDown: Object.freeze({
      motionStartIndex: 1,
      minimumSpeed: 210,
      arcLift: 54
    }),
    climbDown: Object.freeze({
      motionStartIndex: 5,
      minimumSpeed: 118,
      edgeOffset: 14,
      verticalLead: 18
    }),
    climbDown2: Object.freeze({
      motionStartIndex: 7,
      minimumSpeed: 126,
      edgeOffset: 10,
      verticalLead: 14
    }),
    climbUp: Object.freeze({
      motionStartIndex: 2,
      minimumSpeed: 128,
      edgeOffset: 12,
      verticalLead: 16
    })
  });

  function freezeAction(definition) {
    return Object.freeze({
      frames: Object.freeze(definition.frames.slice()),
      frameMs: definition.frameMs,
      frameDurations: definition.frameDurations ? Object.freeze(definition.frameDurations.slice()) : null,
      frameEvents: definition.frameEvents ? Object.freeze({ ...definition.frameEvents }) : null,
      loop: definition.loop,
      onStart: typeof definition.onStart === 'function' ? definition.onStart : null,
      onComplete: typeof definition.onComplete === 'function' ? definition.onComplete : null,
      keepPlayingOnArrival: Boolean(definition.keepPlayingOnArrival)
    });
  }

  function createSequence() {
    return {
      frames: [],
      frameDurations: [],
      frameEvents: {}
    };
  }

  function addSequenceFrame(sequence, frame, durationMs, onEnter) {
    const index = sequence.frames.length;

    sequence.frames.push(frame);
    sequence.frameDurations.push(durationMs);

    if (typeof onEnter === 'function') {
      sequence.frameEvents[index] = onEnter;
    }
  }

  function addRepeatedFrames(sequence, frames, repeats, durationMs, onEnterFactory) {
    for (let repeatIndex = 0; repeatIndex < repeats; repeatIndex += 1) {
      for (let frameIndex = 0; frameIndex < frames.length; frameIndex += 1) {
        const frame = frames[frameIndex];
        const onEnter = typeof onEnterFactory === 'function'
          ? onEnterFactory(frame, repeatIndex, frameIndex)
          : null;

        addSequenceFrame(sequence, frame, durationMs, onEnter);
      }
    }
  }

  function finalizeSequenceAction(sequence, options) {
    const settings = options || {};

    return freezeAction({
      frames: sequence.frames,
      frameMs: settings.frameMs ?? null,
      frameDurations: sequence.frameDurations,
      frameEvents: sequence.frameEvents,
      loop: false,
      onStart: settings.onStart,
      onComplete: settings.onComplete,
      keepPlayingOnArrival: settings.keepPlayingOnArrival
    });
  }

  function createCallAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 3, 200);
    addSequenceFrame(sequence, 73, 2000);
    addRepeatedFrames(sequence, [71, 72], 4, 200);
    addSequenceFrame(sequence, 73, 2000);
    addRepeatedFrames(sequence, [71, 72], 4, 200);
    addSequenceFrame(sequence, 73, 200);
    addSequenceFrame(sequence, 74, 200);
    addSequenceFrame(sequence, 75, 200);
    addSequenceFrame(sequence, 76, 5000);
    addSequenceFrame(sequence, 73, 200);
    addSequenceFrame(sequence, 3, 200);

    return finalizeSequenceAction(sequence);
  }

  function createYawnAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 3, 200);
    addSequenceFrame(sequence, 31, 2000);
    addSequenceFrame(sequence, 107, 200);
    addSequenceFrame(sequence, 108, 2000);
    addRepeatedFrames(sequence, [110, 111], 4, 200);
    addSequenceFrame(sequence, 109, 7000);
    addSequenceFrame(sequence, 31, 200);
    addSequenceFrame(sequence, 3, 200);

    return finalizeSequenceAction(sequence);
  }

  function createStareAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 9, 400);
    addSequenceFrame(sequence, 10, 400);
    addSequenceFrame(sequence, 34, 5000);
    addSequenceFrame(sequence, 36, 400);
    addSequenceFrame(sequence, 34, 5000);
    addSequenceFrame(sequence, 10, 400);
    addSequenceFrame(sequence, 9, 400);

    return finalizeSequenceAction(sequence);
  }

  function createRollAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 3, 200);
    addSequenceFrame(sequence, 9, 200);
    addSequenceFrame(sequence, 10, 200);
    addSequenceFrame(sequence, 126, 3000);

    for (let frame = 125; frame >= 112; frame -= 1) {
      addSequenceFrame(sequence, frame, 200);
    }

    addSequenceFrame(sequence, 10, 200);
    addSequenceFrame(sequence, 9, 200);
    addSequenceFrame(sequence, 3, 200);

    return finalizeSequenceAction(sequence, {
      keepPlayingOnArrival: true
    });
  }

  function createBathAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 3, 400);
    addSequenceFrame(sequence, 9, 400);
    addSequenceFrame(sequence, 10, 400, () => {
      showProp(146, PROP_PRESETS.bath);
    });
    addRepeatedFrames(sequence, [54, 55], 5, 400, (frame) => () => {
      showProp(frame === 54 ? 147 : 148, PROP_PRESETS.bath);
    });
    addSequenceFrame(sequence, 10, 400, hideProp);
    addSequenceFrame(sequence, 54, 3000);
    addSequenceFrame(sequence, 10, 400);
    addSequenceFrame(sequence, 9, 400);
    addSequenceFrame(sequence, 3, 400, hideProp);

    return finalizeSequenceAction(sequence, {
      onComplete: hideProp
    });
  }

  function createEatAction() {
    const sequence = createSequence();
    const leafFrames = [149, 150, 151, 152];

    addSequenceFrame(sequence, 3, 1000, () => {
      showProp(153, PROP_PRESETS.eat);
    });

    for (let cycleIndex = 0; cycleIndex < leafFrames.length; cycleIndex += 1) {
      addSequenceFrame(sequence, 58, 300);
      addSequenceFrame(sequence, 59, 300);
      addSequenceFrame(sequence, 60, 300, () => {
        showProp(leafFrames[cycleIndex], PROP_PRESETS.eat);
      });
      addSequenceFrame(sequence, 61, 300);
      addSequenceFrame(sequence, 60, 300);
      addSequenceFrame(sequence, 61, 300);
      addSequenceFrame(sequence, 60, 300);
      addSequenceFrame(sequence, 61, 300);

      if (cycleIndex < leafFrames.length - 1) {
        addSequenceFrame(sequence, 3, 1000);
      }
    }

    addSequenceFrame(sequence, 3, 2000);
    addSequenceFrame(sequence, 50, 300);
    addSequenceFrame(sequence, 51, 300);
    addSequenceFrame(sequence, 50, 300);
    addSequenceFrame(sequence, 51, 300);
    addSequenceFrame(sequence, 50, 300);
    addSequenceFrame(sequence, 3, 300, hideProp);

    return finalizeSequenceAction(sequence, {
      onComplete: hideProp
    });
  }

  function createWaterAction() {
    const sequence = createSequence();

    addSequenceFrame(sequence, 3, 1000, () => {
      showProp(152, PROP_PRESETS.water);
    });
    addSequenceFrame(sequence, 12, 300);
    addSequenceFrame(sequence, 13, 300, (action) => {
      action.startDirection = action.startDirection ?? state.direction;
      state.direction = action.startDirection * -1;
    });
    addSequenceFrame(sequence, 103, 300);
    addSequenceFrame(sequence, 104, 300);

    [151, 150, 149, 153].forEach((propFrame, index) => {
      addSequenceFrame(sequence, index % 2 === 0 ? 105 : 106, 300, () => {
        showProp(propFrame, PROP_PRESETS.water);
      });
    });

    addSequenceFrame(sequence, 104, 300);
    addSequenceFrame(sequence, 103, 300);
    addSequenceFrame(sequence, 13, 300, (action) => {
      state.direction = action.startDirection ?? state.direction;
    });
    addSequenceFrame(sequence, 12, 300);
    addSequenceFrame(sequence, 3, 1000);
    addSequenceFrame(sequence, 8, 300);
    addSequenceFrame(sequence, 3, 1000, hideProp);

    return finalizeSequenceAction(sequence, {
      onComplete: (action) => {
        if (typeof action.startDirection === 'number') {
          state.direction = action.startDirection;
        }

        hideProp();
      }
    });
  }

  function createTraversalAction(name, frames, frameDurations) {
    const sequence = createSequence();
    const actionConfig = SURFACE_ACTION_CONFIG[name];

    frames.forEach((frame, index) => {
      addSequenceFrame(
        sequence,
        frame,
        frameDurations[index],
        index === actionConfig.motionStartIndex
          ? (action) => {
            action.motionEnabled = true;
          }
          : null
      );
    });

    return finalizeSequenceAction(sequence, {
      keepPlayingOnArrival: true,
      onStart: (action) => {
        action.motionEnabled = false;
        action.path = null;
        action.target = null;
        action.speed = 0;
        action.arrivalPoint = null;
        action.arrivalSurfaceId = null;
      }
    });
  }

  function createJumpToAction() {
    return createTraversalAction('jumpTo', [76, 30, 24], [140, 170, 220]);
  }

  function createJumpDownAction() {
    return createTraversalAction('jumpDown', [78, 77, 24, 84], [140, 150, 210, 160]);
  }

  function createClimbDownAction() {
    return createTraversalAction(
      'climbDown',
      [3, 9, 10, 11, 3, 31, 40, 41, 40, 41, 40, 45, 48],
      [80, 80, 80, 80, 90, 90, 90, 90, 90, 90, 90, 110, 120]
    );
  }

  function createClimbDown2Action() {
    return createTraversalAction(
      'climbDown2',
      [9, 10, 81, 10, 81, 10, 9, 3, 12, 13, 49, 42, 46, 47, 46, 47, 48],
      [80, 80, 80, 80, 80, 80, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 120]
    );
  }

  function createClimbUpAction() {
    return createTraversalAction(
      'climbUp',
      [12, 13, 49, 132, 131, 132, 131, 132, 131, 132, 131, 133, 47, 48],
      [90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 90, 100, 100, 120]
    );
  }

  const ACTIONS = Object.freeze({
    walk: freezeAction({
      frames: [2, 3],
      frameMs: 180,
      loop: true
    }),
    sleep: freezeAction({
      frames: [0, 1],
      frameMs: 420,
      loop: true
    }),
    run: freezeAction({
      frames: [4, 5],
      frameMs: 110,
      loop: true
    }),
    direction: freezeAction({
      frames: [3, 9, 10, 11, 3],
      frameMs: 90,
      loop: false
    }),
    directionBack: freezeAction({
      frames: [3, 12, 13, 14, 3],
      frameMs: 90,
      loop: false
    }),
    bump: freezeAction({
      frames: [62, 63, 64, 65, 66, 67, 68, 69, 70, 63],
      frameMs: 72,
      loop: false
    }),
    call: createCallAction(),
    yawn: createYawnAction(),
    stare: createStareAction(),
    roll: createRollAction(),
    bath: createBathAction(),
    eat: createEatAction(),
    water: createWaterAction(),
    jumpTo: createJumpToAction(),
    jumpDown: createJumpDownAction(),
    climbDown: createClimbDownAction(),
    climbDown2: createClimbDown2Action(),
    climbUp: createClimbUpAction()
  });

  const SPECIAL_ACTIONS = Object.freeze([
    Object.freeze({ name: 'call', weight: 1 }),
    Object.freeze({ name: 'yawn', weight: 1 }),
    Object.freeze({ name: 'stare', weight: 0.8 }),
    Object.freeze({ name: 'roll', weight: 0.55 }),
    Object.freeze({ name: 'bath', weight: 0.7 }),
    Object.freeze({ name: 'eat', weight: 0.9 }),
    Object.freeze({ name: 'water', weight: 0.7 })
  ]);

  const PROP_PRESETS = Object.freeze({
    bath: Object.freeze({
      offsetX: 0,
      offsetY: -4,
      attachToFacing: false,
      flipWithDirection: false
    }),
    eat: Object.freeze({
      offsetX: 18,
      offsetY: 8,
      attachToFacing: true,
      flipWithDirection: true
    }),
    water: Object.freeze({
      offsetX: 20,
      offsetY: 10,
      attachToFacing: true,
      flipWithDirection: true
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
    specialActionChance: 0.28,
    runChance: 0.2,
    sleepChance: 0.42,
    surfaceActionChance: 0.26,
    rollTravelDistance: 220,
    rollTravelMs: 3000,
    minRollDistance: 96,
    minSurfaceWidth: 36,
    minSurfaceGap: 24,
    maxJumpHorizontalDistance: 280,
    maxJumpUpDistance: 260,
    maxJumpDownDistance: 280,
    maxClimbDistance: 300,
    maxEdgeLandingDelta: 28,
    surfaceEdgeApproachThreshold: 12,
    surfaceHorizontalPadding: 8
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
    lastTurnAction: 'directionBack',
    currentSurfaceId: GROUND_SURFACE_ID,
    prop: {
      visible: false,
      currentFrame: null,
      offsetX: 0,
      offsetY: 0,
      attachToFacing: false,
      flipWithDirection: false
    }
  };

  let layer = null;
  let sprite = null;
  let propSprite = null;
  let markedSurfaces = [];

  function randomBetween(min, max) {
    return min + (Math.random() * (max - min));
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function getSpriteMetrics() {
    return {
      width: sprite ? (sprite.offsetWidth || 72) : 72,
      height: sprite ? (sprite.offsetHeight || 50) : 50
    };
  }

  function readCssPixels(name, fallback) {
    const rootStyles = getComputedStyle(document.documentElement);
    const value = parseFloat(rootStyles.getPropertyValue(name));
    return Number.isFinite(value) ? value : fallback;
  }

  function getBounds() {
    const { width: spriteWidth, height: spriteHeight } = getSpriteMetrics();
    const edgePadding = readCssPixels('--sheep-edge-padding', 16);
    const safeTop = readCssPixels('--sheep-safe-top', 68);
    const safeBottom = readCssPixels('--sheep-safe-bottom', 16);

    const minX = edgePadding;
    const minY = Math.max(edgePadding, safeTop);
    const maxX = Math.max(minX, window.innerWidth - spriteWidth - edgePadding);
    const maxY = Math.max(minY, window.innerHeight - spriteHeight - safeBottom);

    return { minX, minY, maxX, maxY };
  }

  function getGroundSurface(bounds) {
    const viewportBounds = bounds || getBounds();
    const { height: spriteHeight } = getSpriteMetrics();

    return {
      id: GROUND_SURFACE_ID,
      type: 'ground',
      element: null,
      minX: viewportBounds.minX,
      maxX: viewportBounds.maxX,
      edgeLeftX: viewportBounds.minX,
      edgeRightX: viewportBounds.maxX,
      landY: viewportBounds.maxY,
      lineY: viewportBounds.maxY + spriteHeight,
      centerX: (viewportBounds.minX + viewportBounds.maxX) / 2
    };
  }

  function getMarkedSurfaceById(surfaceId) {
    return markedSurfaces.find((surface) => surface.id === surfaceId) || null;
  }

  function resolveSurfaceById(surfaceId) {
    if (!surfaceId || surfaceId === GROUND_SURFACE_ID) {
      return getGroundSurface();
    }

    return getMarkedSurfaceById(surfaceId) || getGroundSurface();
  }

  function getCurrentSurface() {
    return resolveSurfaceById(state.currentSurfaceId);
  }

  function setCurrentSurface(surface) {
    state.currentSurfaceId = surface?.id || GROUND_SURFACE_ID;
  }

  function buildSurfaceFromElement(element, index, bounds, spriteMetrics) {
    const rect = element.getBoundingClientRect();

    if (
      rect.width <= 0
      || rect.height <= 0
      || rect.bottom <= 0
      || rect.top >= window.innerHeight
      || rect.right <= 0
      || rect.left >= window.innerWidth
    ) {
      return null;
    }

    const minX = clamp(rect.left, bounds.minX, bounds.maxX);
    const maxX = clamp(rect.right - spriteMetrics.width, bounds.minX, bounds.maxX);

    if ((maxX - minX) < DEFAULTS.minSurfaceWidth) {
      return null;
    }

    const landY = clamp(rect.top - spriteMetrics.height, bounds.minY, bounds.maxY);
    const surfaceId = element.id
      ? `surface:${element.id}`
      : `surface:${index}`;

    return {
      id: surfaceId,
      type: 'marked',
      element: element,
      minX: minX,
      maxX: maxX,
      edgeLeftX: minX,
      edgeRightX: maxX,
      landY: landY,
      lineY: landY + spriteMetrics.height,
      centerX: (minX + maxX) / 2
    };
  }

  function refreshSurfaces() {
    const bounds = getBounds();
    const spriteMetrics = getSpriteMetrics();

    markedSurfaces = Array.from(document.querySelectorAll(SURFACE_SELECTOR))
      .map((element, index) => buildSurfaceFromElement(element, index, bounds, spriteMetrics))
      .filter(Boolean)
      .sort((left, right) => left.lineY - right.lineY || left.minX - right.minX);

    if (state.currentSurfaceId !== GROUND_SURFACE_ID && !getMarkedSurfaceById(state.currentSurfaceId)) {
      state.currentSurfaceId = GROUND_SURFACE_ID;
    }
  }

  function clampXToSurface(surface, x) {
    return clamp(x, surface.minX, surface.maxX);
  }

  function snapToCurrentSurface() {
    const surface = getCurrentSurface();

    state.x = clampXToSurface(surface, state.x);
    state.y = surface.landY;
  }

  function sumDurations(values) {
    return values.reduce((total, value) => total + value, 0);
  }

  function getTraversalMotionBudgetMs(name) {
    const definition = ACTIONS[name];
    const actionConfig = SURFACE_ACTION_CONFIG[name];

    if (!definition || !actionConfig) {
      return 0;
    }

    const frameDurations = definition.frameDurations || definition.frames.map(() => definition.frameMs);
    return sumDurations(frameDurations.slice(actionConfig.motionStartIndex));
  }

  function getPathLength(origin, waypoints) {
    let cursor = origin;
    let length = 0;

    waypoints.forEach((point) => {
      length += Math.hypot(point.x - cursor.x, point.y - cursor.y);
      cursor = point;
    });

    return length;
  }

  function configureTraversalMotion(action, name, targetSurface, waypoints, landingPoint) {
    const path = waypoints.map((point) => ({ x: point.x, y: point.y }));
    const pathLength = getPathLength({ x: state.x, y: state.y }, path);
    const motionBudgetSeconds = Math.max(0.22, getTraversalMotionBudgetMs(name) / 1000);

    action.target = path.shift() || null;
    action.path = path;
    action.speed = Math.max(
      SURFACE_ACTION_CONFIG[name].minimumSpeed,
      pathLength / motionBudgetSeconds
    );
    action.arrivalPoint = landingPoint;
    action.arrivalSurfaceId = targetSurface.id;
  }

  function buildJumpPath(targetPoint, arcLift) {
    const bounds = getBounds();
    const apexY = clamp(
      Math.min(state.y, targetPoint.y) - arcLift,
      bounds.minY,
      bounds.maxY
    );

    return [
      {
        x: (state.x + targetPoint.x) / 2,
        y: apexY
      },
      targetPoint
    ];
  }

  function buildClimbPath(name, edgeX, edgeDirection, landingPoint) {
    const bounds = getBounds();
    const actionConfig = SURFACE_ACTION_CONFIG[name];
    const sideOffset = edgeDirection === 1
      ? actionConfig.edgeOffset * -1
      : actionConfig.edgeOffset;
    const sideX = clamp(edgeX + sideOffset, bounds.minX, bounds.maxX);
    const exitY = clamp(state.y + actionConfig.verticalLead, bounds.minY, bounds.maxY);
    const settleY = clamp(
      landingPoint.y + (landingPoint.y < state.y ? actionConfig.verticalLead : actionConfig.verticalLead * -1),
      bounds.minY,
      bounds.maxY
    );

    if (name === 'climbUp') {
      return [
        { x: sideX, y: clamp(state.y + actionConfig.verticalLead, bounds.minY, bounds.maxY) },
        { x: sideX, y: settleY },
        landingPoint
      ];
    }

    return [
      { x: sideX, y: exitY },
      { x: sideX, y: settleY },
      landingPoint
    ];
  }

  function getEdgeDirection(edge) {
    return edge === 'left' ? 1 : -1;
  }

  function buildSurfaceTarget(surface, preferredX) {
    return {
      x: clampXToSurface(surface, preferredX),
      y: surface.landY
    };
  }

  function pickBestSurfaceCandidate(candidates) {
    const [bestCandidate] = candidates.sort((left, right) => left.score - right.score);
    return bestCandidate || null;
  }

  function pickJumpUpCandidate(sourceSurface) {
    const candidates = markedSurfaces
      .filter((surface) => surface.id !== sourceSurface.id)
      .map((surface) => {
        const verticalDistance = sourceSurface.landY - surface.landY;
        const targetX = clampXToSurface(surface, state.x);
        const horizontalDistance = Math.abs(targetX - state.x);

        if (
          verticalDistance < DEFAULTS.minSurfaceGap
          || verticalDistance > DEFAULTS.maxJumpUpDistance
          || horizontalDistance > DEFAULTS.maxJumpHorizontalDistance
        ) {
          return null;
        }

        return {
          name: 'jumpTo',
          targetSurfaceId: surface.id,
          score: horizontalDistance + (verticalDistance * 1.1)
        };
      })
      .filter(Boolean);

    return pickBestSurfaceCandidate(candidates);
  }

  function pickJumpDownCandidate(sourceSurface) {
    if (sourceSurface.id === GROUND_SURFACE_ID) {
      return null;
    }

    const candidates = [...markedSurfaces, getGroundSurface()]
      .filter((surface) => surface.id !== sourceSurface.id)
      .map((surface) => {
        const verticalDistance = surface.landY - sourceSurface.landY;
        const targetX = clampXToSurface(surface, state.x);
        const horizontalDistance = Math.abs(targetX - state.x);

        if (
          verticalDistance < DEFAULTS.minSurfaceGap
          || verticalDistance > DEFAULTS.maxJumpDownDistance
          || horizontalDistance > DEFAULTS.maxJumpHorizontalDistance
        ) {
          return null;
        }

        return {
          name: 'jumpDown',
          targetSurfaceId: surface.id,
          score: horizontalDistance + verticalDistance
        };
      })
      .filter(Boolean);

    return pickBestSurfaceCandidate(candidates);
  }

  function pickEdgeTraversalCandidate(sourceSurface, actionName, direction) {
    if (sourceSurface.id === GROUND_SURFACE_ID) {
      return null;
    }

    const edge = direction === 1 ? 'left' : 'right';
    const edgeX = edge === 'left' ? sourceSurface.edgeLeftX : sourceSurface.edgeRightX;
    const wantsUpperSurface = actionName === 'climbUp';
    const candidates = [...markedSurfaces, getGroundSurface()]
      .filter((surface) => surface.id !== sourceSurface.id)
      .map((surface) => {
        const verticalDistance = wantsUpperSurface
          ? sourceSurface.landY - surface.landY
          : surface.landY - sourceSurface.landY;
        const landingX = clampXToSurface(surface, edgeX);
        const horizontalDelta = Math.abs(landingX - edgeX);

        if (
          verticalDistance < DEFAULTS.minSurfaceGap
          || verticalDistance > DEFAULTS.maxClimbDistance
          || horizontalDelta > DEFAULTS.maxEdgeLandingDelta
          || (wantsUpperSurface && surface.id === GROUND_SURFACE_ID)
        ) {
          return null;
        }

        return {
          name: actionName,
          edge: edge,
          targetSurfaceId: surface.id,
          score: verticalDistance + (horizontalDelta * 2)
        };
      })
      .filter(Boolean);

    return pickBestSurfaceCandidate(candidates);
  }

  function pickSurfaceTraversalPlan() {
    const currentSurface = getCurrentSurface();
    const candidates = [];

    const jumpUp = pickJumpUpCandidate(currentSurface);
    if (jumpUp) {
      candidates.push({ ...jumpUp, weight: currentSurface.id === GROUND_SURFACE_ID ? 1.6 : 1 });
    }

    if (currentSurface.id !== GROUND_SURFACE_ID) {
      const climbUpLeft = pickEdgeTraversalCandidate(currentSurface, 'climbUp', 1);
      const climbUpRight = pickEdgeTraversalCandidate(currentSurface, 'climbUp', -1);
      const climbUp = pickBestSurfaceCandidate([climbUpLeft, climbUpRight].filter(Boolean));

      if (climbUp) {
        candidates.push({ ...climbUp, weight: 0.75 });
      }

      const jumpDown = pickJumpDownCandidate(currentSurface);
      if (jumpDown) {
        candidates.push({ ...jumpDown, weight: 1.1 });
      }

      const climbDownLeft = pickEdgeTraversalCandidate(currentSurface, 'climbDown', 1);
      const climbDownRight = pickEdgeTraversalCandidate(currentSurface, 'climbDown', -1);
      const climbDown = pickBestSurfaceCandidate([climbDownLeft, climbDownRight].filter(Boolean));

      if (climbDown) {
        candidates.push({ ...climbDown, weight: 1.35 });
      }

      const climbDown2Left = pickEdgeTraversalCandidate(currentSurface, 'climbDown2', 1);
      const climbDown2Right = pickEdgeTraversalCandidate(currentSurface, 'climbDown2', -1);
      const climbDown2 = pickBestSurfaceCandidate([climbDown2Left, climbDown2Right].filter(Boolean));

      if (climbDown2) {
        candidates.push({ ...climbDown2, weight: 1.05 });
      }
    }

    if (!candidates.length) {
      return null;
    }

    const totalWeight = candidates.reduce((sum, candidate) => sum + candidate.weight, 0);
    let cursor = Math.random() * totalWeight;

    for (let index = 0; index < candidates.length; index += 1) {
      cursor -= candidates[index].weight;

      if (cursor <= 0) {
        return candidates[index];
      }
    }

    return candidates[candidates.length - 1];
  }

  function queueSurfaceTraversalPlan(plan) {
    if (!plan) {
      return false;
    }

    if (plan.name === 'jumpTo' || plan.name === 'jumpDown') {
      const targetSurface = resolveSurfaceById(plan.targetSurfaceId);
      const targetPoint = buildSurfaceTarget(targetSurface, state.x);
      const targetDirection = Math.abs(targetPoint.x - state.x) <= 1
        ? state.direction
        : (targetPoint.x < state.x ? 1 : -1);

      queueTurn(targetDirection);
      queueAction(plan.name, {
        onStart: (action) => {
          refreshSurfaces();

          const liveTargetSurface = resolveSurfaceById(plan.targetSurfaceId);
          const landingPoint = buildSurfaceTarget(liveTargetSurface, state.x);
          const path = buildJumpPath(landingPoint, SURFACE_ACTION_CONFIG[plan.name].arcLift);

          configureTraversalMotion(action, plan.name, liveTargetSurface, path, landingPoint);
        }
      });

      return true;
    }

    const sourceSurface = getCurrentSurface();
    const edgeX = plan.edge === 'left' ? sourceSurface.edgeLeftX : sourceSurface.edgeRightX;
    const edgePoint = buildSurfaceTarget(sourceSurface, edgeX);

    if (Math.abs(state.x - edgePoint.x) > DEFAULTS.surfaceEdgeApproachThreshold) {
      queueTravel('walk', edgePoint);
    } else if (state.direction !== getEdgeDirection(plan.edge)) {
      queueTurn(getEdgeDirection(plan.edge));
    }

    queueAction(plan.name, {
      onStart: (action) => {
        refreshSurfaces();

        const liveSourceSurface = getCurrentSurface();
        const liveTargetSurface = resolveSurfaceById(plan.targetSurfaceId);
        const liveEdgeX = plan.edge === 'left'
          ? liveSourceSurface.edgeLeftX
          : liveSourceSurface.edgeRightX;
        const landingPoint = buildSurfaceTarget(liveTargetSurface, liveEdgeX);
        const path = buildClimbPath(plan.name, liveEdgeX, getEdgeDirection(plan.edge), landingPoint);

        configureTraversalMotion(action, plan.name, liveTargetSurface, path, landingPoint);
      }
    });

    return true;
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

    if (!propSprite) {
      return;
    }

    if (!state.prop.visible) {
      propSprite.hidden = true;
      return;
    }

    const propOffsetX = state.prop.attachToFacing
      ? state.prop.offsetX * state.direction * -1
      : state.prop.offsetX;
    const propScaleX = state.prop.flipWithDirection ? state.direction : 1;

    propSprite.hidden = false;
    propSprite.style.transform = `translate3d(${state.x + propOffsetX}px, ${state.y + state.prop.offsetY}px, 0) scaleX(${propScaleX})`;
  }

  function applyAtlasFrame(element, frame, force, previousFrame) {
    if (!element) {
      return previousFrame;
    }

    if (!force && previousFrame === frame) {
      return previousFrame;
    }

    const frameSize = element.offsetWidth || 72;
    const column = frame % SPRITE_COLUMNS;
    const row = Math.floor(frame / SPRITE_COLUMNS);

    element.style.backgroundPosition = `${-column * frameSize}px ${-row * frameSize}px`;
    return frame;
  }

  function setFrame(frame, force) {
    state.currentFrame = applyAtlasFrame(sprite, frame, force, state.currentFrame);
  }

  function setPropFrame(frame, force) {
    state.prop.currentFrame = applyAtlasFrame(propSprite, frame, force, state.prop.currentFrame);
  }

  function showProp(frame, preset) {
    const nextPreset = preset || PROP_PRESETS.bath;

    state.prop.visible = true;
    state.prop.offsetX = nextPreset.offsetX;
    state.prop.offsetY = nextPreset.offsetY;
    state.prop.attachToFacing = nextPreset.attachToFacing;
    state.prop.flipWithDirection = nextPreset.flipWithDirection;
    setPropFrame(frame, false);
  }

  function hideProp() {
    state.prop.visible = false;
    state.prop.currentFrame = null;

    if (propSprite) {
      propSprite.hidden = true;
    }
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

  function enterActionFrame(action, force) {
    if (!action) {
      return;
    }

    setFrame(action.frames[action.frameIndex], force);

    const onEnter = action.frameEvents?.[action.frameIndex];

    if (typeof onEnter === 'function') {
      onEnter(action, action.frames[action.frameIndex]);
    }
  }

  function getCurrentFrameDuration(action) {
    return action.frameDurations?.[action.frameIndex] ?? action.frameMs;
  }

  function buildAction(name, overrides) {
    const definition = ACTIONS[name];
    const options = overrides || {};

    return {
      name: name,
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
      onStart: composeCallbacks(definition.onStart, options.onStart),
      onComplete: composeCallbacks(definition.onComplete, options.onComplete)
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
      onStart: action.onStart,
      onComplete: action.onComplete,
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

    enterActionFrame(state.activeAction, true);
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

    applyPosition();
  }

  function cancelActiveAction() {
    state.activeAction = null;
  }

  function pickTarget(bounds, minDistance) {
    const targetSurface = bounds || getCurrentSurface();
    const availableDistance = Math.max(24, targetSurface.maxX - targetSurface.minX);
    const requiredDistance = Math.min(minDistance ?? DEFAULTS.minWalkDistance, availableDistance);
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
    queueAction('sleep', {
      durationMs: durationMs ?? randomBetween(DEFAULTS.sleepMinMs, DEFAULTS.sleepMaxMs)
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
    const surface = getCurrentSurface();
    const leftSpace = Math.max(0, state.x - surface.minX);
    const rightSpace = Math.max(0, surface.maxX - state.x);
    const preferredDirection = state.direction === 1
      ? (leftSpace >= DEFAULTS.minRollDistance ? 1 : -1)
      : (rightSpace >= DEFAULTS.minRollDistance ? -1 : 1);
    const targetDirection = preferredDirection === 1 && leftSpace >= rightSpace
      ? 1
      : preferredDirection === -1 && rightSpace >= leftSpace
        ? -1
        : (leftSpace >= rightSpace ? 1 : -1);
    const availableDistance = targetDirection === 1 ? leftSpace : rightSpace;
    const travelDistance = Math.min(DEFAULTS.rollTravelDistance, availableDistance);

    if (travelDistance < DEFAULTS.minRollDistance) {
      queueTravel('walk');
      return;
    }

    queueTurn(targetDirection);
    queueAction('roll', {
      speed: travelDistance / (DEFAULTS.rollTravelMs / 1000),
      target: {
        x: state.x + (targetDirection === 1 ? -travelDistance : travelDistance),
        y: surface.landY
      }
    });
  }

  function queueSpecialActionPlan() {
    const actionName = pickWeightedAction(SPECIAL_ACTIONS);

    if (actionName === 'roll') {
      queueRollAction();
      return;
    }

    queueAction(actionName);
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
      getCurrentSurface(),
      mode === 'run' ? DEFAULTS.minRunDistance : DEFAULTS.minWalkDistance
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

  function queueAutonomousPlan() {
    if (state.activeAction || state.actionQueue.length) {
      return;
    }

    refreshSurfaces();

    if (Math.random() < DEFAULTS.sleepChance) {
      queueSleep();
      return;
    }

    if (Math.random() < DEFAULTS.surfaceActionChance && queueSurfaceTraversalPlan(pickSurfaceTraversalPlan())) {
      return;
    }

    if (Math.random() < DEFAULTS.specialActionChance) {
      queueSpecialActionPlan();
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

       if (action.path?.length) {
        action.target = action.path.shift();
        action.lastVector = { dx: 0, dy: 0 };
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

    state.direction = dx < 0 ? 1 : -1;

    const step = Math.min(distance, action.speed * deltaSeconds);
    state.x += (dx / distance) * step;
    state.y += (dy / distance) * step;
  }

  function animateAction(action, deltaMs, timestamp) {
    action.elapsedMs += deltaMs;
    action.frameElapsedMs += deltaMs;

    if (action.target && action.speed > 0 && action.motionEnabled !== false) {
      moveAction(action, deltaMs / 1000, timestamp);

      if (!state.activeAction) {
        return;
      }
    }

    while (action.frameElapsedMs >= getCurrentFrameDuration(action)) {
      const currentFrameDuration = getCurrentFrameDuration(action);

      action.frameElapsedMs -= currentFrameDuration;

      if (action.loop) {
        action.frameIndex = (action.frameIndex + 1) % action.frames.length;
        enterActionFrame(action, false);
        continue;
      }

      if (action.frameIndex < action.frames.length - 1) {
        action.frameIndex += 1;
        enterActionFrame(action, false);
        continue;
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
    const currentSurface = getCurrentSurface();
    const minimumDistance = mode === 'run' ? DEFAULTS.minRunDistance : DEFAULTS.minWalkDistance;
    const horizontalInset = Math.min(
      (currentSurface.maxX - currentSurface.minX) / 2,
      Math.max(minimumDistance * 0.6, DEFAULTS.edgeRetargetMinInset)
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
    refreshSurfaces();

    if (!state.activeAction) {
      snapToCurrentSurface();
    }

    clampPosition();
    setFrame(state.currentFrame ?? NEUTRAL_FRAME, true);
    applyPosition();
  }

  function onScroll() {
    refreshSurfaces();

    if (!state.activeAction) {
      snapToCurrentSurface();
      applyPosition();
    }
  }

  function createLayer() {
    layer = document.createElement('div');
    layer.className = 'sheep-layer';
    layer.setAttribute('aria-hidden', 'true');

    propSprite = document.createElement('div');
    propSprite.className = 'sheep-layer__prop';
    propSprite.setAttribute('aria-hidden', 'true');
    propSprite.style.backgroundImage = `url('${SPRITE_SHEET_URL}')`;
    propSprite.style.backgroundSize = `calc(var(--sheep-size) * ${SPRITE_COLUMNS}) calc(var(--sheep-size) * ${SPRITE_ROWS})`;
    propSprite.hidden = true;

    sprite = document.createElement('div');
    sprite.className = 'sheep-layer__sprite';
    sprite.setAttribute('aria-hidden', 'true');
    sprite.style.backgroundImage = `url('${SPRITE_SHEET_URL}')`;
    sprite.style.backgroundSize = `calc(var(--sheep-size) * ${SPRITE_COLUMNS}) calc(var(--sheep-size) * ${SPRITE_ROWS})`;

    layer.appendChild(propSprite);
    layer.appendChild(sprite);
    document.body.appendChild(layer);
  }

  function seedInitialPosition() {
    const bounds = getBounds();
    const groundSurface = getGroundSurface(bounds);

    state.x = groundSurface.maxX;
    state.y = groundSurface.landY;
    state.direction = 1;
    setCurrentSurface(groundSurface);
    queueSleep(randomBetween(900, 2000));
    startNextAction();
    clampPosition();
    applyPosition();
  }

  function bindEvents() {
    document.addEventListener('show.bs.modal', onModalShown);
    document.addEventListener('hidden.bs.modal', onModalHidden);
    window.addEventListener('resize', onResize, { passive: true });
    window.addEventListener('scroll', onScroll, { passive: true });

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
    refreshSurfaces();
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
