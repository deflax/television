// ---------------------------------------------------------------------------
//  HLS - Video player setup (Plyr + HLS.js)
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

(function() {
  const video = document.querySelector("video");
  const streamMedia = document.getElementById('stream-media');
  const audioOnlyPoster = document.getElementById('audio-only-poster');
  const audioPosterChooseBtn = document.getElementById('audio-poster-choose-btn');
  const audioPosterResetBtn = document.getElementById('audio-poster-reset-btn');
  const audioPosterInput = document.getElementById('audio-poster-input');
  const hlsSource = '/live/stream.m3u8';
  const audioHlsSource = '/live/audio.m3u8';
  const audioOnlyPosterFallbackSrc = '/static/images/derks24-ostfriesland.jpg';
  const audioOnlyPosterStore = {
    dbName: 'stream-audio-only-poster',
    storeName: 'posters',
    key: 'selected'
  };
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
      'settings',
      'pip',
      'fullscreen',
    ],
    settings: ['quality'],
  };
  const audioOnlyOptions = {
    controls: [
      'play',
      'progress',
      'current-time',
      'mute',
      'volume',
    ],
  };
  let player = null;
  let audioPlayer = null;
  let videoHlsSourceLoaded = false;

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
      window.hls.currentLevel = -1; // Enable AUTO quality if option.value = 0
    } else {
      window.hls.levels.forEach((level, levelIndex) => {
        if (level.height === newQuality) {
          console.log("HLS.js: Found quality match with " + newQuality);
          window.hls.nextLevel = levelIndex;
        }
      });
    }
  }

  function ensurePlayer() {
    if (!player) {
      player = new Plyr(video, defaultOptions);
    }
  }

  function loadVideoHlsSource() {
    if (window.hls && !videoHlsSourceLoaded) {
      window.hls.loadSource(hlsSource);
      videoHlsSourceLoaded = true;
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

        ensurePlayer();
        syncAudioOnlyView();
        restoreAudioOnlyPreference();
      });

      // Attach media AFTER registering event handlers to avoid race conditions
      window.hls = hls;
      if (shouldRestoreAudioOnly) {
        ensurePlayer();
        syncAudioOnlyView();
        restoreAudioOnlyPreference();
      } else {
        loadVideoHlsSource();
        hls.attachMedia(video);
      }
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS fallback (Safari without HLS.js support)
      if (!shouldRestoreAudioOnly) {
        video.src = hlsSource;
      }
      ensurePlayer();
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
  let audioOnlyPosterObjectUrl = null;
  let shouldRestoreAudioOnly = window.StreamApp.preferences.getBoolean(preferenceKeys.audioOnly) === true;

  function openAudioOnlyPosterDb() {
    return new Promise((resolve, reject) => {
      if (!window.indexedDB) {
        reject(new Error('IndexedDB is not available'));
        return;
      }

      const request = window.indexedDB.open(audioOnlyPosterStore.dbName, 1);

      request.onupgradeneeded = () => {
        const db = request.result;

        if (!db.objectStoreNames.contains(audioOnlyPosterStore.storeName)) {
          db.createObjectStore(audioOnlyPosterStore.storeName);
        }
      };

      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function useAudioOnlyPosterStore(mode, action) {
    return openAudioOnlyPosterDb().then((db) => {
      return new Promise((resolve, reject) => {
        const transaction = db.transaction(audioOnlyPosterStore.storeName, mode);
        const store = transaction.objectStore(audioOnlyPosterStore.storeName);
        const request = action(store);

        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
        transaction.oncomplete = () => db.close();
        transaction.onerror = () => {
          db.close();
          reject(transaction.error);
        };
        transaction.onabort = () => {
          db.close();
          reject(transaction.error);
        };
      });
    });
  }

  function getStoredAudioOnlyPoster() {
    return useAudioOnlyPosterStore('readonly', (store) => store.get(audioOnlyPosterStore.key));
  }

  function saveAudioOnlyPoster(file) {
    return useAudioOnlyPosterStore('readwrite', (store) => store.put(file, audioOnlyPosterStore.key));
  }

  function deleteStoredAudioOnlyPoster() {
    return useAudioOnlyPosterStore('readwrite', (store) => store.delete(audioOnlyPosterStore.key));
  }

  function revokeAudioOnlyPosterObjectUrl() {
    if (audioOnlyPosterObjectUrl) {
      URL.revokeObjectURL(audioOnlyPosterObjectUrl);
      audioOnlyPosterObjectUrl = null;
    }
  }

  function showDefaultAudioOnlyPoster() {
    if (!audioOnlyPoster) {
      return;
    }

    revokeAudioOnlyPosterObjectUrl();
    audioOnlyPoster.src = audioOnlyPosterFallbackSrc;
  }

  function showStoredAudioOnlyPoster(blob) {
    if (!audioOnlyPoster) {
      return;
    }

    revokeAudioOnlyPosterObjectUrl();
    audioOnlyPosterObjectUrl = URL.createObjectURL(blob);
    audioOnlyPoster.src = audioOnlyPosterObjectUrl;
  }

  function restoreAudioOnlyPoster() {
    if (!audioOnlyPoster) {
      return Promise.resolve();
    }

    return getStoredAudioOnlyPoster()
      .then((poster) => {
        if (poster instanceof Blob) {
          showStoredAudioOnlyPoster(poster);
        } else {
          showDefaultAudioOnlyPoster();
        }
      })
      .catch((error) => {
        console.warn('Audio-only poster restore failed:', error);
        showDefaultAudioOnlyPoster();
      });
  }

  function setAudioOnlyPosterControlsVisible(visible) {
    [audioPosterChooseBtn, audioPosterResetBtn].forEach((control) => {
      if (control) {
        control.hidden = !visible;
        control.disabled = !visible;
      }
    });
  }

  function chooseAudioOnlyPoster() {
    if (audioOnly && audioPosterInput) {
      audioPosterInput.click();
    }
  }

  function handleAudioOnlyPosterSelected() {
    if (!audioOnly || !audioPosterInput || !audioPosterInput.files || audioPosterInput.files.length === 0) {
      return;
    }

    const file = audioPosterInput.files[0];

    showStoredAudioOnlyPoster(file);
    saveAudioOnlyPoster(file).catch((error) => {
      console.warn('Audio-only poster save failed:', error);
    });
    audioPosterInput.value = '';
  }

  function resetAudioOnlyPoster() {
    if (!audioOnly) {
      return;
    }

    showDefaultAudioOnlyPoster();
    deleteStoredAudioOnlyPoster().catch((error) => {
      console.warn('Audio-only poster reset failed:', error);
    });
  }

  function getVideoPresentationElement() {
    return video.closest('.plyr') || video;
  }

  function getAudioPresentationElement() {
    return audioEl ? audioEl.closest('.plyr') || audioEl : null;
  }

  function applyAudioPresentationClasses() {
    const audioPresentation = getAudioPresentationElement();

    if (audioPresentation) {
      audioPresentation.classList.add('w-100', 'mt-2');
    }
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
    const audioPresentation = getAudioPresentationElement();
    const audioPoster = audioOnlyPoster;

    setAudioOnlyPosterControlsVisible(audioOnly);

    if (!videoPresentation || !audioPoster) {
      return;
    }

    if (audioOnly) {
      videoPresentation.style.setProperty('display', 'none');
      if (streamMedia) {
        streamMedia.style.removeProperty('display');
      }
      audioPoster.style.display = 'block';
      if (audioPresentation) {
        audioPresentation.style.display = 'block';
      }
      syncSheepSurfaces();
      return;
    }

    videoPresentation.style.removeProperty('display');
    audioPoster.style.display = 'none';
    if (audioPresentation) {
      audioPresentation.style.display = 'none';
    }
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
    window.StreamApp.hlsSource = audioHlsSource;

    audioEl = document.createElement('audio');
    audioEl.id = 'audio-only-player';
    audioEl.className = 'w-100 mt-2';
    audioEl.style.display = 'none';
    (streamMedia || document.body).appendChild(audioEl);
    audioPlayer = new Plyr(audioEl, audioOnlyOptions);
    applyAudioPresentationClasses();
    audioEl.volume = video.volume;
    audioEl.muted = video.muted;

    if (Hls.isSupported()) {
      audioHls = new Hls({
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 90
      });
      audioHls.loadSource(audioHlsSource);
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
      audioEl.src = audioHlsSource;
      audioEl.play().catch(() => console.warn('Audio-only: autoplay blocked'));
    }

    video.pause();
    if (window.hls) {
      window.hls.stopLoad();
      window.hls.detachMedia();
    }
    video.removeAttribute('src');
    video.src = '';
    video.load();
    syncAudioOnlyView();

    window.StreamApp.preferences.setBoolean(preferenceKeys.audioOnly, true);
    updateAudioOnlyButton(true);
    console.log('Audio-only mode: ON');
  }

  function disableAudioOnly() {
    if (!audioOnly) return;
    audioOnly = false;
    window.StreamApp.hlsSource = hlsSource;

    if (audioEl) {
      video.volume = audioEl.volume;
      video.muted = audioEl.muted;
    }

    if (audioHls) {
      audioHls.destroy();
      audioHls = null;
    }
    if (audioPlayer) {
      audioPlayer.destroy();
      audioPlayer = null;
    }
    if (audioEl) {
      audioEl.pause();
      audioEl.remove();
      audioEl = null;
    }

    syncAudioOnlyView();
    if (window.hls) {
      loadVideoHlsSource();
      window.hls.attachMedia(video);
      window.hls.startLoad();
    } else {
      video.src = hlsSource;
      video.load();
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
      sheepBtn.title = 'Sheepy on';
    } else {
      sheepBtn.classList.remove('btn-outline-success');
      sheepBtn.classList.add('btn-outline-secondary');
      sheepBtn.title = 'Sheepy off';
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

  if (audioPosterChooseBtn) {
    audioPosterChooseBtn.addEventListener('click', chooseAudioOnlyPoster);
  }

  if (audioPosterInput) {
    audioPosterInput.addEventListener('change', handleAudioOnlyPosterSelected);
  }

  if (audioPosterResetBtn) {
    audioPosterResetBtn.addEventListener('click', resetAudioOnlyPoster);
  }

  window.addEventListener('beforeunload', revokeAudioOnlyPosterObjectUrl);

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
  setAudioOnlyPosterControlsVisible(false);
  restoreAudioOnlyPoster();
  initPlayer();

  updateSheepButton(getSheepEnabledState());
})();
