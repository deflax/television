// ---------------------------------------------------------------------------
//  SSE - Server-Sent Events for real-time updates
// ---------------------------------------------------------------------------

window.StreamApp = window.StreamApp || {};

(function() {
  const app = window.StreamApp;
  const visitorNumber = document.getElementById('visitor-number');

  function connectSSE() {
    const evtSource = new EventSource('/events');

    evtSource.addEventListener('playhead', function(e) {
      const data = JSON.parse(e.data);
      app.currentPlayheadId = data.id || null;

      // Re-render EPG to update active highlight
      if (Object.keys(app.currentEpgDatabase).length > 0) {
        app.renderEpg(app.currentEpgDatabase, app.currentPlayheadId);
      }
      // Note: Video source is static (/live/stream.m3u8), mux service handles switching
    });

    evtSource.addEventListener('visitors', function(e) {
      const data = JSON.parse(e.data);
      if (visitorNumber) {
        visitorNumber.textContent = data.visitors;
      }
    });

    evtSource.addEventListener('epg', function(e) {
      try {
        app.currentEpgDatabase = JSON.parse(e.data);
        app.renderEpg(app.currentEpgDatabase, app.currentPlayheadId);
      } catch (err) {
        console.warn('EPG: failed to parse data', err);
      }
    });

    evtSource.onerror = function() {
      console.warn("SSE: connection lost, reconnecting...");
      // EventSource auto-reconnects, but close explicitly if CLOSED
      if (evtSource.readyState === EventSource.CLOSED) {
        evtSource.close();
        setTimeout(connectSSE, 3000);
      }
    };
  }

  // Initialize SSE
  connectSSE();
})();
