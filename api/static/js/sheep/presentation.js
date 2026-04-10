window.SheepInternals = window.SheepInternals || {};

((internals) => {
  function createPresentation(context) {
    const { window, document, state, refs, config, prefersReducedMotion, services } = context;
    let eventsBound = false;
    let scrollSyncFrame = 0;

    function getLayer() {
      return refs.layer;
    }

    function getSprite() {
      return refs.sprite;
    }

    function getPropSprite() {
      return refs.propSprite;
    }

    function getSecondaryPropSprite() {
      return refs.secondaryPropSprite;
    }

    function hasSprite() {
      return Boolean(getSprite());
    }

    function getSpriteMetrics() {
      const sprite = getSprite();

      return {
        width: sprite ? (sprite.offsetWidth || 72) : 72,
        height: sprite ? (sprite.offsetHeight || 50) : 50
      };
    }

    function applySpriteSheetStyles(element) {
      if (!element) {
        return;
      }

      element.style.backgroundImage = `url('${config.SPRITE_SHEET_URL}')`;
      element.style.backgroundSize = `calc(var(--sheep-size) * ${config.SPRITE_COLUMNS}) calc(var(--sheep-size) * ${config.SPRITE_ROWS})`;
    }

    function applyAtlasFrame(element, frame, force, previousFrame) {
      if (!element) {
        return previousFrame;
      }

      if (!force && previousFrame === frame) {
        return previousFrame;
      }

      const frameSize = element.offsetWidth || 72;
      const column = frame % config.SPRITE_COLUMNS;
      const row = Math.floor(frame / config.SPRITE_COLUMNS);

      element.style.backgroundPosition = `${-column * frameSize}px ${-row * frameSize}px`;
      return frame;
    }

    function setFrame(frame, force) {
      state.currentFrame = applyAtlasFrame(getSprite(), frame, force, state.currentFrame);
    }

    function setPropFrame(frame, force) {
      state.prop.currentFrame = applyAtlasFrame(getPropSprite(), frame, force, state.prop.currentFrame);
    }

    function setSecondaryPropFrame(frame, force) {
      state.secondaryProp.currentFrame = applyAtlasFrame(getSecondaryPropSprite(), frame, force, state.secondaryProp.currentFrame);
    }

    function showProp(frame, preset) {
      const nextPreset = preset || services.actionCatalog.PROP_PRESETS.bath;

      state.prop.visible = true;
      state.prop.offsetX = nextPreset.offsetX;
      state.prop.offsetY = nextPreset.offsetY;
      state.prop.attachToFacing = nextPreset.attachToFacing;
      state.prop.flipWithDirection = nextPreset.flipWithDirection;
      setPropFrame(frame, false);
    }

    function showSecondaryProp(frame, preset) {
      const nextPreset = preset || services.actionCatalog.PROP_PRESETS.bath;

      state.secondaryProp.visible = true;
      state.secondaryProp.offsetX = nextPreset.offsetX;
      state.secondaryProp.offsetY = nextPreset.offsetY;
      state.secondaryProp.attachToFacing = nextPreset.attachToFacing;
      state.secondaryProp.flipWithDirection = nextPreset.flipWithDirection;
      setSecondaryPropFrame(frame, false);
    }

    function showSheep() {
      state.sheepVisible = true;

      if (getSprite()) {
        getSprite().hidden = false;
      }
    }

    function hideSheep() {
      state.sheepVisible = false;

      if (getSprite()) {
        getSprite().hidden = true;
      }
    }

    function hideProp() {
      state.prop.visible = false;
      state.prop.currentFrame = null;

      if (getPropSprite()) {
        getPropSprite().hidden = true;
      }
    }

    function hideSecondaryProp() {
      state.secondaryProp.visible = false;
      state.secondaryProp.currentFrame = null;

      if (getSecondaryPropSprite()) {
        getSecondaryPropSprite().hidden = true;
      }
    }

    function applyPropPosition(propState, propSprite) {
      if (!propSprite) {
        return;
      }

      if (!propState.visible) {
        propSprite.hidden = true;
        return;
      }

      const propOffsetX = propState.attachToFacing
        ? propState.offsetX * state.direction * -1
        : propState.offsetX;
      const propScaleX = propState.flipWithDirection ? state.direction : 1;

      propSprite.hidden = false;
      propSprite.style.transform = `translate3d(${state.x + propOffsetX}px, ${state.y + propState.offsetY}px, 0) scaleX(${propScaleX})`;
    }

    function applyPosition() {
      const sprite = getSprite();

      if (!sprite) {
        return;
      }

      sprite.hidden = !state.sheepVisible;
      sprite.style.transform = `translate3d(${state.x}px, ${state.y}px, 0) scaleX(${state.direction})`;

      applyPropPosition(state.prop, getPropSprite());
      applyPropPosition(state.secondaryProp, getSecondaryPropSprite());
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

    function createSpriteElement(className) {
      const element = document.createElement('div');

      element.className = className;
      element.setAttribute('aria-hidden', 'true');
      applySpriteSheetStyles(element);
      return element;
    }

    function assignLayerElements(layer) {
      refs.layer = layer;
      refs.sprite = layer.querySelector('.sheep-layer__sprite');
      const propSprites = layer.querySelectorAll('.sheep-layer__prop');
      refs.propSprite = propSprites[0] || null;
      refs.secondaryPropSprite = propSprites[1] || null;

      if (refs.sprite) {
        applySpriteSheetStyles(refs.sprite);
      }

      if (refs.propSprite) {
        applySpriteSheetStyles(refs.propSprite);
      }

      if (refs.secondaryPropSprite) {
        applySpriteSheetStyles(refs.secondaryPropSprite);
      }
    }

    function createLayer() {
      const layer = document.createElement('div');

      layer.className = 'sheep-layer';
      layer.setAttribute('aria-hidden', 'true');

      const propSprite = createSpriteElement('sheep-layer__prop');
      propSprite.hidden = true;

      const secondaryPropSprite = createSpriteElement('sheep-layer__prop sheep-layer__prop--secondary');
      secondaryPropSprite.hidden = true;

      const sprite = createSpriteElement('sheep-layer__sprite');

      layer.appendChild(propSprite);
      layer.appendChild(secondaryPropSprite);
      layer.appendChild(sprite);
      document.body.appendChild(layer);

      refs.layer = layer;
      refs.sprite = sprite;
      refs.propSprite = propSprite;
      refs.secondaryPropSprite = secondaryPropSprite;
    }

    function ensureLayer() {
      if (!document.body) {
        return null;
      }

      const existingLayer = document.querySelector('.sheep-layer');

      if (existingLayer) {
        assignLayerElements(existingLayer);
        return { created: false };
      }

      createLayer();
      return { created: true };
    }

    function cancelPendingScrollSync() {
      if (!scrollSyncFrame) {
        return;
      }

      window.cancelAnimationFrame(scrollSyncFrame);
      scrollSyncFrame = 0;
    }

    function syncPresentation() {
      const layer = getLayer();
      const sprite = getSprite();

      if (!layer || !sprite) {
        return;
      }

      if (!state.enabled) {
        cancelPendingScrollSync();
        services.runtimeEngine.stopLoop();
        hideProp();
        hideSecondaryProp();
        layer.hidden = true;
        setFrame(config.NEUTRAL_FRAME, true);
        return;
      }

      layer.hidden = false;

      const shouldSuspend = state.modalOpen || state.reducedMotion;
      layer.classList.toggle('is-suspended', shouldSuspend);

      if (shouldSuspend) {
        cancelPendingScrollSync();
        services.runtimeEngine.stopLoop();
        setFrame(config.NEUTRAL_FRAME, true);
        return;
      }

      services.runtimeEngine.startLoop();
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
      services.surfacePlanner.refreshSurfaces();

      if (!state.activeAction) {
        services.surfacePlanner.snapToCurrentSurface();
      }

      services.runtimeEngine.clampPosition();
      setFrame(state.currentFrame ?? config.NEUTRAL_FRAME, true);
      applyPosition();
    }

    function onScroll() {
      if (scrollSyncFrame) {
        return;
      }

      scrollSyncFrame = window.requestAnimationFrame(() => {
        scrollSyncFrame = 0;
        services.surfacePlanner.refreshSurfaces();

        if (!state.activeAction) {
          services.surfacePlanner.snapToCurrentSurface();
          applyPosition();
        }
      });
    }

    function bindEvents() {
      if (eventsBound) {
        return;
      }

      eventsBound = true;
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

    return Object.freeze({
      applyPosition,
      bindEvents,
      cancelPendingScrollSync,
      ensureLayer,
      enterActionFrame,
      getSpriteMetrics,
      hasSprite,
      hideProp,
      hideSheep,
      setFrame,
      setPropFrame,
      setSecondaryPropFrame,
      showProp,
      showSecondaryProp,
      showSheep,
      hideSecondaryProp,
      syncPresentation
    });
  }

  internals.createPresentation = createPresentation;
})(window.SheepInternals);
