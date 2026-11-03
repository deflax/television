// ---------------------------------------------------------------------------
//  EPG - Electronic Program Guide rendering
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

window.StreamApp.currentPlayheadId = null;
window.StreamApp.currentPlayheadMetadata = {};
window.StreamApp.currentEpgGuide = null;
window.StreamApp.selectedEpgDay = null;
window.StreamApp.nowPlayingCopyTimeout = null;

window.StreamApp.setNowPlayingText = function(nowName, text) {
  nowName.textContent = nowName.dataset.copyFeedback === 'true' ? 'copied to clipboard' : text;
};

window.StreamApp.showNowPlayingCopyFeedback = function(nowName) {
  if (window.StreamApp.nowPlayingCopyTimeout) {
    clearTimeout(window.StreamApp.nowPlayingCopyTimeout);
  }
  nowName.dataset.copyFeedback = 'true';
  nowName.textContent = 'copied to clipboard';
  window.StreamApp.nowPlayingCopyTimeout = setTimeout(function() {
    nowName.dataset.copyFeedback = 'false';
    nowName.textContent = nowName.dataset.trackText || '';
    window.StreamApp.nowPlayingCopyTimeout = null;
  }, 1600);
};

window.StreamApp.writeClipboardText = async function(text) {
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    const copied = await navigator.clipboard.writeText(text).then(
      function() { return true; },
      function() { return false; }
    );
    if (copied) return true;
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch {
    copied = false;
  } finally {
    textarea.remove();
  }
  return copied;
};

window.StreamApp.copyNowPlaying = async function() {
  const nowName = document.getElementById('epg-now-name');
  if (!nowName) return;

  const trackText = nowName.dataset.trackText || nowName.textContent;
  if (!trackText) return;

  if (await window.StreamApp.writeClipboardText(trackText)) {
    window.StreamApp.showNowPlayingCopyFeedback(nowName);
  }
};

window.StreamApp.initNowPlayingCopy = function() {
  const nowPlaying = document.getElementById('epg-now-playing');
  if (!nowPlaying || nowPlaying.dataset.copyReady === 'true') return;

  nowPlaying.dataset.copyReady = 'true';
  nowPlaying.addEventListener('click', function(event) {
    window.StreamApp.copyNowPlaying();
    if (event.detail > 0) {
      nowPlaying.blur();
    }
  });
  nowPlaying.addEventListener('keydown', function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    window.StreamApp.copyNowPlaying();
  });
};

window.StreamApp.renderEpg = function(guide, playheadId, metadata = window.StreamApp.currentPlayheadMetadata) {
  const nowPlaying = document.getElementById('epg-now-playing');
  const nowName = document.getElementById('epg-now-name');
  const programmeDiv = document.getElementById('epg-day-programmes');
  const daySelect = document.getElementById('epg-day-select');
  if (!programmeDiv || !daySelect) return;
  window.StreamApp.currentEpgGuide = guide;
  window.StreamApp.currentPlayheadId = playheadId;
  window.StreamApp.currentPlayheadMetadata = metadata || {};

  const catalogue = guide.catalogue;

  // Update "Now Playing" from playhead
  if (nowPlaying && nowName) {
    window.StreamApp.initNowPlayingCopy();
    if (playheadId && catalogue[playheadId]) {
      const channelName = catalogue[playheadId].name;
      const streamTitle = window.StreamApp.currentPlayheadMetadata.stream_title;
      const trimmedStreamTitle = typeof streamTitle === 'string' ? streamTitle.trim() : '';
      const trackText = trimmedStreamTitle || channelName;
      nowName.dataset.trackText = trackText;
      window.StreamApp.setNowPlayingText(nowName, trackText);
      nowPlaying.style.display = 'block';
    } else {
      nowPlaying.style.display = 'none';
    }
  }

  // Keep the selector itself stable during SSE updates so keyboard focus survives.
  const today = new Date(`${guide.start_day}T00:00:00Z`);
  const dayFormat = new Intl.DateTimeFormat('en', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC'
  });
  if (daySelect.dataset.startDay !== guide.start_day || daySelect.options.length !== guide.days) {
    daySelect.replaceChildren();
    for (let offset = 0; offset < guide.days; offset++) {
      const day = new Date(today);
      day.setUTCDate(day.getUTCDate() + offset);
      const option = document.createElement('option');
      option.value = day.toISOString().slice(0, 10);
      const prefix = offset === 0 ? 'Today — ' : (offset === 1 ? 'Tomorrow — ' : '');
      option.textContent = `${prefix}${dayFormat.format(day)} (UTC)`;
      daySelect.appendChild(option);
    }
    daySelect.dataset.startDay = guide.start_day;
  }
  const selectedDay = window.StreamApp.selectedEpgDay;
  if (selectedDay && !Array.from(daySelect.options).some(option => option.value === selectedDay)) {
    window.StreamApp.selectedEpgDay = null;
  }
  daySelect.value = window.StreamApp.selectedEpgDay || guide.start_day;
  document.getElementById('epg-day-controls').hidden = false;
  if (daySelect.dataset.ready !== 'true') {
    daySelect.addEventListener('change', function() {
      const app = window.StreamApp;
      app.selectedEpgDay = daySelect.value === app.currentEpgGuide.start_day ? null : daySelect.value;
      app.renderEpg(app.currentEpgGuide, app.currentPlayheadId, app.currentPlayheadMetadata);
    });
    daySelect.dataset.ready = 'true';
  }

  programmeDiv.replaceChildren();
  const dayKey = daySelect.value;
  const programmes = guide.programmes.filter(programme => programme.start.slice(0, 10) === dayKey);
  if (programmes.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'text-secondary mb-1';
    empty.textContent = 'No scheduled streams.';
    programmeDiv.appendChild(empty);
    return;
  }
  const now = Date.now();
  const list = document.createElement('ul');
  list.className = 'list-unstyled mb-1';
  for (const programme of programmes) {
    const item = document.createElement('li');
    const isActive = playheadId === programme.id
      && now >= Date.parse(programme.start) && now < Date.parse(programme.stop);
    item.className = isActive ? '' : (programme.live ? 'text-warning' : 'text-secondary');
    if (isActive) item.style.color = 'var(--epg-active-purple)';
    const label = document.createElement('strong');
    label.textContent = programme.live ? 'LIVE' : `${programme.start.slice(11, 16)}–${
      programme.stop.slice(0, 10) === dayKey ? programme.stop.slice(11, 16) : '24:00'
    }`;
    item.appendChild(label);
    item.appendChild(document.createTextNode(` ${programme.title}`));
    if (isActive) {
      const icon = document.createElement('i');
      icon.className = 'fa fa-volume-up ms-1';
      item.appendChild(icon);
    }
    if (programme.desc) {
      const details = document.createElement('small');
      details.className = 'text-muted';
      details.textContent = ` - ${programme.desc}`;
      item.appendChild(details);
    }
    list.appendChild(item);
  }
  programmeDiv.appendChild(list);
};
