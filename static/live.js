/* Live debug view — polls /live/data every 2s and renders all raw data */

const STATE_CONFIG = {
  open:               { icon: '🟢', label: 'Open' },
  closing_predicted:  { icon: '🟡', label: 'Closing Predicted' },
  closed_inferred:    { icon: '🔴', label: 'Closed Inferred' },
  opening_predicted:  { icon: '🟡', label: 'Opening Predicted' },
  stale_data:         { icon: '⚠️', label: 'Stale Data' },
  unknown:            { icon: '❓', label: 'Unknown' },
};

const BERTH_POSITIONS = {
  '0042': 2,
  '0040': 4,
  '0038': 8,
  '0036': 45,
  '0034': 70,
  '0032': 75,
  '0030': 97,
  '0033': 97,
  '0035': 93,
  '0037': 55,
  '0039': 39,
  '0041': 25,
  'A027': 2,
};

const BERTH_LINE = {
  '0042': 'up', '0040': 'up', '0038': 'up', '0036': 'up', '0034': 'up',
  '0032': 'up', '0030': 'up',
  'A027': 'down',
  '0033': 'down', '0035': 'down', '0037': 'down',
  '0039': 'down', '0041': 'down',
};

const STATION_BERTH_POSITIONS = {
  '0038': { entry: 8, at_platform: 17 },
  '0041': { entry: 25, at_platform: 17 },
  '0032': { entry: 75, at_platform: 83 },
  '0035': { entry: 93, at_platform: 83 },
};

const STATION_POSITIONS = {
  'Angmering': { up: 17, down: 17 },
  'Goring':    { up: 83, down: 83 },
};

