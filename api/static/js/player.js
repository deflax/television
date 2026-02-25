// ---------------------------------------------------------------------------
//  HLS - Video player setup (Plyr + HLS.js)
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

(function() {
  const video = document.querySelector("video");
  const hlsSource = '/live/stream.m3u8';
  const defaultOptions = {};

  // Expose for SSE and audio-only toggle
  window.StreamApp.video = video;
  window.StreamApp.hlsSource = hlsSource;

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
    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Native HLS (Safari)
      video.src = hlsSource;
      const player = new Plyr(video, defaultOptions);
      
      // Native HLS error handling
      video.addEventListener('error', function(e) {
        console.warn('Video error, attempting reload in 3s...', e);
        setTimeout(() => {
          video.load();
          video.play().catch(() => console.warn('Auto-play blocked'));
        }, 3000);
      });
    } else if (Hls.isSupported()) {
      const hls = new Hls({
        // Enable more aggressive error recovery
        enableWorker: true,
        lowLatencyMode: false,
        backBufferLength: 90
      });

      hls.loadSource(hlsSource);
      hls.attachMedia(video);

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
        console.log("HLS.js: manifest parsed");

        // Transform available levels into an array of integers (height values).
        const availableQualities = hls.levels.map((l) => l.height);
        availableQualities.unshift(0); //prepend 0 to quality array

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

        hls.on(Hls.Events.LEVEL_SWITCHED, function (event, data) {
          console.log("HLS.js: level switched");
          var span = document.querySelector(".plyr__menu__container [data-plyr='quality'][value='0'] span");
          if (hls.autoLevelEnabled) {
            span.innerHTML = `AUTO (${hls.levels[data.level].height}p)`;
          } else {
            span.innerHTML = `AUTO`;
          }
        });

        const player = new Plyr(video, defaultOptions);
      });

      window.hls = hls;
    }
  }

  // ---------------------------------------------------------------------------
  //  Audio-Only Toggle (detach video, use <audio> element to save CPU)
  // ---------------------------------------------------------------------------

  const audioOnlyBtn = document.getElementById('audio-only-btn');
  let audioOnly = false;
  let audioHls = null;
  let audioEl = null;

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
    video.closest('.plyr, .content').style.setProperty('display', 'none');
    document.getElementById('audio-only-poster').style.display = 'block';

    updateAudioOnlyButton(true);
    console.log('Audio-only mode: ON');
  }

  function disableAudioOnly() {
    if (!audioOnly) return;
    audioOnly = false;

    // 1. Destroy the audio element
    if (audioHls) {
      audioHls.destroy();
      audioHls = null;
    }
    if (audioEl) {
      audioEl.pause();
      audioEl.remove();
      audioEl = null;
    }

    // Carry over volume and mute state from the audio element
    if (audioEl) {
      video.volume = audioEl.volume;
      video.muted = audioEl.muted;
    }

    // 2. Show the video player and resume
    document.getElementById('audio-only-poster').style.display = 'none';
    video.closest('.plyr, .content').style.removeProperty('display');
    if (window.hls) {
      window.hls.startLoad();
    }
    video.play().catch(() => console.warn('Video resume: autoplay blocked'));

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

  if (audioOnlyBtn) {
    audioOnlyBtn.addEventListener('click', () => {
      if (audioOnly) {
        disableAudioOnly();
      } else {
        enableAudioOnly();
      }
    });
  }

  // Initialize player
  initPlayer();
})();
