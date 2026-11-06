'use strict';

const POLL_MS = 2000;
const state = { groups: [], procs: [], advice: null, checked: new Set(), touched: false };

const $ = (id) => document.getElementById(id);

function mb(bytes) {
  const m = bytes / 1048576;
  return m >= 1024 ? `${(m / 1024).toFixed(1)} GB` : `${Math.round(m)} MB`;
}

function idleText(seconds) {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.round(seconds / 60)}m`;
}

async function poll() {
  try {
    const [stateRes, snapRes] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/snapshot'),
    ]);
    const data = await stateRes.json();
    const snap = await snapRes.json();
    state.groups = data.groups || [];
    state.procs = snap.processes || [];
    state.advice = data.advice || null;
    applyAdvice();
    render(data);
  } catch (err) {
    $('advice-body').textContent = `Lost contact with the monitor: ${err.message}`;
  }
}

// Claude's targets arrive pre-ticked, but only until the user touches a
// checkbox. After that their selection wins, so a poll never undoes a click.
function applyAdvice() {
  if (state.touched || !state.advice || !state.advice.available) return;
  const names = new Set();
  for (const target of state.advice.targets || []) {
    const match = state.procs.find((p) => p.pid === target.pid);
    if (match) names.add(match.name.toLowerCase());
    else if (target.name) names.add(String(target.name).toLowerCase());
  }
  state.checked = names;
}

function render(data) {
  const totals = data.totals || {};
  const ram = totals.ram_percent || 0;
  $('ram-value').textContent = `${mb(totals.ram_used || 0)} / ${mb(totals.ram_total || 0)}  ${ram.toFixed(0)}%`;
  $('ram-bar').style.width = `${ram}%`;
  $('ram-bar').classList.toggle('high', ram >= 85);

  const cpu = totals.cpu_percent || 0;
  $('cpu-value').textContent = `${cpu.toFixed(0)}%`;
  $('cpu-bar').style.width = `${Math.min(cpu, 100)}%`;

  $('proc-count').textContent = `${state.groups.length} apps`;
  $('window-info').textContent =
    `${data.sample_count || 0} samples over ${Math.round((data.window_seconds || 0) / 60)} min`;

  renderList();
  renderTrend();
  renderAdvice();
}

function renderList() {
  const list = $('process-list');
  list.innerHTML = '';
  const suggested = new Set(state.checked);

  for (const group of state.groups.slice(0, 40)) {
    const row = document.createElement('div');
    row.className = 'row';
    if (!group.killable) row.classList.add('blocked');
    if (suggested.has(group.name) && group.killable) row.classList.add('suggested');

    const box = document.createElement('input');
    box.type = 'checkbox';
    box.disabled = !group.killable;
    box.checked = group.killable && state.checked.has(group.name);
    box.addEventListener('change', () => {
      state.touched = true;
      if (box.checked) state.checked.add(group.name);
      else state.checked.delete(group.name);
      renderList();
      updateButton();
    });

    const label = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'row-name';
    name.textContent = group.name;
    const meta = document.createElement('div');
    meta.className = 'row-meta';
    const bits = [];
    if (group.count > 1) bits.push(`${group.count} procs`);
    if (!group.killable) bits.push('system-critical');
    else if (group.idle_seconds > 300) bits.push(`idle ${idleText(group.idle_seconds)}`);
    if (group.growing) bits.push(`+${group.rss_trend} MB/min`);
    meta.textContent = bits.join('  ·  ');
    label.append(name, meta);

    const rss = document.createElement('div');
    rss.className = 'row-rss';
    rss.textContent = mb(group.rss);

    row.append(box, label, rss);
    list.append(row);
  }
  updateButton();
}

function renderTrend() {
  const body = $('trend-body');
  body.innerHTML = '';
  for (const group of state.groups.slice(0, 15)) {
    const tr = document.createElement('tr');
    const tag = group.growing
      ? '<span class="tag growing">growing</span>'
      : group.idle_seconds > 300
        ? '<span class="tag idle">idle</span>'
        : '<span class="tag active">active</span>';
    tr.innerHTML =
      `<td>${escapeHtml(group.name)}</td>` +
      `<td>${mb(group.rss)}</td>` +
      `<td>${group.cpu_percent.toFixed(1)}%</td>` +
      `<td>${idleText(group.idle_seconds)}</td>` +
      `<td>${tag}</td>`;
    body.append(tr);
  }
}

function renderAdvice() {
  const box = $('advice-body');
  const advice = state.advice;
  if (!advice || !advice.available) {
    box.className = 'advice-body muted';
    box.textContent = advice && advice.error
      ? `advice.json could not be read: ${advice.error}`
      : 'No advice yet. Ask Claude to check your laptop.';
    return;
  }
  box.className = 'advice-body';
  box.textContent = advice.summary || 'Claude wrote advice with no summary.';
  if (advice.notes) {
    const note = document.createElement('div');
    note.className = 'advice-note';
    note.textContent = advice.notes;
    box.append(note);
  }
}

function updateButton() {
  const btn = $('optimize');
  const n = selectedTargets().length;
  btn.disabled = n === 0;
  btn.textContent = n === 0 ? 'Optimize' : `Optimize  ·  ${n} processes`;
}

function selectedTargets() {
  const targets = [];
  for (const proc of state.procs) {
    if (!state.checked.has((proc.name || '').toLowerCase())) continue;
    if (proc.create_time === null || proc.create_time === undefined) continue;
    targets.push({ pid: proc.pid, create_time: proc.create_time });
  }
  return targets;
}

async function optimize(targets, force) {
  const btn = $('optimize');
  btn.disabled = true;
  btn.textContent = 'Working...';
  try {
    const res = await fetch('/api/kill', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targets, force: Boolean(force) }),
    });
    renderResults(await res.json());
  } catch (err) {
    renderResults({ ok: false, error: err.message });
  }
  state.checked.clear();
  state.touched = true;
  await poll();
}

function renderResults(data) {
  const box = $('results');
  box.innerHTML = '';
  if (!data.ok) {
    box.innerHTML = `<div class="result failed">${escapeHtml(data.error || 'request failed')}</div>`;
    return;
  }

  let freed = 0;
  for (const r of data.results || []) {
    if (r.status === 'closed') freed += r.freed_bytes || 0;
    const div = document.createElement('div');
    div.className = `result ${r.status}`;
    const left = document.createElement('span');
    left.textContent = `${r.name} ${r.status}`;
    div.append(left);

    if (r.status === 'refused') {
      const force = document.createElement('button');
      force.className = 'force-btn';
      force.textContent = 'force';
      const proc = state.procs.find((p) => p.pid === r.pid);
      force.addEventListener('click', () =>
        optimize([{ pid: r.pid, create_time: proc ? proc.create_time : null }], true),
      );
      div.append(force);
    } else {
      const right = document.createElement('span');
      right.textContent = r.status === 'closed' ? `-${mb(r.freed_bytes || 0)}` : '';
      div.append(right);
    }
    box.append(div);
  }

  for (const r of data.rejected || []) {
    const div = document.createElement('div');
    div.className = 'result rejected';
    div.textContent = `${r.pid}: ${r.reason}`;
    box.append(div);
  }

  if (freed > 0) {
    const total = document.createElement('div');
    total.className = 'result closed';
    total.textContent = `Reclaimed ${mb(freed)}`;
    box.prepend(total);
  }
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

$('optimize').addEventListener('click', () => optimize(selectedTargets(), false));
poll();
setInterval(poll, POLL_MS);