function esc(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatDuration(secs) {
  if (secs == null) return '';
  if (secs < 60) return `${Math.round(secs)}s`;
  const m = Math.floor(secs / 60);
  const s = Math.round(secs % 60);
  return s > 0 ? `${m}m ${s}s` : `${m}m`;
}

function formatTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// ── Diagram: render berth ticks once ─────────────────────────────────
function renderBerthTicks() {
  const dia = document.getElementById('track-diagram');
  for (const [berth, pct] of Object.entries(BERTH_POSITIONS)) {
    const line = BERTH_LINE[berth] || 'up';
    const tick = document.createElement('div');
    tick.className = `dia-berth-tick ${line}`;
    tick.style.left = pct + '%';
    dia.appendChild(tick);

    const label = document.createElement('div');
    label.className = `dia-berth-label ${line}`;
    label.style.left = pct + '%';
    label.textContent = berth;
    dia.appendChild(label);
  }
}

// ── Diagram: render berth zones as colored overlays ──────────────────
function renderZones(zones) {
  const dia = document.getElementById('track-diagram');
  // Remove old zones
  dia.querySelectorAll('.dia-zone').forEach(el => el.remove());

  const zoneColors = {
    approach: 'zone-approach',
    strike_in: 'zone-strike_in',
    at_crossing: 'zone-at_crossing',
    clear: 'zone-clear',
  };

  for (const [zoneName, dirs] of Object.entries(zones)) {
    for (const [dir, berths] of Object.entries(dirs)) {
      if (!berths.length) continue;
      const positions = berths.map(b => BERTH_POSITIONS[b]).filter(p => p != null);
      if (!positions.length) continue;
      const minP = Math.min(...positions);
      const maxP = Math.max(...positions);
      const el = document.createElement('div');
      el.className = `dia-zone ${dir} ${zoneColors[zoneName] || ''}`;
      el.style.left = minP + '%';
      el.style.width = Math.max(maxP - minP, 1) + '%';
      el.title = `${zoneName} (${dir}): ${berths.join(', ')}`;
      dia.appendChild(el);
    }
  }
}

// ── Diagram: render trains ───────────────────────────────────────────
function renderTrains(trains) {
  const dia = document.getElementById('track-diagram');
  dia.querySelectorAll('.dia-train').forEach(el => el.remove());

  for (const t of trains) {
    let pct, line;
    const berth = t.last_berth;

    if (berth && STATION_BERTH_POSITIONS[berth] && t.sub_position) {
      const sp = STATION_BERTH_POSITIONS[berth];
      pct = sp[t.sub_position] ?? sp.entry;
      line = t.direction || BERTH_LINE[berth] || 'up';
    } else if (t.phase === 'at_station' && t.station && STATION_POSITIONS[t.station]) {
      line = t.direction || 'up';
      pct = STATION_POSITIONS[t.station][line] || STATION_POSITIONS[t.station].up;
    } else {
      if (!berth || !(berth in BERTH_POSITIONS)) continue;
      pct = BERTH_POSITIONS[berth];
      line = t.direction || BERTH_LINE[berth] || 'up';
    }

    const el = document.createElement('div');
    el.className = `dia-train ${line} phase-${t.phase}`;
    el.style.left = pct + '%';
    el.textContent = t.headcode;
    el.title = `${t.headcode} · ${t.direction || '?'} · ${t.phase} · berth ${berth || '?'} · conf ${t.confidence}`;
    dia.appendChild(el);
  }
}

// ── State banner ─────────────────────────────────────────────────────
function updateStateBanner(crossing) {
  const banner = document.getElementById('state-banner');
  const state = crossing.state || 'unknown';
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.unknown;
  banner.className = 'state-banner ' + state;
  document.getElementById('state-icon').textContent = cfg.icon;
  document.getElementById('state-label').textContent = cfg.label;
  document.getElementById('state-confidence').textContent =
    `${Math.round(crossing.confidence * 100)}%`;

  const sinceSecs = crossing.seconds_in_state ?? crossing.since_secs;
  document.getElementById('state-since').textContent =
    sinceSecs != null ? `for ${formatDuration(sinceSecs)}` : '';

  // Reason for current state — empty string clears the line
  document.getElementById('state-reason').textContent = crossing.reason || '';
}

// ── Crossing light ───────────────────────────────────────────────────
function updateCrossingLight(state) {
  const light = document.getElementById('dia-crossing-light');
  light.className = 'dia-crossing-light ' + (state || 'unknown');
}

// ── Route grid ───────────────────────────────────────────────────────
function updateRoutes(routeData) {
  const grid = document.getElementById('route-grid');
  const config = routeData.config || [];
  const activeNames = new Set((routeData.active || []).map(r => r.name));
  const activeMap = {};
  for (const r of (routeData.active || [])) {
    activeMap[r.name] = r;
  }

  document.getElementById('route-count').textContent = routeData.active ? routeData.active.length : 0;

  if (!config.length) {
    grid.innerHTML = '<div class="empty">No routes configured</div>';
    return;
  }

  // Sort: active first, then by name
  const sorted = [...config].sort((a, b) => {
    const aActive = activeNames.has(a.name) ? 0 : 1;
    const bActive = activeNames.has(b.name) ? 0 : 1;
    if (aActive !== bActive) return aActive - bActive;
    return a.name.localeCompare(b.name);
  });

  grid.innerHTML = sorted.map(r => {
    const isActive = activeNames.has(r.name);
    const cls = isActive ? 'active' : 'inactive';
    const info = activeMap[r.name];
    const held = info && info.held_secs != null ? `${formatDuration(info.held_secs)}` : '';
    return `
      <div class="route-chip ${cls}" title="Address 0x${esc(r.address)} bit ${r.bit} (${esc(r.side)})">
        <span class="route-dot"></span>
        <span>${esc(r.name)}</span>
        ${held ? `<span class="route-held">${held}</span>` : ''}
      </div>`;
  }).join('');
}

// ── Trains table ─────────────────────────────────────────────────────
function updateTrains(trains) {
  const body = document.getElementById('trains-body');
  document.getElementById('train-count').textContent = trains.length;

  if (!trains.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">No trains tracked</td></tr>';
    return;
  }

  // Sort: active phases first, then by age
  const phaseOrder = {
    at_crossing: 0, strike_in: 1, at_station: 2,
    approaching: 3, cleared: 4, lost: 5,
  };
  const sorted = [...trains].sort((a, b) => {
    const pa = phaseOrder[a.phase] ?? 99;
    const pb = phaseOrder[b.phase] ?? 99;
    if (pa !== pb) return pa - pb;
    return a.age_secs - b.age_secs;
  });

  body.innerHTML = sorted.map(t => {
    const dirClass = t.direction === 'up' ? 'dir-east' : t.direction === 'down' ? 'dir-west' : '';
    const dirLabel = t.direction === 'up' ? '↗ E' : t.direction === 'down' ? '↙ W' : '?';
    const staleClass = t.is_stale ? 'stale-row' : '';
    const eta = t.predicted_at_crossing ? formatTime(t.predicted_at_crossing) : '—';
    return `
      <tr class="${staleClass}">
        <td><strong>${esc(t.headcode)}</strong></td>
        <td class="${dirClass}">${dirLabel}</td>
        <td><span class="phase-pill ${esc(t.phase)}">${esc(t.phase)}</span></td>
        <td>${esc(t.last_berth || '—')}</td>
        <td>${t.station ? esc(t.station) + (t.sub_position === 'at_platform' ? ' 🚏' : '') : '—'}</td>
        <td>${t.confidence}</td>
        <td>${formatDuration(t.age_secs)}</td>
        <td>${eta}</td>
      </tr>`;
  }).join('');
}

// ── Zone grid ────────────────────────────────────────────────────────
function updateZones(zones) {
  const grid = document.getElementById('zone-grid');
  const zoneNames = { approach: 'Approach', strike_in: 'Strike-in', at_crossing: 'At Crossing', clear: 'Clear' };

  grid.innerHTML = Object.entries(zones).map(([name, dirs]) => {
    const upBerths = (dirs.up || []).join(', ') || '—';
    const downBerths = (dirs.down || []).join(', ') || '—';
    return `
      <div class="zone-card">
        <div class="zone-card-title">${zoneNames[name] || name}</div>
        <div class="zone-berth"><span class="zone-dir">↗</span> ${esc(upBerths)}</div>
        <div class="zone-berth"><span class="zone-dir">↙</span> ${esc(downBerths)}</div>
      </div>`;
  }).join('');
}

// ── Feed status ──────────────────────────────────────────────────────
function updateFeed(feed) {
  const dot = document.getElementById('feed-status');
  const text = document.getElementById('feed-text');
  if (feed.connected) {
    dot.className = 'feed-dot connected';
    text.textContent = `Feed ${feed.age_secs}s ago`;
  } else {
    dot.className = 'feed-dot disconnected';
    text.textContent = feed.age_secs != null ? `Feed lost (${feed.age_secs}s)` : 'No feed';
  }
}

// ── Raw JSON toggle ──────────────────────────────────────────────────
document.getElementById('raw-toggle').addEventListener('click', () => {
  const pre = document.getElementById('raw-json');
  const arrow = document.querySelector('.toggle-arrow');
  pre.classList.toggle('collapsed');
  arrow.textContent = pre.classList.contains('collapsed') ? '▸' : '▾';
});

// ── Main poll loop ───────────────────────────────────────────────────
let zonesRendered = false;

// Forward any ?token=... from the page URL into the /live/data fetch so
// that admin-protected production deployments can be bookmarked as
// /live?token=<your-admin-token>.
const PAGE_TOKEN = new URLSearchParams(window.location.search).get('token');
const DATA_URL = '/live/data' + (PAGE_TOKEN ? '?token=' + encodeURIComponent(PAGE_TOKEN) : '');

async function poll() {
  try {
    const r = await fetch(DATA_URL);
    if (r.status === 401) {
      document.getElementById('feed-text').textContent =
        'Auth required — append ?token=<admin-token> to the URL';
      document.getElementById('feed-status').className = 'feed-dot disconnected';
      return;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    // Update everything
    updateStateBanner(data.crossing);
    updateCrossingLight(data.crossing.state);
    updateRoutes(data.routes);
    updateTrains(data.trains);
    updateFeed(data.feed);

    // Zones only change on config reload, but render at least once
    if (!zonesRendered && data.berth_zones) {
      updateZones(data.berth_zones);
      renderZones(data.berth_zones);
      zonesRendered = true;
    }

    // Trains on diagram
    renderTrains(data.trains);

    // Update time
    document.getElementById('update-time').textContent = formatTime(data.timestamp);

    // Raw JSON
    document.getElementById('raw-json').textContent = JSON.stringify(data, null, 2);

  } catch (e) {
    document.getElementById('feed-text').textContent = 'Poll error';
    document.getElementById('feed-status').className = 'feed-dot disconnected';
  }
}

// Initial render
renderBerthTicks();
poll();

// Polling — pause when tab hidden
let pollInterval = setInterval(poll, 2000);

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    clearInterval(pollInterval);
  } else {
    poll();
    pollInterval = setInterval(poll, 2000);
  }
});
