const STATE_CONFIG = {
  open:               { icon: '🟢', label: 'Open',              sub: 'Barriers are up — road is clear' },
  closing_predicted:  { icon: '🟡', label: 'Closing Soon',      sub: 'Train approaching — barriers expected to lower' },
  closed_inferred:    { icon: '🔴', label: 'Closed',            sub: 'Train at crossing — barriers are down' },
  opening_predicted:  { icon: '🟡', label: 'Opening Soon',      sub: 'Train clearing — barriers expected to raise' },
  stale_data:         { icon: '⚠️', label: 'No Data',           sub: 'Feed connection lost — status unknown' },
  unknown:            { icon: '❓', label: 'Unknown',            sub: 'Waiting for data…' },
};

const DIRECTION_LABELS = { up: '↗ East', down: '↙ West' };

function formatDuration(secs) {
  if (secs == null) return '';
  if (secs < 60) return `${Math.round(secs)}s`;
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

// Berth positions on the schematic (percentage from left edge)
// Real geography: Angmering (west, ~14%) — CROSSING (~27%) — Goring (east, ~86%)
// Crossing is ~885m east of Angmering, ~3.07km west of Goring (BLI1 mileages + BPLAN nwk).
const BERTH_POSITIONS = {
  // Eastbound (up) berths — west to east
  '0042': 2,    // far west approach (eastbound entry to area LA)
  '0040': 4,    // west of Angmering
  '0038': 8,    // west of Angmering P1 (entry side for eastbound)
  '0036': 45,   // just past crossing (eastbound)
  '0034': 70,   // between crossing and Goring
  '0032': 75,   // west of Goring (entry side for eastbound)
  '0030': 97,   // far east of Goring
  // Westbound (down) berths — east to west
  '0033': 97,   // far east of Goring
  '0035': 93,   // east of Goring P2 (entry side for westbound)
  '0037': 55,   // between crossing and Goring
  '0039': 39,   // approaching crossing (westbound)
  '0041': 25,   // east of Angmering P2 (entry side for westbound)
  'A027': 2,    // past Angmering P2, departed westward
};

// Which line (up=P1/top, down=P2/bottom) each berth belongs to
const BERTH_LINE = {
  '0042': 'up', '0040': 'up', '0038': 'up',
  '0036': 'up', '0034': 'up', '0032': 'up', '0030': 'up',
  'A027': 'down',
  '0033': 'down', '0035': 'down', '0037': 'down',
  '0039': 'down', '0041': 'down',
};

// Station positions for AT_STATION trains
const STATION_POSITIONS = {
  'Angmering': { up: 17, down: 17 },
  'Goring':    { up: 83, down: 83 },
};

// Station berth sub-positions — entry vs at_platform positions (% from left)
const STATION_BERTH_POSITIONS = {
  '0038': { entry: 8, at_platform: 17 },    // Eastbound Angmering P1
  '0041': { entry: 25, at_platform: 17 },   // Westbound Angmering P2
  '0032': { entry: 75, at_platform: 83 },   // Eastbound Goring P1
  '0035': { entry: 93, at_platform: 83 },   // Westbound Goring P2
};

// Render berth tick marks (once on load)
function renderBerthTicks() {
  const area = document.getElementById('track-area');
  for (const [berth, pct] of Object.entries(BERTH_POSITIONS)) {
    const line = BERTH_LINE[berth] || 'up';
    // Tick mark
    const tick = document.createElement('div');
    tick.className = `berth-tick ${line}`;
    tick.style.left = pct + '%';
    area.appendChild(tick);
    // Label (hidden by default, toggled via .show-berths)
    const label = document.createElement('div');
    label.className = `berth-label ${line}`;
    label.style.left = pct + '%';
    label.textContent = berth;
    area.appendChild(label);
  }
  // Station ticks — show where AT_STATION trains display
  for (const [name, pos] of Object.entries(STATION_POSITIONS)) {
    for (const line of ['up', 'down']) {
      const tick = document.createElement('div');
      tick.className = `berth-tick station-tick ${line}`;
      tick.style.left = pos[line] + '%';
      area.appendChild(tick);
      const label = document.createElement('div');
      label.className = `berth-label station-tick-label ${line}`;
      label.style.left = pos[line] + '%';
      label.textContent = line === 'up' ? 'P1' : 'P2';
      area.appendChild(label);
    }
  }
}
renderBerthTicks();

async function updateDiagram() {
  try {
    const data = await fetchJSON('/diagram');

    // Update crossing light
    const light = document.getElementById('crossing-light');
    light.className = 'crossing-light ' + data.state;

    // Render trains on track
    const area = document.getElementById('track-area');
    area.querySelectorAll('.track-train').forEach(el => el.remove());

    for (const t of data.trains) {
      let pct, line;
      const berth = t.last_berth;

      if (berth && STATION_BERTH_POSITIONS[berth] && t.sub_position) {
        // Station berth with sub-position — use entry or at_platform position
        const sp = STATION_BERTH_POSITIONS[berth];
        pct = sp[t.sub_position] ?? sp.entry;
        line = t.direction || BERTH_LINE[berth] || 'up';
      } else if (t.phase === 'at_station' && t.station && STATION_POSITIONS[t.station]) {
        // Fallback: at_station without station berth info
        line = t.direction || 'up';
        pct = STATION_POSITIONS[t.station][line] || STATION_POSITIONS[t.station].up;
      } else {
        // Position from berth
        if (!berth || !(berth in BERTH_POSITIONS)) continue;
        pct = BERTH_POSITIONS[berth];
        line = t.direction || BERTH_LINE[berth] || 'up';
      }

      const el = document.createElement('div');
      el.className = `track-train ${line} phase-${t.phase}`;
      el.style.left = pct + '%';
      el.textContent = t.headcode;
      area.appendChild(el);
    }
  } catch (e) { /* ignore */ }
}

async function updateStatus() {
  try {
    const data = await fetchJSON('/status');
    const cfg = STATE_CONFIG[data.state] || STATE_CONFIG.unknown;

    const card = document.getElementById('status-card');
    card.className = 'status-card ' + data.state;

    document.getElementById('status-icon').textContent = cfg.icon;
    document.getElementById('status-label').textContent = cfg.label;
    document.getElementById('status-sub').textContent = cfg.sub;

    // ETA
    const eta = document.getElementById('status-eta');
    if (data.seconds_until_change != null && data.seconds_until_change > 0) {
      const nextLabel = STATE_CONFIG[data.predicted_next_state]?.label || '';
      const trainCount = (data.active_trains || []).length;
      const multi = trainCount > 1 ? ` · ${trainCount} trains` : '';
      eta.textContent = `${nextLabel} in ~${formatDuration(data.seconds_until_change)}${multi}`;
    } else {
      eta.textContent = '';
    }

    // Confidence
    const pct = Math.round(data.confidence * 100);
    document.getElementById('confidence-text').textContent = `${pct}% confidence`;
    document.getElementById('confidence-fill').style.width = `${pct}%`;

    // Trains
    const trainsSection = document.getElementById('trains-section');
    const trainsList = document.getElementById('trains-list');
    if (data.active_trains && data.active_trains.length > 0) {
      trainsSection.classList.remove('tab-hidden');
      trainsList.innerHTML = data.active_trains.map(t => `
        <div class="train">
          <div>
            <span class="train-hc">${esc(t.headcode)}</span>
            <span class="train-info">${DIRECTION_LABELS[t.direction] || '?'}</span>
          </div>
          <span class="phase-badge phase-${esc(t.phase)}">${esc(t.phase.replace('_', ' '))}</span>
        </div>
      `).join('');
    } else {
      trainsSection.classList.add('tab-hidden');
    }

    document.getElementById('footer-text').textContent =
      `Live · updated ${formatTime(new Date().toISOString())}`;
    document.querySelector('footer').classList.remove('disconnected');

  } catch (e) {
    document.getElementById('footer-text').textContent = 'Disconnected';
    document.querySelector('footer').classList.add('disconnected');
  }
}

async function updateRecentTrains() {
  try {
    const data = await fetchJSON('/history?type=passages&limit=20');
    const list = document.getElementById('recent-trains-list');
    if (!data.passages || data.passages.length === 0) {
      list.innerHTML = '<div class="empty">No trains recorded yet</div>';
      return;
    }
    // Deduplicate by headcode+direction+observed_at_crossing (the earlier bug logged duplicates)
    const seen = new Set();
    const unique = data.passages.filter(p => {
      const key = `${p.headcode}-${p.direction}-${p.observed_at_crossing}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 5);

    list.innerHTML = unique.map(p => {
      const dir = DIRECTION_LABELS[p.direction] || '?';
      const time = formatTime(p.observed_clear || p.observed_at_crossing);
      return `
        <div class="recent-train">
          <div>
            <span class="recent-train-hc">${esc(p.headcode)}</span>
            <span class="recent-train-dir">${dir}</span>
          </div>
          <span class="recent-train-time">${time}</span>
        </div>
      `;
    }).join('');
  } catch (e) { /* ignore */ }
}

async function updateHistory() {
  try {
    const data = await fetchJSON('/history?limit=8');
    const list = document.getElementById('history-list');
    if (!data.intervals || data.intervals.length === 0) {
      list.innerHTML = '<div class="empty">No history yet</div>';
      return;
    }
    list.innerHTML = data.intervals.map(iv => {
      const cfg = STATE_CONFIG[iv.state] || {};
      const dur = iv.duration_secs ? formatDuration(iv.duration_secs) : 'ongoing';
      return `
        <div class="history-item">
          <div>
            <span>${cfg.icon || '?'}</span>
            <span class="history-state">${cfg.label || esc(iv.state)}</span>
            <span class="history-duration">· ${dur}</span>
          </div>
          <span class="history-time">${formatTime(iv.started_at)}</span>
        </div>
      `;
    }).join('');
  } catch (e) { /* ignore */ }
}

// Predictions panel
let predictionsVisible = false;
let predictionsInterval = null;

async function updatePredictions() {
  try {
    const data = await fetchJSON('/predictions/windows');
    const list = document.getElementById('predictions-list');
    const currentEl = document.getElementById('predictions-current');
    const now = new Date();

    // Show current closure if active
    if (data.current_closure) {
      const cc = data.current_closure;
      const stateLabel = STATE_CONFIG[cc.state]?.label || cc.state;
      const stateIcon = STATE_CONFIG[cc.state]?.icon || '?';
      const trains = cc.trains.map(t =>
        `<span class="pred-train">${esc(t.headcode)} ${DIRECTION_LABELS[t.direction] || ''}</span>`
      ).join(' ');
      currentEl.innerHTML = `
        <div class="pred-current">
          <span>${stateIcon} ${stateLabel} now</span>
          <div class="pred-current-trains">${trains}</div>
        </div>`;
    } else {
      currentEl.innerHTML = '';
    }

    if (!data.windows || data.windows.length === 0) {
      list.innerHTML = '<div class="empty">No upcoming closures predicted</div>';
      return;
    }

    list.innerHTML = data.windows.map(w => {
      const closeAt = new Date(w.close_at);
      const openAt = new Date(w.open_at);
      const minsUntil = Math.max(0, Math.round((closeAt - now) / 60000));
      const durMins = Math.floor(w.duration_secs / 60);
      const durSecs = w.duration_secs % 60;
      const durText = durSecs > 0 ? `${durMins}m ${durSecs}s` : `${durMins}m`;

      // Proximity class
      let proximity = 'later';
      if (closeAt <= now) proximity = 'active';
      else if (minsUntil <= 5) proximity = 'imminent';
      else if (minsUntil <= 15) proximity = 'soon';

      const untilText = proximity === 'active' ? 'Now'
        : minsUntil < 1 ? '<1 min'
        : `${minsUntil} min`;

      const closeTime = closeAt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
      const openTime = openAt.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });

      const trains = w.trains.map(t => {
        const dir = t.direction === 'east' ? '↗' : '↙';
        const dirClass = t.direction === 'east' ? 'dir-east' : 'dir-west';
        return `
          <div class="pred-train-row">
            <span class="${dirClass}">${dir}</span>
            <strong>${esc(t.headcode)}</strong>
            <span class="pred-eta">@ ${esc(t.crossing_eta)}</span>
            <span class="pred-route">${esc(t.origin)} → ${esc(t.destination)}</span>
          </div>`;
      }).join('');

      return `
        <div class="pred-card ${proximity}">
          <div class="pred-header">
            <div class="pred-times">
              <span class="pred-close">🔴 ${closeTime}</span>
              <span class="pred-arrow">→</span>
              <span class="pred-open">🟢 ${openTime}</span>
              <span class="pred-dur">${durText}</span>
            </div>
            <div class="pred-until">${untilText}</div>
          </div>
          <div class="pred-trains">${trains}</div>
        </div>`;
    }).join('');
  } catch (e) { console.error('Predictions fetch error:', e); }
}

// Upcoming trains panel
let upcomingStation = 'ANG';
let upcomingVisible = false;
let upcomingInterval = null;

function switchTab(name, btn) {
  // Hide all tab contents
  ['tab-map', 'tab-predictions', 'tab-upcoming', 'tab-history', 'tab-info'].forEach(id => {
    document.getElementById(id).classList.add('tab-hidden');
  });
  // Deactivate all tab buttons
  document.querySelectorAll('.tab-bar button').forEach(b => b.classList.remove('active'));
  // Show selected tab
  document.getElementById('tab-' + name).classList.remove('tab-hidden');
  btn.classList.add('active');
  // Manage upcoming polling
  if (name === 'upcoming') {
    if (!upcomingVisible) {
      upcomingVisible = true;
      updateUpcoming();
      upcomingInterval = setInterval(updateUpcoming, 30000);
    }
  } else {
    upcomingVisible = false;
    if (upcomingInterval) {
      clearInterval(upcomingInterval);
      upcomingInterval = null;
    }
  }
  // Manage predictions polling
  if (name === 'predictions') {
    if (!predictionsVisible) {
      predictionsVisible = true;
      updatePredictions();
      predictionsInterval = setInterval(updatePredictions, 30000);
    }
  } else {
    predictionsVisible = false;
    if (predictionsInterval) {
      clearInterval(predictionsInterval);
      predictionsInterval = null;
    }
  }
}

function switchUpcomingStation(crs, btn) {
  upcomingStation = crs;
  btn.parentElement.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  updateUpcoming();
}

function switchHistorySub(name, btn) {
  document.getElementById('history-sub-crossing').classList.toggle('tab-hidden', name !== 'crossing');
  document.getElementById('history-sub-trains').classList.toggle('tab-hidden', name !== 'trains');
  btn.parentElement.querySelectorAll('.sub-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
}

async function updateUpcoming() {
  try {
    const data = await fetchJSON(`/next?station=${upcomingStation}&limit=8`);
    const body = document.getElementById('upcoming-body');
    if (!data.services || data.services.length === 0) {
      body.innerHTML = '<tr><td colspan="7" class="text-muted">No upcoming services</td></tr>';
      return;
    }
    body.innerHTML = data.services.map(s => {
      let arr = s.arrival ? esc(s.arrival) : '—';
      if (s.arrival_scheduled) arr += `<span class="delayed">${esc(s.arrival_scheduled)}</span>`;
      let dep = s.departure ? esc(s.departure) : '—';
      if (s.departure_scheduled) dep += `<span class="delayed">${esc(s.departure_scheduled)}</span>`;
      const statusMap = { 'APPROACHING': 'approaching', 'AT_PLATFORM': 'at_platform', 'ARRIVING': 'approaching' };
      const badgeClass = statusMap[s.status] || '';
      const statusText = s.status ? s.status.replace('_', ' ').toLowerCase() : '';
      const statusBadge = statusText ? `<span class="status-badge ${badgeClass}">${esc(statusText)}</span>` : '';
      const dirArrow = s.direction === 'east' ? '↗' : s.direction === 'west' ? '↙' : '';
      const dirClass = s.direction === 'east' ? 'dir-east' : s.direction === 'west' ? 'dir-west' : '';
      return `<tr>
        <td class="${dirClass}">${dirArrow}</td>
        <td><strong>${esc(s.headcode)}</strong></td>
        <td>${arr}</td>
        <td>${dep}</td>
        <td>${esc(s.origin)}</td>
        <td>${esc(s.destination)}</td>
        <td>${statusBadge}</td>
      </tr>`;
    }).join('');
  } catch (e) { /* ignore */ }
}

// Service health warnings
let warningDismissed = false;
let warningDismissedAt = 0;
let healthInterval = null;
const DISMISS_DURATION_MS = 30 * 60 * 1000; // 30 minutes

async function updateServiceWarnings() {
  try {
    const data = await fetchJSON('/health');
    const banner = document.getElementById('service-warning');
    const textEl = document.getElementById('warning-text');

    // Auto-expire dismiss after 30 minutes
    if (warningDismissed && Date.now() - warningDismissedAt > DISMISS_DURATION_MS) {
      warningDismissed = false;
    }

    // Also reset dismiss when warnings change (new issue or all clear)
    if (!data.warnings || data.warnings.length === 0) {
      banner.classList.remove('visible');
      warningDismissed = false;
      return;
    }

    if (warningDismissed) {
      banner.classList.remove('visible');
      return;
    }

    textEl.textContent = data.warnings.join(' · ');
    banner.classList.add('visible');
  } catch (e) { /* ignore — health endpoint itself may be down */ }
}

document.getElementById('warning-dismiss').addEventListener('click', () => {
  warningDismissed = true;
  warningDismissedAt = Date.now();
  document.getElementById('service-warning').classList.remove('visible');
});

updateServiceWarnings();

updateStatus();
updateDiagram();
updateRecentTrains();
updateHistory();

// Pause polling when tab is hidden to save battery/bandwidth
let statusInterval, diagramInterval, historyInterval, trainInterval;

function startPolling() {
    statusInterval = setInterval(updateStatus, 3000);
    diagramInterval = setInterval(updateDiagram, 3000);
    historyInterval = setInterval(updateRecentTrains, 15000);
    trainInterval = setInterval(updateHistory, 15000);
    healthInterval = setInterval(updateServiceWarnings, 30000);
}

function stopPolling() {
    clearInterval(statusInterval);
    clearInterval(diagramInterval);
    clearInterval(historyInterval);
    clearInterval(trainInterval);
    clearInterval(healthInterval);
}

document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
        stopPolling();
        if (upcomingInterval) {
            clearInterval(upcomingInterval);
            upcomingInterval = null;
        }
        if (predictionsInterval) {
            clearInterval(predictionsInterval);
            predictionsInterval = null;
        }
    } else {
        // Refresh immediately when tab becomes visible again
        updateStatus();
        updateDiagram();
        updateServiceWarnings();
        startPolling();
        if (upcomingVisible) {
            updateUpcoming();
            upcomingInterval = setInterval(updateUpcoming, 30000);
        }
        if (predictionsVisible) {
            updatePredictions();
            predictionsInterval = setInterval(updatePredictions, 30000);
        }
    }
});

startPolling();

// Event listeners (replacing inline onclick/onsubmit handlers for CSP compliance)
document.querySelectorAll('#tab-bar button[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab, btn));
});

document.getElementById('berth-toggle-btn').addEventListener('click', () => {
  document.getElementById('track-area').classList.toggle('show-berths');
});

document.querySelectorAll('[data-station]').forEach(btn => {
  btn.addEventListener('click', () => switchUpcomingStation(btn.dataset.station, btn));
});

document.querySelectorAll('[data-history-sub]').forEach(btn => {
  btn.addEventListener('click', () => switchHistorySub(btn.dataset.historySub, btn));
});

document.getElementById('feedback-open-btn').addEventListener('click', () => {
  document.getElementById('feedback-modal').classList.add('open');
});

document.getElementById('feedback-close-btn').addEventListener('click', () => {
  document.getElementById('feedback-modal').classList.remove('open');
});

document.getElementById('feedback-form').addEventListener('submit', submitFeedback);

// Feedback form
async function submitFeedback(e) {
  e.preventDefault();
  const msg = document.getElementById('feedback-msg');
  const status = document.getElementById('feedback-status');
  const btn = e.target.querySelector('.feedback-submit');
  btn.disabled = true;
  status.textContent = 'Sending…';
  try {
    const r = await fetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg.value }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    status.textContent = 'Thank you!';
    msg.value = '';
    setTimeout(() => {
      document.getElementById('feedback-modal').classList.remove('open');
      status.textContent = '';
    }, 1500);
  } catch (err) {
    status.textContent = 'Failed to send — try again';
    console.error('Feedback error:', err);
  } finally {
    btn.disabled = false;
  }
}
