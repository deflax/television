window.SheepInternals = window.SheepInternals || {};

((internals) => {
  function createActionCatalog(context) {
    const { state, helpers, effects } = context;
    const { clamp } = helpers;

    const SURFACE_ACTION_CONFIG = Object.freeze({
      jumpTo: Object.freeze({
        motionStartIndex: 1,
        minimumSpeed: 188,
        arcLift: 108
      }),
      jumpDown: Object.freeze({
        motionStartIndex: 1,
        minimumSpeed: 182,
        arcLift: 68
      }),
      climbDown: Object.freeze({
        motionStartIndex: 5,
        minimumSpeed: 104,
        edgeOffset: 14,
        verticalLead: 18
      }),
      climbDown2: Object.freeze({
        motionStartIndex: 7,
        minimumSpeed: 112,
        edgeOffset: 10,
        verticalLead: 14
      }),
      climbUp: Object.freeze({
        motionStartIndex: 2,
        minimumSpeed: 114,
        edgeOffset: 12,
        verticalLead: 16
      })
    });

    const PROP_PRESETS = Object.freeze({
      blackSheepVisitor: Object.freeze({
        offsetX: 0,
        offsetY: 12,
        attachToFacing: false,
        flipWithDirection: false
      }),
      alienVisit: Object.freeze({
        offsetX: 0,
        offsetY: -28,
        attachToFacing: false,
        flipWithDirection: false
      }),
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
        flipWithDirection: false
      })
    });

    const DEFAULTS = Object.freeze({
      minWalkSpeed: 40,
      maxWalkSpeed: 66,
      minRunSpeed: 92,
      maxRunSpeed: 126,
      minWalkDistance: 120,
      minRunDistance: 176,
      sleepMinMs: 14000,
      sleepMaxMs: 28000,
      edgeRetargetMinInset: 48,
      edgeRetargetVerticalInset: 40,
      specialActionChance: 0.28,
      runChance: 0.2,
      sleepChance: 0.16,
      surfaceActionChance: 0.32,
      markedSurfaceDwellChance: 0.48,
      abductedMeteorDelayMs: 6800,
      callResponseChance: 0.3,
      markedSurfaceDwellMinMs: 3200,
      markedSurfaceDwellMaxMs: 7600,
      markedSurfaceMinWalkDistance: 28,
      rollTravelDistance: 220,
      rollTravelMs: 3000,
      minRollDistance: 96,
      minSurfaceWidth: 36,
      minSurfaceGap: 24,
      maxJumpHorizontalDistance: 280,
      maxJumpUpDistance: 260,
      maxJumpDownDistance: 280,
      maxClimbDistance: 300,
      maxEdgeLandingDelta: 40,
      surfaceEdgeApproachThreshold: 16,
      surfaceHorizontalPadding: 8
    });

    const {
      queueAction,
      queueSleep,
      getBounds,
      showProp,
      showSecondaryProp,
      showSheep,
      hideSheep,
      hideProp,
      hideSecondaryProp
    } = effects;

    function freezeAction(definition) {
      return Object.freeze({
        frames: Object.freeze(definition.frames.slice()),
        frameMs: definition.frameMs,
        frameDurations: definition.frameDurations ? Object.freeze(definition.frameDurations.slice()) : null,
        frameEvents: definition.frameEvents ? Object.freeze({ ...definition.frameEvents }) : null,
        loop: definition.loop,
        onStart: typeof definition.onStart === 'function' ? definition.onStart : null,
        onComplete: typeof definition.onComplete === 'function' ? definition.onComplete : null,
        keepPlayingOnArrival: Boolean(definition.keepPlayingOnArrival),
        waitForLanding: Boolean(definition.waitForLanding)
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
        keepPlayingOnArrival: settings.keepPlayingOnArrival,
        waitForLanding: settings.waitForLanding
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
      addSequenceFrame(sequence, 3, 320);

      return finalizeSequenceAction(sequence, {
        onComplete: () => {
          const callDirection = state.direction;

          if (Math.random() < DEFAULTS.callResponseChance) {
            queueAction('blackSheep', {
              onStart: (action) => {
                action.callDirection = callDirection;
                action.approachFromLeft = callDirection === 1;
              }
            });
          }
        }
      });
    }

    function createYawnAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 3, 200);
      addSequenceFrame(sequence, 31, 2000);
      addSequenceFrame(sequence, 107, 200);
      addSequenceFrame(sequence, 108, 2000);
      addRepeatedFrames(sequence, [110, 111], 4, 200);
      addSequenceFrame(sequence, 109, 2800);
      addSequenceFrame(sequence, 31, 200);
      addSequenceFrame(sequence, 3, 200);

      return finalizeSequenceAction(sequence, {
        onComplete: () => {
          if (Math.random() < DEFAULTS.sleepChance) {
            queueSleep();
          }
        }
      });
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

    function createMeteorAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 134, 180);
      addSequenceFrame(sequence, 135, 180);
      addSequenceFrame(sequence, 136, 120, (action) => {
        const bounds = getBounds();
        const goLeft = action.entryFromLeft ? false : true;
        const horizontalTravel = Math.min(180, (goLeft ? state.x - bounds.minX : bounds.maxX - state.x) * 0.9);
        const verticalTravel = bounds.maxY - state.y;

        action.target = {
          x: clamp(state.x + (goLeft ? horizontalTravel * -1 : horizontalTravel), bounds.minX, bounds.maxX),
          y: clamp(state.y + verticalTravel, bounds.minY, bounds.maxY)
        };
        action.speed = 220;
      });
      addSequenceFrame(sequence, 137, 100);
      addSequenceFrame(sequence, 138, 100);
      addSequenceFrame(sequence, 139, 100);
      addSequenceFrame(sequence, 140, 100);
      addSequenceFrame(sequence, 141, 100);
      addSequenceFrame(sequence, 142, 100);
      addSequenceFrame(sequence, 143, 120);

    return finalizeSequenceAction(sequence, {
      waitForLanding: true,
      onStart: (action) => {
        const bounds = getBounds();
        const maxEntryDepth = Math.min(180, Math.max(120, (bounds.maxY - bounds.minY) * 0.18));

          action.entryFromLeft = Math.random() < 0.5;
          state.abducted = false;
          state.abductedReturnAt = 0;
          state.x = action.entryFromLeft ? bounds.minX : bounds.maxX;
          state.y = clamp(bounds.minY + (Math.random() * maxEntryDepth), bounds.minY, bounds.minY + maxEntryDepth);
        showSheep();
        hideProp();
        hideSecondaryProp();
      },
      onComplete: (_action, _timestamp, reason) => {
        if (reason === 'arrived' || reason === 'complete') {
          queueAction('bath');
        }
      }
    });
  }

    function createBlackSheepAction() {
      const sequence = createSequence();
      const frameTimings = [260, 240, 220, 280, 260, 340, 280, 240, 220, 260, 300, 220];

      addSequenceFrame(sequence, 73, frameTimings[0], (action) => {
        const bounds = getBounds();

        action.approachFromLeft = typeof action.approachFromLeft === 'boolean'
          ? action.approachFromLeft
          : Math.random() < 0.5;
        state.direction = action.callDirection ?? (action.approachFromLeft ? 1 : -1);
        action.entryOffsetX = action.approachFromLeft
          ? bounds.minX - state.x
          : bounds.maxX - state.x;
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.entryOffsetX;
      });
      addSequenceFrame(sequence, 74, frameTimings[1], (action) => {
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -136 : 136;
      });
      addSequenceFrame(sequence, 75, frameTimings[2], (action) => {
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -108 : 108;
      });
      addSequenceFrame(sequence, 76, frameTimings[3], (action) => {
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -76 : 76;
      });
      addSequenceFrame(sequence, 172, frameTimings[4], (action) => {
        action.startDirection = action.startDirection ?? state.direction;
        state.direction = action.approachFromLeft ? -1 : 1;
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -26 : 26;
      });
      addSequenceFrame(sequence, 173, frameTimings[5], (action) => {
        state.direction = action.approachFromLeft ? -1 : 1;
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -18 : 18;
      });
      addSequenceFrame(sequence, 174, frameTimings[6], (action) => {
        state.direction = action.approachFromLeft ? -1 : 1;
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -12 : 12;
      });
      addSequenceFrame(sequence, 173, frameTimings[7], (action) => {
        state.direction = action.startDirection ?? state.direction;
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -14 : 14;
      });
      addSequenceFrame(sequence, 74, frameTimings[8], (action) => {
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -72 : 72;
      });
      addSequenceFrame(sequence, 73, frameTimings[9], (action) => {
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -104 : 104;
      });
      addSequenceFrame(sequence, 74, frameTimings[10], (action) => {
        showProp(144, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -136 : 136;
      });
      addSequenceFrame(sequence, 3, frameTimings[11], (action) => {
        showProp(145, PROP_PRESETS.blackSheepVisitor);
        state.prop.offsetX = action.approachFromLeft ? -184 : 184;
      });

      return finalizeSequenceAction(sequence, {
        onComplete: (action) => {
          if (typeof action.startDirection === 'number') {
            state.direction = action.startDirection;
          }

          hideProp();
        }
      });
    }

    function createAlienVisitAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 3, 220, (action) => {
        if (typeof action.ufoOffsetX !== 'number') {
          const bounds = getBounds();
          action.ufoOffsetX = Math.random() < 0.5
            ? bounds.minX - state.x
            : bounds.maxX - state.x;
        }

        showSecondaryProp(158, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -90;
        hideProp();
      });
      addSequenceFrame(sequence, 9, 220, (action) => {
        showSecondaryProp(159, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -96;
      });
      addSequenceFrame(sequence, 10, 220, (action) => {
        showSecondaryProp(160, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -92;
      });
      addSequenceFrame(sequence, 10, 220, (action) => {
        showSecondaryProp(161, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -92;
        showProp(166, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX;
        state.prop.offsetY = -28;
      });
      addSequenceFrame(sequence, 154, 220, (action) => {
        showSecondaryProp(162, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -94;
        showProp(167, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX;
        state.prop.offsetY = -8;
      });
      addSequenceFrame(sequence, 155, 220, (action) => {
        showSecondaryProp(163, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -96;
        showProp(168, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX;
        state.prop.offsetY = 12;
      });
      addSequenceFrame(sequence, 156, 240, (action) => {
        showSecondaryProp(164, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -96;
        showProp(166, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX + (action.ufoOffsetX < 0 ? 24 : -24);
        state.prop.offsetY = 20;
      });
      addSequenceFrame(sequence, 157, 260, (action) => {
        showSecondaryProp(165, PROP_PRESETS.alienVisit);
        state.secondaryProp.offsetX = action.ufoOffsetX;
        state.secondaryProp.offsetY = -96;
        showProp(167, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX + (action.ufoOffsetX < 0 ? 52 : -52);
        state.prop.offsetY = 20;
      });
      addSequenceFrame(sequence, 154, 220, (action) => {
        hideSecondaryProp();
        showProp(166, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX < 0 ? -108 : 108;
        state.prop.offsetY = 18;
      });
      addSequenceFrame(sequence, 155, 220, (action) => {
        showProp(167, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX < 0 ? -72 : 72;
        state.prop.offsetY = 14;
      });
      addSequenceFrame(sequence, 156, 220, (action) => {
        showProp(168, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX < 0 ? -38 : 38;
        state.prop.offsetY = 8;
      });
      addSequenceFrame(sequence, 157, 260, (action) => {
        showProp(169, PROP_PRESETS.alienVisit);
        state.prop.offsetX = action.ufoOffsetX < 0 ? -8 : 8;
        state.prop.offsetY = 0;
      });
      addSequenceFrame(sequence, 10, 220, () => {
        hideSheep();
        hideProp();
        hideSecondaryProp();
        state.abducted = true;
        state.abductedReturnAt = state.lastTimestamp + DEFAULTS.abductedMeteorDelayMs;
      });
      addSequenceFrame(sequence, 9, 320);
      addSequenceFrame(sequence, 3, 220);

      return finalizeSequenceAction(sequence, {
        onStart: () => {
          state.abducted = false;
          state.abductedReturnAt = 0;
          showSheep();
          hideProp();
          hideSecondaryProp();
        },
        onComplete: () => {
          hideProp();
          hideSecondaryProp();
        }
      });
    }

    function createUfoBlinkAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 3, 180, () => {
        showProp(158, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 9, 220, () => {
        showProp(170, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 10, 220, () => {
        showProp(171, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 9, 220, () => {
        showProp(170, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 3, 180, () => {
        showProp(158, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 3, 160, hideProp);

      return finalizeSequenceAction(sequence, {
        onComplete: hideProp
      });
    }

    function createGhostPuffAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 3, 220, () => {
        showProp(175, PROP_PRESETS.alienVisit);
      });
      addSequenceFrame(sequence, 3, 180, hideProp);

      return finalizeSequenceAction(sequence, {
        onComplete: hideProp
      });
    }

    function createBathAction() {
      const sequence = createSequence();

      addSequenceFrame(sequence, 3, 400);
      addSequenceFrame(sequence, 9, 400);
      addSequenceFrame(sequence, 10, 400, () => {
        showProp(146, PROP_PRESETS.bath);
      });
      addRepeatedFrames(sequence, [169, 170], 7, 400, (frame) => () => {
        showProp(frame === 169 ? 147 : 148, PROP_PRESETS.bath);
      });
      addSequenceFrame(sequence, 10, 400, hideProp);
      addSequenceFrame(sequence, 171, 3000);
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

      function showWaterProp(action, frame) {
        action.startDirection = action.startDirection ?? state.direction;
        showProp(frame, PROP_PRESETS.water);
        state.prop.attachToFacing = false;
        state.prop.offsetX = PROP_PRESETS.water.offsetX * action.startDirection * -1;
      }

      addSequenceFrame(sequence, 3, 1000, (action) => {
        showWaterProp(action, 152);
      });
      addSequenceFrame(sequence, 12, 300);
      addSequenceFrame(sequence, 13, 300, (action) => {
        action.startDirection = action.startDirection ?? state.direction;
        state.direction = action.startDirection * -1;
      });
      addSequenceFrame(sequence, 103, 300);
      addSequenceFrame(sequence, 104, 300);

      [151, 150, 149, 153].forEach((propFrame, index) => {
        addSequenceFrame(sequence, index % 2 === 0 ? 105 : 106, 300, (action) => {
          showWaterProp(action, propFrame);
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
      return createTraversalAction('jumpDown', [78, 77, 24, 24], [140, 150, 210, 160]);
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
      meteor: createMeteorAction(),
      blackSheep: createBlackSheepAction(),
      alienVisit: createAlienVisitAction(),
      ufoBlink: createUfoBlinkAction(),
      ghostPuff: createGhostPuffAction(),
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
      Object.freeze({ name: 'alienVisit', weight: 0.13 }),
      Object.freeze({ name: 'ufoBlink', weight: 0.06 }),
      Object.freeze({ name: 'ghostPuff', weight: 0.04 }),
      Object.freeze({ name: 'bath', weight: 0.7 }),
      Object.freeze({ name: 'eat', weight: 0.9 }),
      Object.freeze({ name: 'water', weight: 0.7 })
    ]);

    function getActionDefinition(name) {
      return ACTIONS[name] || null;
    }

    function getSurfaceActionConfig(name) {
      return SURFACE_ACTION_CONFIG[name] || null;
    }

    function getTraversalMotionBudgetMs(name) {
      const definition = getActionDefinition(name);
      const actionConfig = getSurfaceActionConfig(name);

      if (!definition || !actionConfig) {
        return 0;
      }

      const frameDurations = definition.frameDurations || definition.frames.map(() => definition.frameMs);
      return frameDurations
        .slice(actionConfig.motionStartIndex)
        .reduce((total, value) => total + value, 0);
    }

    return Object.freeze({
      ACTIONS,
      DEFAULTS,
      PROP_PRESETS,
      SPECIAL_ACTIONS,
      SURFACE_ACTION_CONFIG,
      getActionDefinition,
      getSpecialActions() {
        return SPECIAL_ACTIONS;
      },
      getSurfaceActionConfig,
      getTraversalMotionBudgetMs
    });
  }

  internals.createActionCatalog = createActionCatalog;
})(window.SheepInternals);
