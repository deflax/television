// ---------------------------------------------------------------------------
//  EPG - Electronic Program Guide rendering
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

window.StreamApp.currentPlayheadId = null;
window.StreamApp.currentEpgDatabase = {};

window.StreamApp.renderEpg = function(database, playheadId) {
  const nowPlaying = document.getElementById('epg-now-playing');
  const nowName = document.getElementById('epg-now-name');
  const scheduleDiv = document.getElementById('epg-schedule');
  if (!scheduleDiv) return;

  const channelCount = Object.keys(database).length;

  // Update "Now Playing" from playhead
  if (nowPlaying && nowName) {
    if (playheadId && database[playheadId]) {
      nowName.textContent = database[playheadId].name;
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
        const style = isActive ? 'style="color: rgb(132,4,217);"' : '';
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
