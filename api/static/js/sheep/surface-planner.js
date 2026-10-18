window.SheepInternals = window.SheepInternals || {};

((internals) => {
  function createSurfacePlanner(context) {
    const { window, document, state, config, helpers, services } = context;
    const { clamp } = helpers;
    let markedSurfaces = [];
    const surfaceIds = new WeakMap();
    let nextSurfaceId = 0;

    function getDefaults() {
      return services.actionCatalog.DEFAULTS;
    }

    function readCssPixels(name, fallback) {
      const rootStyles = getComputedStyle(document.documentElement);
      const value = parseFloat(rootStyles.getPropertyValue(name));
      return Number.isFinite(value) ? value : fallback;
    }

    function getBounds() {
      const { width: spriteWidth, height: spriteHeight } = services.presentation.getSpriteMetrics();
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
      const { height: spriteHeight } = services.presentation.getSpriteMetrics();

      return {
        id: config.GROUND_SURFACE_ID,
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
      if (!surfaceId || surfaceId === config.GROUND_SURFACE_ID) {
        return getGroundSurface();
      }

      return getMarkedSurfaceById(surfaceId) || getGroundSurface();
    }

    function getCurrentSurface() {
      return resolveSurfaceById(state.currentSurfaceId);
    }

    function setCurrentSurface(surface) {
      state.currentSurfaceId = surface?.id || config.GROUND_SURFACE_ID;
    }

    function buildSurfaceFromElement(element, bounds, spriteMetrics) {
      const rect = element.getBoundingClientRect();
      const styles = window.getComputedStyle(element);
      const landY = rect.top - spriteMetrics.height;

      // A platform is its real top, never a viewport-clamped substitute.
      if (
        element.hidden
        || styles.display === 'none'
        || styles.visibility === 'hidden'
        || styles.visibility === 'collapse'
        || Number(styles.opacity) === 0
        || rect.width <= 0
        || rect.height <= 0
        || rect.right <= 0
        || rect.left >= window.innerWidth
        || landY < bounds.minY
        || landY > bounds.maxY
      ) {
        return null;
      }

      // The compact body must fit on the top; transparent atlas margins may overhang.
      const minX = Math.max(rect.left - spriteMetrics.width * 0.2, bounds.minX);
      const maxX = Math.min(rect.right - spriteMetrics.width * 0.8, bounds.maxX);

      if ((maxX - minX) < getDefaults().minSurfaceWidth) {
        return null;
      }

      if (!surfaceIds.has(element)) {
        surfaceIds.set(element, `surface:anonymous:${nextSurfaceId++}`);
      }

      return {
        id: element.id ? `surface:${element.id}` : surfaceIds.get(element),
        type: 'marked',
        element,
        rect: {
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height
        },
        minX,
        maxX,
        edgeLeftX: minX,
        edgeRightX: maxX,
        landY,
        lineY: rect.top,
        centerX: (minX + maxX) / 2
      };
    }

    function refreshSurfaces() {
      const bounds = getBounds();
      const spriteMetrics = services.presentation.getSpriteMetrics();

      markedSurfaces = Array.from(document.querySelectorAll(config.SURFACE_SELECTOR))
        .map((element) => buildSurfaceFromElement(element, bounds, spriteMetrics))
        .filter(Boolean)
        .sort((left, right) => left.lineY - right.lineY || left.minX - right.minX);
    }

    function getSurfaces() {
      return [...markedSurfaces, getGroundSurface()];
    }

    function clampXToSurface(surface, x) {
      return clamp(x, surface.minX, surface.maxX);
    }

    function snapToCurrentSurface() {
      const surface = getCurrentSurface();

      state.x = clampXToSurface(surface, state.x);
      state.y = surface.landY;
    }

    function findLandingSurfaceBetween(fromX, fromY, toX, toY, excludedSurfaceId) {
      const verticalTravel = toY - fromY;

      // One-way tops require a downward crossing, not horizontal proximity.
      if (verticalTravel <= 0) {
        return null;
      }

      const { width: spriteWidth } = services.presentation.getSpriteMetrics();
      let firstContact = null;

      for (const surface of getSurfaces()) {
        if (surface.id === excludedSurfaceId || fromY > surface.landY || toY < surface.landY) {
          continue;
        }

        const timeOfImpact = (surface.landY - fromY) / verticalTravel;
        const crossingX = fromX + (toX - fromX) * timeOfImpact;
        const landingX = clampXToSurface(surface, crossingX);
        const footLeft = crossingX + spriteWidth * 0.2;
        const footRight = crossingX + spriteWidth * 0.8;

        if (
          Math.abs(landingX - crossingX) > spriteWidth * 0.2
          || (surface.rect && (footRight <= surface.rect.left || footLeft >= surface.rect.right))
        ) {
          continue;
        }

        if (!firstContact || timeOfImpact < firstContact.timeOfImpact) {
          firstContact = { surface, landingX, timeOfImpact };
        }
      }

      return firstContact;
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
      const path = waypoints.map((point) => ({ ...point }));
      const pathLength = getPathLength({ x: state.x, y: state.y }, path);
      const motionBudgetSeconds = Math.max(0.22, services.actionCatalog.getTraversalMotionBudgetMs(name) / 1000);
      const actionConfig = services.actionCatalog.getSurfaceActionConfig(name);

      action.target = path.shift() || null;
      action.path = path;
      action.speed = Math.min(
        actionConfig.maximumSpeed || Infinity,
        Math.max(actionConfig.minimumSpeed, pathLength / motionBudgetSeconds)
      );
      action.arrivalPoint = landingPoint;
      action.arrivalSurfaceId = targetSurface.id;
      action.waitForMotionCompletion = true;
      action.motionLoopStartIndex = actionConfig.motionLoopStartIndex;
      action.motionLoopEndIndex = actionConfig.motionLoopEndIndex;
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
          y: apexY,
          phase: 'apex'
        },
        { ...targetPoint, phase: 'land' }
      ];
    }

    function buildClimbPath(name, edgeX, edgeDirection, landingPoint) {
      const bounds = getBounds();
      const actionConfig = services.actionCatalog.getSurfaceActionConfig(name);
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

      return [
        { x: sideX, y: exitY, phase: 'exit' },
        { x: sideX, y: settleY, phase: 'settle' },
        { ...landingPoint, phase: 'land' }
      ];
    }

    function getClimbSide(surface, edge) {
      if (!surface.rect) {
        return null;
      }

      const bounds = getBounds();
      const { width } = services.presentation.getSpriteMetrics();
      const side = edge === 'left' ? surface.rect.left : surface.rect.right;
      const desiredX = side - width / 2;
      const x = clamp(desiredX, bounds.minX, bounds.maxX);

      // A visible wall at the viewport rim is climbed from just inside its edge,
      // keeping the complete sprite onscreen. Offscreen edges remain inaccessible.
      return side >= 0 && side <= window.innerWidth ? { x } : null;
    }

    function canClimbFrom(source, target, edge) {
      const side = getClimbSide(target, edge);
      if (!side) {
        return false;
      }

      const defaults = getDefaults();
      const { height } = services.presentation.getSpriteMetrics();
      const bottomGap = Math.max(0, source.landY + height - target.rect.bottom);
      return source.landY - target.landY >= defaults.minSurfaceGap
        && bottomGap <= defaults.maxClimbApproachGap
        && Math.abs(clampXToSurface(source, side.x) - side.x) <= defaults.maxEdgeLandingDelta;
    }

    function buildSideClimbPath(surface, edge) {
      const side = getClimbSide(surface, edge);
      const { height } = services.presentation.getSpriteMetrics();
      const landingPoint = buildSurfaceTarget(surface, side.x);
      const entryY = Math.min(state.y, surface.rect.bottom - height);

      return [
        { x: side.x, y: state.y, phase: 'approach' },
        { x: side.x, y: Math.max(surface.landY, entryY), phase: 'entry' },
        { x: side.x, y: surface.landY, phase: 'climb' },
        { ...landingPoint, phase: 'land' }
      ];
    }

    function getClimbUpCandidates(source) {
      const candidates = [];

      for (const surface of markedSurfaces) {
        for (const edge of ['left', 'right']) {
          if (surface.id !== source.id && canClimbFrom(source, surface, edge)) {
            candidates.push({ name: 'climbUp', edge, targetSurfaceId: surface.id, weight: 0.95 });
          }
        }
      }

      return candidates;
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
      const defaults = getDefaults();
      const candidates = markedSurfaces
        .filter((surface) => surface.id !== sourceSurface.id)
        .map((surface) => {
          const verticalDistance = sourceSurface.landY - surface.landY;
          const targetX = clampXToSurface(surface, state.x);
          const horizontalDistance = Math.abs(targetX - state.x);

          if (
            verticalDistance < defaults.minSurfaceGap
            || verticalDistance > defaults.maxJumpUpDistance
            || horizontalDistance > defaults.maxJumpHorizontalDistance
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
      const defaults = getDefaults();

      if (sourceSurface.id === config.GROUND_SURFACE_ID) {
        return null;
      }

      const candidates = [...markedSurfaces, getGroundSurface()]
        .filter((surface) => surface.id !== sourceSurface.id)
        .map((surface) => {
          const verticalDistance = surface.landY - sourceSurface.landY;
          const targetX = clampXToSurface(surface, state.x);
          const horizontalDistance = Math.abs(targetX - state.x);

          if (
            verticalDistance < defaults.minSurfaceGap
            || verticalDistance > defaults.maxJumpDownDistance
            || horizontalDistance > defaults.maxJumpHorizontalDistance
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
      const defaults = getDefaults();

      if (sourceSurface.id === config.GROUND_SURFACE_ID) {
        return null;
      }

      const edge = direction === 1 ? 'left' : 'right';
      const edgeX = edge === 'left' ? sourceSurface.edgeLeftX : sourceSurface.edgeRightX;
      const candidates = getSurfaces()
        .filter((surface) => surface.id !== sourceSurface.id)
        .map((surface) => {
          const verticalDistance = surface.landY - sourceSurface.landY;
          const landingX = clampXToSurface(surface, edgeX);
          const horizontalDelta = Math.abs(landingX - edgeX);

          if (
            verticalDistance < defaults.minSurfaceGap
            || verticalDistance > defaults.maxClimbDistance
            || horizontalDelta > defaults.maxEdgeLandingDelta
          ) {
            return null;
          }

          return {
            name: actionName,
            edge,
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
        candidates.push({ ...jumpUp, weight: currentSurface.id === config.GROUND_SURFACE_ID ? 1.9 : 1.15 });
      }
      candidates.push(...getClimbUpCandidates(currentSurface));

      if (currentSurface.id !== config.GROUND_SURFACE_ID) {
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

    function getTraversalSurface(surfaceId) {
      return surfaceId === config.GROUND_SURFACE_ID
        ? getGroundSurface()
        : getMarkedSurfaceById(surfaceId);
    }

    function getGeometryKey(surface) {
      const bounds = getBounds();
      return [
        surface.minX, surface.maxX, surface.landY, surface.rect?.bottom,
        bounds.minX, bounds.maxX, bounds.minY, bounds.maxY
      ].join(':');
    }

    function invalidateTraversal(action) {
      action.target = null;
      action.path = [];
      action.arrivalPoint = null;
      action.arrivalSurfaceId = null;
      action.traversal.invalid = true;
      return false;
    }

    function startTraversal(action, plan) {
      refreshSurfaces();
      const source = getCurrentSurface();
      const target = getTraversalSurface(plan.targetSurfaceId);
      action.traversal = { ...plan, sourceSurfaceId: source.id };

      if (!target) {
        return invalidateTraversal(action);
      }

      let landingPoint;
      let path;
      const defaults = getDefaults();

      if (plan.name === 'climbUp') {
        if (!canClimbFrom(source, target, plan.edge)) {
          return invalidateTraversal(action);
        }
        path = buildSideClimbPath(target, plan.edge);
        action.climbFacingDirection = plan.edge === 'left' ? -1 : 1;
        landingPoint = path[path.length - 1];
      } else if (plan.name === 'jumpTo' || plan.name === 'jumpDown') {
        landingPoint = buildSurfaceTarget(target, state.x);
        const gap = plan.name === 'jumpTo' ? state.y - landingPoint.y : landingPoint.y - state.y;
        const limit = plan.name === 'jumpTo' ? defaults.maxJumpUpDistance : defaults.maxJumpDownDistance;
        if (gap < defaults.minSurfaceGap || gap > limit
          || Math.abs(landingPoint.x - state.x) > defaults.maxJumpHorizontalDistance) {
          return invalidateTraversal(action);
        }
        path = buildJumpPath(landingPoint, services.actionCatalog.getSurfaceActionConfig(plan.name).arcLift);
      } else {
        const edgeX = plan.edge === 'left' ? source.edgeLeftX : source.edgeRightX;
        landingPoint = buildSurfaceTarget(target, edgeX);
        const gap = target.landY - state.y;
        if (gap < defaults.minSurfaceGap || gap > defaults.maxClimbDistance
          || Math.abs(landingPoint.x - edgeX) > defaults.maxEdgeLandingDelta) {
          return invalidateTraversal(action);
        }
        path = buildClimbPath(plan.name, edgeX, getEdgeDirection(plan.edge), landingPoint);
      }

      configureTraversalMotion(action, plan.name, target, path, landingPoint);
      action.traversal.geometryKey = getGeometryKey(target);
      return true;
    }

    function refreshActiveTraversal(action) {
      const traversal = action?.traversal;
      if (!traversal) {
        return true;
      }
      if (traversal.invalid) {
        return false;
      }

      refreshSurfaces();
      const target = getTraversalSurface(traversal.targetSurfaceId);
      if (!target) {
        return invalidateTraversal(action);
      }

      const geometryKey = getGeometryKey(target);
      if (geometryKey === traversal.geometryKey) {
        return true;
      }

      const defaults = getDefaults();
      const phase = action.target?.phase || 'land';
      let landingPoint = buildSurfaceTarget(target, action.arrivalPoint.x);
      let path;

      if (traversal.name === 'climbUp') {
        const side = getClimbSide(target, traversal.edge);
        const { height } = services.presentation.getSpriteMetrics();
        if (!side
          || Math.abs(side.x - state.x) > defaults.maxEdgeLandingDelta
          || state.y + height - target.rect.bottom > defaults.maxClimbApproachGap) {
          return invalidateTraversal(action);
        }
        landingPoint = buildSurfaceTarget(target, side.x);
        if (phase === 'land') {
          path = [{ ...landingPoint, phase: 'land' }];
        } else {
          path = buildSideClimbPath(target, traversal.edge)
            .filter((point) => phase !== 'climb' || point.phase !== 'entry');
        }
      } else if (traversal.name === 'jumpTo' || traversal.name === 'jumpDown') {
        if (state.y - landingPoint.y > defaults.maxJumpUpDistance
          || landingPoint.y - state.y > defaults.maxJumpDownDistance
          || Math.abs(landingPoint.x - state.x) > defaults.maxJumpHorizontalDistance) {
          return invalidateTraversal(action);
        }
        const landing = { ...landingPoint, phase: 'land' };
        const bounds = getBounds();
        path = phase === 'apex'
          ? [{
            x: (state.x + landing.x) / 2,
            y: clamp(Math.min(action.target.y, landing.y), bounds.minY, bounds.maxY),
            phase: 'apex'
          }, landing]
          : [landing];
      } else {
        if (landingPoint.y - state.y > defaults.maxClimbDistance
          || landingPoint.y < state.y
          || Math.abs(landingPoint.x - state.x) > defaults.maxEdgeLandingDelta) {
          return invalidateTraversal(action);
        }
        path = [{ ...landingPoint, phase: 'land' }];
      }

      configureTraversalMotion(action, traversal.name, target, path, landingPoint);
      traversal.geometryKey = geometryKey;
      return true;
    }

    function queueSurfaceTraversalPlan(plan) {
      if (!plan) {
        return false;
      }

      refreshSurfaces();
      const runtime = services.runtimeEngine;
      const source = getCurrentSurface();
      const target = getTraversalSurface(plan.targetSurfaceId);
      if (!target) {
        return false;
      }

      if (plan.name === 'jumpTo' || plan.name === 'jumpDown') {
        const landing = buildSurfaceTarget(target, state.x);
        runtime.queueTurn(Math.abs(landing.x - state.x) <= 1
          ? state.direction : (landing.x < state.x ? 1 : -1));
      } else {
        const side = plan.name === 'climbUp' ? getClimbSide(target, plan.edge) : null;
        if (plan.name === 'climbUp' && (!side || !canClimbFrom(source, target, plan.edge))) {
          return false;
        }
        const approachX = side ? side.x : (plan.edge === 'left' ? source.edgeLeftX : source.edgeRightX);
        const approach = buildSurfaceTarget(source, approachX);
        if (Math.abs(state.x - approach.x) > getDefaults().surfaceEdgeApproachThreshold) {
          runtime.queueTravel('walk', approach);
        }
        runtime.queueTurn(plan.name === 'climbUp'
          ? (plan.edge === 'left' ? -1 : 1)
          : getEdgeDirection(plan.edge));
      }

      runtime.queueAction(plan.name, { onStart: (action) => startTraversal(action, plan) });
      return true;
    }

    function isMarkedSurface(surface) {
      return Boolean(surface && surface.id !== config.GROUND_SURFACE_ID);
    }

    return Object.freeze({
      clampXToSurface,
      findLandingSurfaceBetween,
      getBounds,
      getCurrentSurface,
      getGroundSurface,
      getSurfaces,
      isMarkedSurface,
      pickSurfaceTraversalPlan,
      queueSurfaceTraversalPlan,
      refreshActiveTraversal,
      refreshSurfaces,
      resolveSurfaceById,
      setCurrentSurface,
      snapToCurrentSurface
    });
  }

  internals.createSurfacePlanner = createSurfacePlanner;
})(window.SheepInternals);
