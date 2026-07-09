// ---------------------------------------------------------------------------
//  EPG - Electronic Program Guide rendering
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

window.StreamApp.currentPlayheadId = null;
window.StreamApp.currentPlayheadMetadata = {};
window.StreamApp.currentEpgDatabase = {};
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
  nowPlaying.addEventListener('click', function() {
    window.StreamApp.copyNowPlaying();
  });
  nowPlaying.addEventListener('keydown', function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    window.StreamApp.copyNowPlaying();
  });
};

window.StreamApp.renderEpg = function(database, playheadId, metadata = window.StreamApp.currentPlayheadMetadata) {
  const nowPlaying = document.getElementById('epg-now-playing');
  const nowName = document.getElementById('epg-now-name');
  const scheduleDiv = document.getElementById('epg-schedule');
  if (!scheduleDiv) return;
  window.StreamApp.currentPlayheadMetadata = metadata || {};

  const channelCount = Object.keys(database).length;

  // Update "Now Playing" from playhead
  if (nowPlaying && nowName) {
    window.StreamApp.initNowPlayingCopy();
    if (playheadId && database[playheadId]) {
      const channelName = database[playheadId].name;
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

  // Hide schedule list when there is only a single channel
  if (channelCount <= 1) {
    scheduleDiv.innerHTML = '';
    return;
  }

  // Separate scheduled vs live entries
  const scheduled = [];
  const live = [];
  for (const [key, entry] of Object.entries(database)) {
    const startAt = entry.start_at;
    if (startAt !== 'now' && startAt !== 'never') {
      scheduled.push({id: key, name: entry.name, startAt: startAt, details: entry.details || ''});
    } else if (startAt === 'now') {
      live.push({id: key, name: entry.name, details: entry.details || ''});
    }
  }

  // Parse military time (e.g. '1745') or legacy hour (e.g. '14') and convert UTC to local
  for (const s of scheduled) {
    const raw = String(s.startAt).trim();
    let utcH, utcM;
    if (raw.length <= 2) {
      utcH = parseInt(raw); utcM = 0;
    } else {
      utcH = parseInt(raw.slice(0, -2)); utcM = parseInt(raw.slice(-2));
    }
    const utcDate = new Date();
    utcDate.setUTCHours(utcH, utcM, 0, 0);
    s.localHour = utcDate.getHours();
    s.localMinute = utcDate.getMinutes();
    s.localTotal = s.localHour * 60 + s.localMinute;
  }
  scheduled.sort((a, b) => a.localTotal - b.localTotal);

  let html = '';
  if (scheduled.length === 0 && live.length === 0) {
    html = '<p class="text-secondary">No scheduled streams.</p>';
  } else {
    if (scheduled.length > 0) {
      html += '<ul class="list-unstyled mb-1">';
      for (const s of scheduled) {
        const isActive = playheadId === s.id;
        const cls = isActive ? '' : 'text-secondary';
        const style = isActive ? 'style="color: var(--epg-active-purple);"' : '';
        const icon = isActive ? ' <i class="fa fa-volume-up"></i>' : '';
        const detail = s.details ? ` <small class="text-muted">- ${s.details}</small>` : '';
        const localStr = s.localHour.toString().padStart(2, '0') + ':' + s.localMinute.toString().padStart(2, '0');
        html += `<li class="${cls}" ${style}><strong>${localStr}</strong> ${s.name}${icon}${detail}</li>`;
      }
      html += '</ul>';
    }
    if (live.length > 0) {
      html += '<ul class="list-unstyled mb-0">';
      for (const l of live) {
        const detail = l.details ? ` <small class="text-muted">- ${l.details}</small>` : '';
        html += `<li class="text-warning"><i class="fa fa-broadcast-tower"></i> ${l.name} <span class="badge bg-danger">LIVE</span>${detail}</li>`;
      }
      html += '</ul>';
    }
  }
  scheduleDiv.innerHTML = html;
};
