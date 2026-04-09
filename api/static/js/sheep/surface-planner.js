window.SheepInternals = window.SheepInternals || {};

((internals) => {
  function createSurfacePlanner(context) {
    const { window, document, state, config, helpers, services } = context;
    const { clamp } = helpers;
    let markedSurfaces = [];

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

      if ((maxX - minX) < getDefaults().minSurfaceWidth) {
        return null;
      }

      const landY = clamp(rect.top - spriteMetrics.height, bounds.minY, bounds.maxY);
      const surfaceId = element.id
        ? `surface:${element.id}`
        : `surface:${index}`;

      return {
        id: surfaceId,
        type: 'marked',
        element,
        minX,
        maxX,
        edgeLeftX: minX,
        edgeRightX: maxX,
        landY,
        lineY: landY + spriteMetrics.height,
        centerX: (minX + maxX) / 2
      };
    }

    function refreshSurfaces() {
      const bounds = getBounds();
      const spriteMetrics = services.presentation.getSpriteMetrics();

      markedSurfaces = Array.from(document.querySelectorAll(config.SURFACE_SELECTOR))
        .map((element, index) => buildSurfaceFromElement(element, index, bounds, spriteMetrics))
        .filter(Boolean)
        .sort((left, right) => left.lineY - right.lineY || left.minX - right.minX);

      if (state.currentSurfaceId !== config.GROUND_SURFACE_ID && !getMarkedSurfaceById(state.currentSurfaceId)) {
        state.currentSurfaceId = config.GROUND_SURFACE_ID;
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
      const motionBudgetSeconds = Math.max(0.22, services.actionCatalog.getTraversalMotionBudgetMs(name) / 1000);
      const actionConfig = services.actionCatalog.getSurfaceActionConfig(name);

      action.target = path.shift() || null;
      action.path = path;
      action.speed = Math.max(
        actionConfig.minimumSpeed,
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
            verticalDistance < defaults.minSurfaceGap
            || verticalDistance > defaults.maxClimbDistance
            || horizontalDelta > defaults.maxEdgeLandingDelta
            || (wantsUpperSurface && surface.id === config.GROUND_SURFACE_ID)
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

      if (currentSurface.id !== config.GROUND_SURFACE_ID) {
        const climbUpLeft = pickEdgeTraversalCandidate(currentSurface, 'climbUp', 1);
        const climbUpRight = pickEdgeTraversalCandidate(currentSurface, 'climbUp', -1);
        const climbUp = pickBestSurfaceCandidate([climbUpLeft, climbUpRight].filter(Boolean));

        if (climbUp) {
          candidates.push({ ...climbUp, weight: 0.95 });
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
      const runtime = services.runtimeEngine;
      const defaults = getDefaults();

      if (!plan) {
        return false;
      }

      if (plan.name === 'jumpTo' || plan.name === 'jumpDown') {
        const targetSurface = resolveSurfaceById(plan.targetSurfaceId);
        const targetPoint = buildSurfaceTarget(targetSurface, state.x);
        const targetDirection = Math.abs(targetPoint.x - state.x) <= 1
          ? state.direction
          : (targetPoint.x < state.x ? 1 : -1);

        runtime.queueTurn(targetDirection);
        runtime.queueAction(plan.name, {
          onStart: (action) => {
            refreshSurfaces();

            const liveTargetSurface = resolveSurfaceById(plan.targetSurfaceId);
            const landingPoint = buildSurfaceTarget(liveTargetSurface, state.x);
            const path = buildJumpPath(landingPoint, services.actionCatalog.getSurfaceActionConfig(plan.name).arcLift);

            configureTraversalMotion(action, plan.name, liveTargetSurface, path, landingPoint);
          }
        });

        return true;
      }

      const sourceSurface = getCurrentSurface();
      const edgeX = plan.edge === 'left' ? sourceSurface.edgeLeftX : sourceSurface.edgeRightX;
      const edgePoint = buildSurfaceTarget(sourceSurface, edgeX);

      if (Math.abs(state.x - edgePoint.x) > defaults.surfaceEdgeApproachThreshold) {
        runtime.queueTravel('walk', edgePoint);
      } else if (state.direction !== getEdgeDirection(plan.edge)) {
        runtime.queueTurn(getEdgeDirection(plan.edge));
      }

      runtime.queueAction(plan.name, {
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

    function isMarkedSurface(surface) {
      return Boolean(surface && surface.id !== config.GROUND_SURFACE_ID);
    }

    return Object.freeze({
      clampXToSurface,
      getBounds,
      getCurrentSurface,
      getGroundSurface,
      isMarkedSurface,
      pickSurfaceTraversalPlan,
      queueSurfaceTraversalPlan,
      refreshSurfaces,
      resolveSurfaceById,
      setCurrentSurface,
      snapToCurrentSurface
    });
  }

  internals.createSurfacePlanner = createSurfacePlanner;
})(window.SheepInternals);
