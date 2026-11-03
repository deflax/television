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
  const daySlider = document.getElementById('epg-day-slider');
  const dayLabel = document.getElementById('epg-day-label');
  const dayControls = document.getElementById('epg-day-controls');
  if (!programmeDiv || !daySlider || !dayLabel || !dayControls) return;
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

  const today = new Date(`${guide.start_day}T00:00:00Z`);
  const selectedDay = window.StreamApp.selectedEpgDay || guide.start_day;
  const selectedDate = new Date(`${selectedDay}T00:00:00Z`);
  let offset = (selectedDate - today) / 86400000;
  if (!Number.isInteger(offset) || offset < 0 || offset >= guide.days) {
    window.StreamApp.selectedEpgDay = null;
    offset = 0;
  }
  const dayKey = window.StreamApp.selectedEpgDay || guide.start_day;
  const day = new Date(`${dayKey}T00:00:00Z`);
  dayLabel.textContent = new Intl.DateTimeFormat('en', {weekday: 'short', timeZone: 'UTC'}).format(day);
  daySlider.max = String(Math.max(0, guide.days - 1));
  daySlider.value = String(offset);
  daySlider.disabled = guide.days < 2;
  daySlider.style.setProperty('--epg-slider-progress', `${guide.days > 1 ? offset * 100 / (guide.days - 1) : 0}%`);
  const weekday = new Intl.DateTimeFormat('en', {weekday: 'long', timeZone: 'UTC'}).format(day);
  daySlider.setAttribute('aria-valuetext', `${weekday} (UTC)`);
  if (daySlider.dataset.ready !== 'true') {
    daySlider.addEventListener('input', function() {
      const app = window.StreamApp;
      const selected = new Date(`${app.currentEpgGuide.start_day}T00:00:00Z`);
      selected.setUTCDate(selected.getUTCDate() + Number(daySlider.value));
      app.selectedEpgDay = daySlider.value === '0' ? null : selected.toISOString().slice(0, 10);
      app.renderEpg(app.currentEpgGuide, app.currentPlayheadId, app.currentPlayheadMetadata);
      programmeDiv.scrollTop = 0;
    });
    daySlider.dataset.ready = 'true';
  }
  dayControls.hidden = false;

  programmeDiv.replaceChildren();
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
