// ---------------------------------------------------------------------------
//  HLS - Video player setup (Plyr + HLS.js)
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

(function() {
  const video = document.querySelector("video");
  const streamMedia = document.getElementById('stream-media');
  const hlsSource = '/live/stream.m3u8';
  const preferenceKeys = {
    audioOnly: 'stream.audioOnly',
    sheepEnabled: 'stream.sheepEnabled'
  };
  const defaultOptions = {
    controls: [
      'play-large',
      'play',
      'progress',
      'current-time',
      'mute',
      'volume',
      'captions',
      'settings',
      'pip',
      'fullscreen',
    ],
    settings: ['captions', 'quality', 'speed'],
  };

  // Expose for SSE and audio-only toggle
  window.StreamApp.video = video;
  window.StreamApp.hlsSource = hlsSource;
  window.StreamApp.preferences = {
    keys: preferenceKeys,
    getBoolean(key) {
      try {
        const value = window.localStorage.getItem(key);

        if (value === 'true') {
          return true;
        }

        if (value === 'false') {
          return false;
        }
      } catch (error) {
        console.warn('Preference read failed:', error);
      }

      return null;
    },
    setBoolean(key, value) {
      try {
        window.localStorage.setItem(key, value ? 'true' : 'false');
      } catch (error) {
        console.warn('Preference write failed:', error);
      }
    }
  };

  function updateQuality(newQuality) {
    if (newQuality === 0) {
      window.hls.currentLevel = -1; //Enable AUTO quality if option.value = 0
    } else {
      window.hls.levels.forEach((level, levelIndex) => {
        if (level.height === newQuality) {
          console.log("HLS.js: Found quality match with " + newQuality);
          window.hls.currentLevel = levelIndex;
        }
      });
    }
  }

  function initPlayer() {
    // For more options, see: https://github.com/sampotts/plyr/#options
    // Prefer HLS.js over native HLS — it provides quality switching and better error recovery
    if (Hls.isSupported()) {
      const hls = new Hls({
        // Enable more aggressive error recovery
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 90
      });

      hls.loadSource(hlsSource);

      // HLS.js error handling with automatic recovery
      hls.on(Hls.Events.ERROR, function (event, data) {
        console.warn('HLS error:', data.type, data.details, data.fatal);
        
        if (data.fatal) {
          switch (data.type) {
            case Hls.ErrorTypes.NETWORK_ERROR:
              console.log('HLS: fatal network error, attempting recovery...');
              hls.startLoad();
              break;
            case Hls.ErrorTypes.MEDIA_ERROR:
              console.log('HLS: fatal media error, attempting recovery...');
              hls.recoverMediaError();
              break;
            default:
              console.log('HLS: unrecoverable error, reloading stream in 3s...');
              setTimeout(() => {
                hls.destroy();
                initPlayer();
              }, 3000);
              break;
          }
        }
      });

      // From the m3u8 playlist, hls parses the manifest and returns
      // all available video qualities.
      hls.on(Hls.Events.MANIFEST_PARSED, function (event, data) {
        // Transform available levels into an array of integers (height values).
        const availableQualities = hls.levels.map((l) => l.height);
        availableQualities.unshift(0); //prepend 0 to quality array
        console.log("HLS.js: manifest parsed, qualities:", availableQualities);

        defaultOptions.quality = {
          default: 0, //Default - AUTO
          options: availableQualities,
          forced: true,
          onChange: (e) => updateQuality(e),
        };

        defaultOptions.i18n = {
          qualityLabel: {
            0: 'Auto',
          },
        };

        // Update the Auto label with current resolution when HLS switches levels
        hls.on(Hls.Events.LEVEL_SWITCHED, function (event, data) {
          var span = document.querySelector(".plyr__menu__container [data-plyr='quality'][value='0'] span");
          if (span) {
            if (hls.autoLevelEnabled) {
              span.innerHTML = `AUTO (${hls.levels[data.level].height}p)`;
            } else {
              span.innerHTML = `AUTO`;
            }
          }
        });

        const player = new Plyr(video, defaultOptions);
        syncAudioOnlyView();
        restoreAudioOnlyPreference();
      });

      // Attach media AFTER registering event handlers to avoid race conditions
      hls.attachMedia(video);

      window.hls = hls;
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS fallback (Safari without HLS.js support)
      video.src = hlsSource;
      const player = new Plyr(video, defaultOptions);
      syncAudioOnlyView();
      restoreAudioOnlyPreference();

      video.addEventListener('error', function(e) {
        console.warn('Video error, attempting reload in 3s...', e);
        setTimeout(() => {
          video.load();
          video.play().catch(() => console.warn('Auto-play blocked'));
        }, 3000);
      });
    }
  }

  // ---------------------------------------------------------------------------
  //  Audio-Only Toggle (detach video, use <audio> element to save CPU)
  // ---------------------------------------------------------------------------

  const audioOnlyBtn = document.getElementById('audio-only-btn');
  const sheepBtn = document.getElementById('sheep-btn');
  let audioOnly = false;
  let audioHls = null;
  let audioEl = null;
  let shouldRestoreAudioOnly = window.StreamApp.preferences.getBoolean(preferenceKeys.audioOnly) === true;

  function getVideoPresentationElement() {
    return video.closest('.plyr') || video;
  }

  function syncSheepSurfaces() {
    if (streamMedia) {
      streamMedia.classList.toggle('sheep-surface', !audioOnly);
    }

    if (window.SheepApp && typeof window.SheepApp.refreshSurfaces === 'function') {
      window.SheepApp.refreshSurfaces();
    }
  }

  function syncAudioOnlyView() {
    const videoPresentation = getVideoPresentationElement();
    const audioPoster = document.getElementById('audio-only-poster');

    if (!videoPresentation || !audioPoster) {
      return;
    }

    if (audioOnly) {
      videoPresentation.style.setProperty('display', 'none');
      if (streamMedia) {
        streamMedia.style.removeProperty('display');
      }
      audioPoster.style.display = 'block';
      syncSheepSurfaces();
      return;
    }

    videoPresentation.style.removeProperty('display');
    audioPoster.style.display = 'none';
    syncSheepSurfaces();
  }

  function restoreAudioOnlyPreference() {
    if (!shouldRestoreAudioOnly) {
      return;
    }

    shouldRestoreAudioOnly = false;
    enableAudioOnly();
  }

  function enableAudioOnly() {
    if (audioOnly) return;
    audioOnly = true;

    // 1. Create a hidden <audio> element
    audioEl = document.createElement('audio');
    audioEl.id = 'audio-only-player';
    audioEl.style.display = 'none';
    document.body.appendChild(audioEl);
    // Carry over volume and mute state from the video player
    audioEl.volume = video.volume;
    audioEl.muted = video.muted;

    // 2. Attach HLS.js to the audio element
    if (Hls.isSupported()) {
      audioHls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 90
      });
      audioHls.loadSource(hlsSource);
      audioHls.attachMedia(audioEl);
      audioHls.on(Hls.Events.MANIFEST_PARSED, () => {
        audioEl.play().catch(() => console.warn('Audio-only: autoplay blocked'));
      });
      audioHls.on(Hls.Events.ERROR, (event, data) => {
        if (data.fatal && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          audioHls.startLoad();
        } else if (data.fatal && data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          audioHls.recoverMediaError();
        }
      });
    } else if (audioEl.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari native HLS
      audioEl.src = hlsSource;
      audioEl.play().catch(() => console.warn('Audio-only: autoplay blocked'));
    }

    // 3. Pause and hide the video player (stops video decoding)
    video.pause();
    if (window.hls) {
      window.hls.stopLoad();
    }
    syncAudioOnlyView();

    window.StreamApp.preferences.setBoolean(preferenceKeys.audioOnly, true);
    updateAudioOnlyButton(true);
    console.log('Audio-only mode: ON');
  }

  function disableAudioOnly() {
    if (!audioOnly) return;
    audioOnly = false;

    // 1. Carry over volume and mute state before destroying
    if (audioEl) {
      video.volume = audioEl.volume;
      video.muted = audioEl.muted;
    }

    // 2. Destroy the audio element
    if (audioHls) {
      audioHls.destroy();
      audioHls = null;
    }
    if (audioEl) {
      audioEl.pause();
      audioEl.remove();
      audioEl = null;
    }

    // 3. Show the video player and resume
    syncAudioOnlyView();
    if (window.hls) {
      window.hls.startLoad();
    }
    video.play().catch(() => console.warn('Video resume: autoplay blocked'));

    window.StreamApp.preferences.setBoolean(preferenceKeys.audioOnly, false);
    updateAudioOnlyButton(false);
    console.log('Audio-only mode: OFF');
  }

  function updateAudioOnlyButton(enabled) {
    if (enabled) {
      audioOnlyBtn.classList.remove('btn-outline-secondary');
      audioOnlyBtn.classList.add('btn-outline-warning');
      audioOnlyBtn.title = 'Video Off (audio only)';
    } else {
      audioOnlyBtn.classList.remove('btn-outline-warning');
      audioOnlyBtn.classList.add('btn-outline-secondary');
      audioOnlyBtn.title = 'Toggle video';
    }
  }

  function updateSheepButton(enabled) {
    if (!sheepBtn) {
      return;
    }

    if (enabled) {
      sheepBtn.classList.remove('btn-outline-secondary');
      sheepBtn.classList.add('btn-outline-success');
      sheepBtn.title = 'Sheep on';
    } else {
      sheepBtn.classList.remove('btn-outline-success');
      sheepBtn.classList.add('btn-outline-secondary');
      sheepBtn.title = 'Sheep off';
    }
  }

  function getSheepEnabledState() {
    if (window.SheepApp && typeof window.SheepApp.isEnabled === 'function') {
      return window.SheepApp.isEnabled();
    }

    const storedValue = window.StreamApp.preferences.getBoolean(preferenceKeys.sheepEnabled);
    return storedValue === null ? true : storedValue;
  }

  if (audioOnlyBtn) {
    audioOnlyBtn.addEventListener('click', () => {
      if (audioOnly) {
        disableAudioOnly();
      } else {
        enableAudioOnly();
      }
    });
  }

  if (sheepBtn) {
    updateSheepButton(getSheepEnabledState());
    sheepBtn.addEventListener('click', () => {
      if (!window.SheepApp || typeof window.SheepApp.toggle !== 'function') {
        return;
      }

      const enabled = window.SheepApp.toggle();
      updateSheepButton(enabled);
    });
  }

  // Initialize player
  initPlayer();

  updateSheepButton(getSheepEnabledState());
})();
