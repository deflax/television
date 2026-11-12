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
  const dayButtons = document.getElementById('epg-day-buttons');
  const dayControls = document.getElementById('epg-day-controls');
  const dayDate = document.getElementById('epg-day-date');
  if (!programmeDiv || !dayButtons || !dayControls) return;
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

  const now = Date.now();
  const localDay = date => `${date.getFullYear()}-${
    String(date.getMonth() + 1).padStart(2, '0')
  }-${String(date.getDate()).padStart(2, '0')}`;
  const todayKey = localDay(new Date(now));
  const tomorrow = new Date(now);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowKey = localDay(tomorrow);
  const start = new Date(`${guide.start_day}T00:00:00Z`);
  const end = new Date(start);
  end.setUTCDate(end.getUTCDate() + guide.days);
  const lastDay = localDay(new Date(end.getTime() - 1));
  const date = new Date(start);
  date.setHours(0, 0, 0, 0);
  const days = [];
  while (localDay(date) <= lastDay) {
    days.push(localDay(date));
    date.setDate(date.getDate() + 1);
  }
  const defaultDay = days.includes(todayKey) ? todayKey : days[0];
  if (!days.includes(window.StreamApp.selectedEpgDay)) {
    window.StreamApp.selectedEpgDay = null;
  }
  const dayKey = window.StreamApp.selectedEpgDay || defaultDay;
  const offset = days.indexOf(dayKey);
  const timeFormat = new Intl.DateTimeFormat(undefined, {
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  });
  const programmesByDay = new Map(days.map(day => [day, []]));
  for (const programme of guide.programmes) {
    const start = new Date(programme.start);
    const stop = new Date(programme.stop);
    programmesByDay.get(localDay(start))?.push({programme, start, stop});
  }
  if (dayDate) {
    dayDate.dateTime = dayKey;
    const dayName = dayKey === todayKey ? 'Today' : dayKey === tomorrowKey ? 'Tomorrow'
      : new Intl.DateTimeFormat('en', {weekday: 'long'})
        .format(new Date(`${dayKey}T12:00:00`));
    const zone = timeFormat.resolvedOptions().timeZone;
    dayDate.textContent = `${dayName}, ${dayKey} (${zone})`;
  }
  if (dayButtons.children.length !== 2) {
    dayButtons.replaceChildren();
    for (const direction of [-1, 1]) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = direction < 0 ? '<' : '>';
      button.className = 'btn btn-sm btn-outline-secondary sheep-surface';
      button.setAttribute('aria-label', direction < 0 ? 'Previous day' : 'Next day');
      button.setAttribute('aria-controls', 'epg-day-programmes');
      button.addEventListener('click', function() {
        if (button.disabled) return;
        const app = window.StreamApp;
        const day = button.dataset.day;
        app.selectedEpgDay = day === localDay(new Date(Date.now())) ? null : day;
        app.renderEpg(app.currentEpgGuide, app.currentPlayheadId, app.currentPlayheadMetadata);
      });
      dayButtons.appendChild(button);
    }
  }
  dayButtons.children[0].disabled = offset === 0;
  dayButtons.children[1].disabled = offset === days.length - 1;
  dayButtons.children[0].dataset.day = days[offset - 1];
  dayButtons.children[1].dataset.day = days[offset + 1];
  dayControls.hidden = false;

  // Shared grid cells reserve the tallest day's natural height, including wrapping.
  programmeDiv.replaceChildren();
  for (const pageDay of days) {
    const page = document.createElement('div');
    page.className = 'epg-day-page';
    page.dataset.day = pageDay;
    page.inert = pageDay !== dayKey;
    page.setAttribute('aria-hidden', String(pageDay !== dayKey));
    const programmes = programmesByDay.get(pageDay);
    if (programmes.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'text-secondary mb-1';
      empty.textContent = 'No scheduled streams.';
      page.appendChild(empty);
    } else {
      const list = document.createElement('ul');
      list.className = 'list-unstyled mb-1';
      for (const {programme, start, stop} of programmes) {
        const item = document.createElement('li');
        const isActive = playheadId === programme.id
          && now >= start.getTime() && now < stop.getTime();
        item.className = isActive ? '' : (programme.live ? 'text-warning' : 'text-secondary');
        if (isActive) item.style.color = 'var(--epg-active-purple)';
        const label = document.createElement('strong');
        const stopDay = localDay(stop);
        const midnight = stopDay !== pageDay && stop.getHours() === 0 && stop.getMinutes() === 0;
        const stopText = midnight ? '24:00'
          : `${timeFormat.format(stop)}${stopDay !== pageDay ? ` (${stopDay})` : ''}`;
        label.textContent = programme.live ? 'LIVE' : `${timeFormat.format(start)}–${stopText}`;
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
      page.appendChild(list);
    }
    programmeDiv.appendChild(page);
  }
};
