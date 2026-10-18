window.SheepInternals = window.SheepInternals || {};

((internals) => {
  // Atlas whitespace is not solid. Surface landing still uses the full sprite bottom.
  const BODY = Object.freeze({ left: 0.2, right: 0.8, top: 0.36, bottom: 0.88 });
  const EPSILON = 0.000001;
  const CONTACT_GAP = 0.05;
  const SPAWN_GAP = 4;

  function createCollisionWorld() {
    const sheep = new Map();
    let debugEnabled = false;
    let debugCanvas = null;
    let debugContext = null;

    function isSheepContext(context) {
      const sprite = context?.refs?.sprite;
      return Number.isFinite(context?.sheepId)
        && context.sheepId > 0
        && Boolean(context.state && context.refs && context.services?.presentation)
        && (!sprite || (
          sprite !== context.refs.propSprite
          && sprite !== context.refs.secondaryPropSprite
          && !sprite.classList?.contains('sheep-layer__prop')
        ));
    }

    function isActive(context) {
      const { state, refs } = context;
      return !context.destroyed
        && !state.destroyed
        && state.enabled !== false
        && state.sheepVisible !== false
        && !state.hidden
        && !state.modalOpen
        && !state.reducedMotion
        && !state.abducted
        && Boolean(refs.sprite)
        && !refs.sprite.hidden
        && refs.sprite.isConnected !== false
        && !refs.layer?.hidden;
    }

    function bodyAt(context, x, y) {
      const { width, height } = context.services.presentation.getSpriteMetrics();
      return {
        left: x + width * BODY.left,
        right: x + width * BODY.right,
        top: y + height * BODY.top,
        bottom: y + height * BODY.bottom
      };
    }

    function awarenessFor(body) {
      const paddingX = (body.right - body.left) * 0.45;
      const paddingY = (body.bottom - body.top) * 0.25;
      return {
        left: body.left - paddingX,
        right: body.right + paddingX,
        top: body.top - paddingY,
        bottom: body.bottom + paddingY
      };
    }

    function overlaps(left, right) {
      return left.left < right.right - EPSILON
        && left.right > right.left + EPSILON
        && left.top < right.bottom - EPSILON
        && left.bottom > right.top + EPSILON;
    }

    function overlapNormal(body, obstacle, dx, dy) {
      const leftDepth = body.right - obstacle.left;
      const rightDepth = obstacle.right - body.left;
      const topDepth = body.bottom - obstacle.top;
      const bottomDepth = obstacle.bottom - body.top;
      const xDepth = Math.min(leftDepth, rightDepth);
      const yDepth = Math.min(topDepth, bottomDepth);
      const normalX = leftDepth === rightDepth ? Math.sign(dx) || 1 : (leftDepth < rightDepth ? -1 : 1);
      const normalY = topDepth === bottomDepth ? Math.sign(dy) || 1 : (topDepth < bottomDepth ? -1 : 1);

      if (xDepth < yDepth || (Math.abs(xDepth - yDepth) <= EPSILON && dx * normalX >= dy * normalY)) {
        return { x: normalX, y: 0 };
      }
      return { x: 0, y: normalY };
    }

    // Slab intersection over the complete proposed segment, not only its endpoint.
    function sweep(body, obstacle, dx, dy) {
      if (overlaps(body, obstacle)) {
        const separationX = body.left + body.right - obstacle.left - obstacle.right;
        const separationY = body.top + body.bottom - obstacle.top - obstacle.bottom;
        // Include coincident centers and tangential escape: neither is an approach.
        if (dx * separationX + dy * separationY >= -EPSILON) {
          return null;
        }
        return { time: 0, normal: overlapNormal(body, obstacle, dx, dy) };
      }

      let entryX = -Infinity;
      let exitX = Infinity;
      let entryY = -Infinity;
      let exitY = Infinity;

      if (Math.abs(dx) <= EPSILON) {
        if (body.right <= obstacle.left + EPSILON || body.left >= obstacle.right - EPSILON) {
          return null;
        }
      } else {
        const first = (obstacle.left - body.right) / dx;
        const last = (obstacle.right - body.left) / dx;
        entryX = Math.min(first, last);
        exitX = Math.max(first, last);
      }

      if (Math.abs(dy) <= EPSILON) {
        if (body.bottom <= obstacle.top + EPSILON || body.top >= obstacle.bottom - EPSILON) {
          return null;
        }
      } else {
        const first = (obstacle.top - body.bottom) / dy;
        const last = (obstacle.bottom - body.top) / dy;
        entryY = Math.min(first, last);
        exitY = Math.max(first, last);
      }

      const entry = Math.max(entryX, entryY);
      const exit = Math.min(exitX, exitY);
      if (entry > exit || entry < -EPSILON || entry > 1 || exit <= EPSILON) {
        return null;
      }

      const normal = entryX >= entryY
        ? { x: dx > 0 ? -1 : 1, y: 0 }
        : { x: 0, y: dy > 0 ? -1 : 1 };
      return { time: Math.max(0, entry), normal };
    }

    function movementIntent(record, timestamp) {
      const { state } = record.context;
      const action = state.activeAction;
      if (action?.target && action.speed > 0 && action.motionEnabled !== false && !state.menuOpen) {
        const dx = action.target.x - state.x;
        const dy = action.target.y - state.y;
        const distance = Math.hypot(dx, dy);
        if (distance > EPSILON) {
          return { x: dx / distance, y: dy / distance };
        }
      }

      // The runtime action is authoritative; the sample fallback supports direct movers.
      if (!('activeAction' in state) && timestamp - record.timestamp >= 0 && timestamp - record.timestamp <= 100) {
        return { x: record.dx, y: record.dy };
      }
      return { x: 0, y: 0 };
    }

    function shouldYield(context, otherRecord, body, otherBody, dx, dy, timestamp) {
      const separationX = (otherBody.left + otherBody.right - body.left - body.right) / 2;
      const separationY = (otherBody.top + otherBody.bottom - body.top - body.bottom) / 2;
      if (dx * separationX + dy * separationY <= EPSILON) {
        return false;
      }

      const otherIntent = movementIntent(otherRecord, timestamp);
      if (Math.hypot(otherIntent.x, otherIntent.y) <= EPSILON) {
        return true;
      }
      // A leader moving away keeps going; a follower owns its own following distance.
      if (otherIntent.x * separationX + otherIntent.y * separationY > EPSILON) {
        return true;
      }
      return context.sheepId > otherRecord.context.sheepId;
    }

    function register(context) {
      if (!isSheepContext(context)) {
        return false;
      }
      const existing = sheep.get(context.sheepId);
      if (existing) {
        return existing.context === context;
      }
      sheep.set(context.sheepId, {
        context, dx: 0, dy: 0, timestamp: -Infinity,
        yieldingTo: null, yieldUntil: 0, contact: null
      });
      if (debugEnabled) {
        renderDebug();
      }
      return true;
    }

    function unregister(context) {
      if (sheep.get(context?.sheepId)?.context !== context) {
        return;
      }
      sheep.delete(context.sheepId);
      if (!sheep.size && debugCanvas) {
        debugCanvas.remove();
        debugCanvas = null;
        debugContext = null;
      } else if (debugEnabled) {
        renderDebug();
      }
    }

    function placeOnSurface(context, surface) {
      if (sheep.get(context?.sheepId)?.context !== context || !surface) {
        return null;
      }
      const desiredX = Math.min(surface.maxX, Math.max(surface.minX, context.state.x));
      const body = bodyAt(context, 0, surface.landY);
      const blocked = [];
      for (const record of sheep.values()) {
        if (record.context === context || !isActive(record.context)) {
          continue;
        }
        const other = bodyAt(record.context, record.context.state.x, record.context.state.y);
        if (body.bottom > other.top + EPSILON && body.top < other.bottom - EPSILON) {
          blocked.push({ min: other.left - body.right - SPAWN_GAP, max: other.right - body.left + SPAWN_GAP });
        }
      }
      blocked.sort((left, right) => left.min - right.min);
      let cursor = surface.minX;
      let bestX = null;
      let bestDistance = Infinity;
      function consider(min, max) {
        if (min > max) {
          return;
        }
        const x = Math.min(max, Math.max(min, desiredX));
        const distance = Math.abs(x - desiredX);
        if (distance < bestDistance) {
          bestX = x;
          bestDistance = distance;
        }
      }
      for (const interval of blocked) {
        if (interval.max < cursor || interval.min > surface.maxX) {
          continue;
        }
        consider(cursor, Math.min(surface.maxX, interval.min));
        cursor = Math.max(cursor, interval.max);
      }
      consider(cursor, surface.maxX);
      // A completely occupied surface has no valid placement; never overlap as a fallback.
      if (bestX === null) {
        return null;
      }
      context.state.x = bestX;
      context.state.y = surface.landY;
      context.state.currentSurfaceId = surface.id;
      return { x: bestX, y: surface.landY };
    }

    function resolveMovement(context, from, to, timestamp) {
      const result = { x: to.x, y: to.y, contact: null };
      const record = sheep.get(context?.sheepId);
      if (!record || record.context !== context || !isActive(context)) {
        return result;
      }
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      record.dx = 0;
      record.dy = 0;
      record.timestamp = timestamp;
      record.contact = null;
      if (Math.hypot(dx, dy) <= EPSILON) {
        return result;
      }

      const body = bodyAt(context, from.x, from.y);
      let earliest = null;
      function consider(hit, otherId, soft, wait = false) {
        if (!hit) {
          return;
        }
        if (!earliest || hit.time < earliest.time - EPSILON || (
          Math.abs(hit.time - earliest.time) <= EPSILON
          && ((earliest.soft && !soft) || (earliest.soft === soft && otherId < earliest.otherId))
        )) {
          earliest = { ...hit, otherId, soft, wait };
        }
      }

      for (const otherRecord of sheep.values()) {
        const otherContext = otherRecord.context;
        if (otherContext === context || !isActive(otherContext)) {
          continue;
        }
        const otherBody = bodyAt(otherContext, otherContext.state.x, otherContext.state.y);
        const otherIsYielding = otherRecord.yieldingTo === context.sheepId
          && otherRecord.yieldUntil > timestamp;
        consider(sweep(body, otherBody, dx, dy), otherContext.sheepId, false, otherIsYielding);
        if (!otherIsYielding && !overlaps(body, otherBody)
          && shouldYield(context, otherRecord, body, otherBody, dx, dy, timestamp)) {
          consider(sweep(body, awarenessFor(otherBody), dx, dy), otherContext.sheepId, true);
        }
      }

      if (earliest) {
        const progress = Math.max(0, earliest.time - CONTACT_GAP / Math.hypot(dx, dy));
        result.x = from.x + dx * progress;
        result.y = from.y + dy * progress;
        result.contact = {
          kind: 'sheep',
          otherId: earliest.otherId,
          normal: earliest.normal,
          timeOfImpact: earliest.time,
          soft: earliest.soft,
          wait: earliest.wait
        };
        record.contact = result.contact;
        if (earliest.soft) {
          record.yieldingTo = earliest.otherId;
          record.yieldUntil = timestamp + 1200;
        }
      }
      record.dx = result.x - from.x;
      record.dy = result.y - from.y;
      return result;
    }

    function isDebugEnabled() {
      return debugEnabled;
    }

    function setDebug(enabled) {
      debugEnabled = Boolean(enabled);
      if (!debugEnabled && debugCanvas) {
        debugCanvas.hidden = true;
      } else if (debugEnabled) {
        renderDebug();
      }
    }

    function renderDebug() {
      if (!debugEnabled || !sheep.size) {
        return;
      }
      let first = null;
      for (const record of sheep.values()) {
        if (isActive(record.context)) {
          first = record.context;
          break;
        }
      }
      if (!first) {
        if (debugCanvas) {
          debugCanvas.hidden = true;
        }
        return;
      }
      const { window, document } = first;
      if (!debugCanvas) {
        debugCanvas = document.createElement('canvas');
        debugCanvas.setAttribute('aria-hidden', 'true');
        debugCanvas.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483647;';
        document.body.appendChild(debugCanvas);
        debugContext = debugCanvas.getContext('2d');
      }
      if (!debugContext) {
        debugCanvas.hidden = true;
        return;
      }
      debugCanvas.hidden = false;
      const ratio = Math.min(2, window.devicePixelRatio || 1);
      const width = window.innerWidth;
      const height = window.innerHeight;
      const pixelWidth = Math.round(width * ratio);
      const pixelHeight = Math.round(height * ratio);
      if (debugCanvas.width !== pixelWidth || debugCanvas.height !== pixelHeight) {
        debugCanvas.width = pixelWidth;
        debugCanvas.height = pixelHeight;
        debugCanvas.style.width = `${width}px`;
        debugCanvas.style.height = `${height}px`;
      }
      debugContext.setTransform(ratio, 0, 0, ratio, 0, 0);
      debugContext.clearRect(0, 0, width, height);
      debugContext.lineWidth = 1;
      debugContext.font = '11px monospace';
      debugContext.setLineDash([]);
      debugContext.strokeStyle = '#36df95';
      for (const surface of first.services.surfacePlanner.getSurfaces()) {
        const metrics = first.services.presentation.getSpriteMetrics();
        debugContext.beginPath();
        debugContext.moveTo(surface.rect?.left ?? surface.minX, surface.lineY);
        debugContext.lineTo(surface.rect?.right ?? surface.maxX + metrics.width, surface.lineY);
        debugContext.stroke();
      }
      for (const record of sheep.values()) {
        const context = record.context;
        if (!isActive(context)) {
          continue;
        }
        const body = bodyAt(context, context.state.x, context.state.y);
        const awareness = awarenessFor(body);
        debugContext.setLineDash([3, 3]);
        debugContext.strokeStyle = '#f5cc58';
        debugContext.strokeRect(awareness.left, awareness.top, awareness.right - awareness.left, awareness.bottom - awareness.top);
        debugContext.setLineDash([]);
        debugContext.strokeStyle = '#ff657a';
        debugContext.strokeRect(body.left, body.top, body.right - body.left, body.bottom - body.top);
        debugContext.fillStyle = '#ff657a';
        debugContext.fillText(String(context.sheepId), body.left, body.top - 3);
        if (record.contact && window.performance.now() - record.timestamp < 600) {
          const centerX = (body.left + body.right) / 2;
          const centerY = (body.top + body.bottom) / 2;
          debugContext.strokeStyle = '#ffffff';
          debugContext.beginPath();
          debugContext.moveTo(centerX, centerY);
          debugContext.lineTo(centerX + record.contact.normal.x * 20, centerY + record.contact.normal.y * 20);
          debugContext.stroke();
        }
      }
    }

    return Object.freeze({ register, unregister, placeOnSurface, resolveMovement, isDebugEnabled, setDebug, renderDebug });
  }

  internals.createCollisionWorld = createCollisionWorld;
})(window.SheepInternals);
