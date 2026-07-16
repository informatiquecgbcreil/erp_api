
  (function() {
    const els = document.querySelectorAll('.kpi[data-count]');
    els.forEach(el => {
      const target = Number(el.getAttribute('data-count') || '0');
      if (!isFinite(target) || target <= 0) return;
      const isMoney = (el.textContent || '').includes('€');
      const duration = 650;
      const start = performance.now();
      function tick(t) {
        const p = Math.min((t - start) / duration, 1);
        const val = Math.floor(target * (0.2 + 0.8 * p));
        el.textContent = isMoney ? val.toLocaleString('fr-FR') + '€' : val.toLocaleString('fr-FR');
        if (p < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  })();

  const charts = {};
  const chartStates = new Map();

  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function palette() {
    return [
      cssVar('--accent', '#4f46e5'),
      cssVar('--ok', '#16a34a'),
      cssVar('--warn', '#d97706'),
      cssVar('--danger', '#dc2626'),
      '#0ea5e9', '#8b5cf6', '#ec4899', '#14b8a6', '#64748b', '#22c55e'
    ];
  }

  function hexToRgba(color, alpha) {
    if (!color) return `rgba(79,70,229,${alpha})`;
    if (color.startsWith('#')) {
      let hex = color.slice(1);
      if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
      if (hex.length === 6) {
        const num = parseInt(hex, 16);
        const r = (num >> 16) & 255;
        const g = (num >> 8) & 255;
        const b = num & 255;
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
      }
    }
    return color;
  }

  function getBaseCanvasHeight(canvas) {
    if (!canvas) return 190;
    const raw = parseInt(canvas.dataset.baseHeight || canvas.getAttribute('height') || '', 10);
    const base = Number.isFinite(raw) && raw > 0 ? raw : 190;
    canvas.dataset.baseHeight = String(base);
    return base;
  }

  function setupCanvas(canvas) {
    if (!canvas) return null;
    const ratio = Math.max(1, window.devicePixelRatio || 1);
    const rect = canvas.getBoundingClientRect();
    const shell = canvas.closest('.chart-shell');
    const attrWidth = parseInt(canvas.getAttribute('width') || '', 10);
    const width = Math.max(
      280,
      Math.floor((shell && shell.clientWidth) || rect.width || (Number.isFinite(attrWidth) ? attrWidth : 0) || 320)
    );
    const height = Math.max(180, getBaseCanvasHeight(canvas));
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = '100%';
    canvas.style.maxWidth = '100%';
    canvas.style.height = height + 'px';
    canvas.style.display = 'block';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function formatValue(v, money=true) {
    const n = Number(v || 0);
    return money
      ? n.toLocaleString('fr-FR', { minimumFractionDigits: 0, maximumFractionDigits: 2 }) + '€'
      : n.toLocaleString('fr-FR');
  }

  function getUi(canvas) {
    const card = canvas.closest('.chart-card') || canvas.parentElement;
    const shell = card.querySelector('.chart-shell') || card;
    const meta = card.querySelector(`[data-chart-meta="${canvas.id}"]`);
    return { card, shell, meta };
  }

  function setTooltip() {
    return;
  }

  function clearTooltip() {
    return;
  }

  function clearAllTooltips() {
    return;
  }

  function setMeta(canvas, items, money=true) {
    const { meta } = getUi(canvas);
    if (!meta) return;
    meta.innerHTML = '';
    items.forEach(item => {
      const el = document.createElement('span');
      el.className = 'chart-meta__item';
      const valueText = item.rawText || (money ? formatValue(item.value, true) : formatValue(item.value, false));
      el.innerHTML = `<strong>${item.label}</strong><span>${valueText}</span>`;
      meta.appendChild(el);
    });
  }

  function drawLegendItems(canvas, items, money=true, handlers={}, options={}) {
    const { card } = getUi(canvas);
    if (!card) return;
    let legend = card.querySelector('.chart-legend');
    if (options.show === false) {
      if (legend) legend.remove();
      return;
    }
    if (!legend) {
      legend = document.createElement('div');
      legend.className = 'chart-legend';
      card.appendChild(legend);
    }
    legend.innerHTML = '';
    items.forEach((item, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chart-legend__item';
      button.innerHTML = `<i style="background:${item.color}"></i><span>${item.label}</span><strong>${item.rawText || (money ? formatValue(item.value, true) : formatValue(item.value, false))}</strong>`;
      if (typeof handlers.onEnter === 'function') button.addEventListener('mouseenter', () => { clearTooltip(canvas); handlers.onEnter(index, item); });
      if (typeof handlers.onLeave === 'function') button.addEventListener('mouseleave', () => { clearTooltip(canvas); handlers.onLeave(index, item); });
      if (typeof handlers.onClick === 'function') button.addEventListener('click', () => { clearTooltip(canvas); handlers.onClick(index, item); });
      legend.appendChild(button);
    });
  }

  function hitArc(cx, cy, r0, r1, start, end, x, y) {
    const dx = x - cx, dy = y - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < r0 || dist > r1) return false;
    let ang = Math.atan2(dy, dx);
    if (ang < -Math.PI / 2) ang += Math.PI * 2;
    let s = start, e = end;
    while (e < s) e += Math.PI * 2;
    while (ang < s) ang += Math.PI * 2;
    return ang >= s && ang <= e;
  }

  function makeDoughnut(id, cfg) {
    const canvas = document.getElementById(id);
    if (!canvas || !cfg || !cfg.values || !cfg.values.length) return;
    const area = setupCanvas(canvas); if (!area) return;
    const { ctx, width, height } = area;
    const state = chartStates.get(id) || {};
    const money = cfg.money !== false;
    const cx = width / 2, cy = height / 2;
    const r = Math.min(width, height) * 0.31;
    const inner = r * 0.58;
    const totalRaw = cfg.values.reduce((a, b) => a + Math.max(0, Number(b || 0)), 0);
    const total = totalRaw || 1;
    const colors = palette();
    let angle = -Math.PI / 2;
    const hitboxes = [];
    cfg.values.forEach((raw, i) => {
      const value = Math.max(0, Number(raw || 0));
      const slice = (value / total) * Math.PI * 2;
      const hovered = state.hovered === i;
      ctx.beginPath();
      ctx.arc(cx, cy, r, angle, angle + slice);
      ctx.arc(cx, cy, inner, angle + slice, angle, true);
      ctx.closePath();
      ctx.fillStyle = colors[i % colors.length];
      ctx.globalAlpha = hovered ? 1 : 0.9;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (hovered) {
        ctx.lineWidth = 3;
        ctx.strokeStyle = 'rgba(255,255,255,.92)';
        ctx.stroke();
      }
      hitboxes.push({ index:i, x:cx, y:cy, r0:inner, r1:r, start:angle, end:angle+slice, label:cfg.labels[i], value });
      angle += slice;
    });

    ctx.fillStyle = cssVar('--text', '#111827');
    ctx.font = '700 18px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(formatValue(totalRaw, money), cx, cy + 4);
    ctx.font = '12px system-ui';
    ctx.fillStyle = cssVar('--muted', '#64748b');
    ctx.fillText(money ? 'total' : 'participants', cx, cy + 22);

    chartStates.set(id, { ...state, type:'doughnut', hitboxes, redraw:() => makeDoughnut(id, cfg), money });
    drawLegendItems(
      canvas,
      cfg.labels.map((label, i) => ({ label, color: colors[i % colors.length], value: cfg.values[i] })),
      money,
      {
        onEnter: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); },
        onLeave: () => { const s = chartStates.get(id) || {}; s.hovered = null; chartStates.set(id, s); redraw(id); clearTooltip(canvas); },
        onClick: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); }
      },
      { show: cfg.showLegend !== false }
    );
    const values = cfg.values.map((v, i) => ({ label: cfg.labels[i], value: v })).sort((a, b) => Number(b.value) - Number(a.value)).slice(0, 4);
    setMeta(canvas, values, money);
  }

  function makeBar(id, cfg) {
    const canvas = document.getElementById(id);
    if (!canvas || !cfg || !cfg.values || !cfg.values.length) return;
    const area = setupCanvas(canvas); if (!area) return;
    const { ctx, width, height } = area;
    const state = chartStates.get(id) || {};
    const money = cfg.money !== false;
    const pad = { top: 16, right: 10, bottom: 34, left: 36 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const max = Math.max(...cfg.values.map(v => Number(v || 0)), 1);
    const colors = palette();
    const n = cfg.values.length;
    const gap = 12;
    const barW = Math.max(20, (chartW - gap * (n - 1)) / n);
    const hitboxes = [];

    for (let g = 0; g < 4; g++) {
      const y = pad.top + (chartH / 4) * g;
      ctx.strokeStyle = 'rgba(148,163,184,.18)';
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + chartW, y); ctx.stroke();
    }
    ctx.strokeStyle = 'rgba(148,163,184,.25)';
    ctx.beginPath(); ctx.moveTo(pad.left, pad.top + chartH); ctx.lineTo(pad.left + chartW, pad.top + chartH); ctx.stroke();

    cfg.values.forEach((raw, i) => {
      const v = Number(raw || 0);
      const h = Math.max(2, (v / max) * (chartH - 8));
      const x = pad.left + i * (barW + gap);
      const y = pad.top + chartH - h;
      const hovered = state.hovered === i;
      ctx.fillStyle = colors[0];
      ctx.globalAlpha = hovered ? 1 : 0.82;
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') ctx.roundRect(x, y, barW, h, 10);
      else ctx.rect(x, y, barW, h);
      ctx.fill();
      ctx.globalAlpha = 1;
      if (hovered) {
        ctx.fillStyle = 'rgba(79,70,229,.10)';
        ctx.fillRect(x - 4, pad.top, barW + 8, chartH);
        ctx.fillStyle = cssVar('--text', '#111827');
        ctx.font = '700 11px system-ui';
        ctx.textAlign = 'center';
        ctx.fillText(formatValue(v, money), x + barW / 2, Math.max(14, y - 6));
      }
      ctx.fillStyle = cssVar('--muted', '#64748b');
      ctx.font = '11px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText((cfg.labels[i] || '').toString().slice(0, 8), x + barW / 2, height - 10);
      hitboxes.push({ index:i, x, y, w:barW, h, label:cfg.labels[i], value:v });
    });

    chartStates.set(id, { ...state, type:'bar', hitboxes, redraw:() => makeBar(id, cfg), money });
    drawLegendItems(
      canvas,
      cfg.labels.map((label, i) => ({ label, color: colors[0], value: cfg.values[i] })),
      money,
      {
        onEnter: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); },
        onLeave: () => { const s = chartStates.get(id) || {}; s.hovered = null; chartStates.set(id, s); redraw(id); clearTooltip(canvas); },
        onClick: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); }
      }
    );
    const values = cfg.values.map((v, i) => ({ label: cfg.labels[i], value: v })).sort((a, b) => Number(b.value) - Number(a.value)).slice(0, 3);
    setMeta(canvas, values, money);
  }

  function makeLine(id, cfg) {
    const canvas = document.getElementById(id);
    if (!canvas || !cfg || !cfg.values || !cfg.values.length) return;
    const area = setupCanvas(canvas); if (!area) return;
    const { ctx, width, height } = area;
    const state = chartStates.get(id) || {};
    const pad = { top: 16, right: 12, bottom: 34, left: 36 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const max = Math.max(...cfg.values.map(v => Number(v || 0)), 1);
    const pts = cfg.values.map((raw, i) => {
      const v = Number(raw || 0);
      return { x: pad.left + (chartW * i / Math.max(1, cfg.values.length - 1)), y: pad.top + chartH - (v / max) * (chartH - 8), v, label: cfg.labels[i] };
    });

    for (let g = 0; g < 4; g++) {
      const y = pad.top + (chartH / 4) * g;
      ctx.strokeStyle = 'rgba(148,163,184,.18)';
      ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + chartW, y); ctx.stroke();
    }
    const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + chartH);
    grad.addColorStop(0, 'rgba(79,70,229,.28)');
    grad.addColorStop(1, 'rgba(79,70,229,0)');

    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.lineTo(pts[pts.length - 1].x, pad.top + chartH);
    ctx.lineTo(pts[0].x, pad.top + chartH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.strokeStyle = cssVar('--accent', '#4f46e5');
    ctx.lineWidth = 3;
    ctx.beginPath();
    pts.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y));
    ctx.stroke();

    pts.forEach((p, i) => {
      const hovered = state.hovered === i;
      ctx.fillStyle = cssVar('--accent', '#4f46e5');
      ctx.beginPath(); ctx.arc(p.x, p.y, 4, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = cssVar('--muted', '#64748b');
      ctx.font = '11px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText((cfg.labels[i] || '').toString().slice(0, 8), p.x, height - 10);
      if (hovered) {
        ctx.strokeStyle = 'rgba(79,70,229,.25)';
        ctx.beginPath(); ctx.moveTo(p.x, pad.top); ctx.lineTo(p.x, pad.top + chartH); ctx.stroke();
        ctx.fillStyle = cssVar('--text', '#111827');
        ctx.font = '700 11px system-ui';
        ctx.fillText(formatValue(p.v, false), p.x, Math.max(14, p.y - 10));
      }
    });

    chartStates.set(id, { ...state, type:'line', hitboxes:pts.map(p => ({ ...p, w: 12, h: 12, value: p.v })), redraw:() => makeLine(id, cfg), money:false });
    const values = cfg.values.map((v, i) => ({ label: cfg.labels[i], value: v })).sort((a, b) => Number(b.value) - Number(a.value)).slice(0, 3);
    setMeta(canvas, values, false);
  }

  function makeHorizontalBars(id, cfg) {
    const canvas = document.getElementById(id);
    if (!canvas || !cfg || !cfg.values || !cfg.values.length) return;
    const area = setupCanvas(canvas); if (!area) return;
    const { ctx, width, height } = area;
    const state = chartStates.get(id) || {};
    const money = false;
    const pad = { top: 14, right: 40, bottom: 12, left: 96 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const max = Math.max(...cfg.values.map(v => Number(v || 0)), 1);
    const gap = 10;
    const barH = Math.max(18, (chartH - gap * (cfg.values.length - 1)) / cfg.values.length);
    const baseColor = cssVar('--accent', '#4f46e5');
    const hitboxes = [];

    for (let g = 1; g <= 4; g++) {
      const x = pad.left + (chartW / 4) * g;
      ctx.strokeStyle = 'rgba(148,163,184,.18)';
      ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, pad.top + chartH); ctx.stroke();
    }

    cfg.values.forEach((raw, i) => {
      const v = Number(raw || 0);
      const y = pad.top + i * (barH + gap);
      const w = Math.max(v > 0 ? 6 : 0, (v / max) * chartW);
      const hovered = state.hovered === i;
      ctx.fillStyle = hovered ? baseColor : hexToRgba(baseColor, .82);
      ctx.beginPath();
      if (typeof ctx.roundRect === 'function') ctx.roundRect(pad.left, y, w, barH, 10);
      else ctx.rect(pad.left, y, w, barH);
      ctx.fill();

      ctx.fillStyle = cssVar('--text', '#111827');
      ctx.font = '12px system-ui';
      ctx.textAlign = 'right';
      ctx.fillText(cfg.labels[i] || '', pad.left - 8, y + barH * 0.68);
      ctx.textAlign = 'left';
      ctx.fillText(formatValue(v, false), pad.left + w + 8, y + barH * 0.68);

      hitboxes.push({ index:i, x:pad.left, y, w, h:barH, label:cfg.labels[i], value:v });
    });

    chartStates.set(id, { ...state, type:'hbar', hitboxes, redraw:() => makeHorizontalBars(id, cfg), money });
    drawLegendItems(
      canvas,
      cfg.labels.map((label, i) => ({ label, color: baseColor, value: cfg.values[i] })),
      false,
      {
        onEnter: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); },
        onLeave: () => { const s = chartStates.get(id) || {}; s.hovered = null; chartStates.set(id, s); redraw(id); clearTooltip(canvas); },
        onClick: (i) => { const s = chartStates.get(id) || {}; s.hovered = i; chartStates.set(id, s); redraw(id); }
      },
      { show: cfg.showLegend !== false }
    );
    const values = cfg.values.map((v, i) => ({ label: cfg.labels[i], value: v }));
    if (Number(cfg.unknown || 0) > 0) values.push({ label: 'Âge non renseigné', value: cfg.unknown });
    setMeta(canvas, values, false);
  }

  function makeNestedDoughnut(id, cfg) {
    const canvas = document.getElementById(id);
    if (!canvas || !cfg || !cfg.inner_values || !cfg.inner_values.length) return;
    const area = setupCanvas(canvas); if (!area) return;
    const { ctx, width, height } = area;
    const state = chartStates.get(id) || {};
    const colors = palette();
    const cx = width / 2, cy = height / 2;
    const outerR = Math.min(width, height) * 0.39;
    const midR = outerR * 0.72;
    const innerHole = outerR * 0.30;
    const innerTotalRaw = cfg.inner_values.reduce((a, b) => a + Math.max(0, Number(b || 0)), 0);
    const innerTotal = innerTotalRaw || 1;
    const selectedParent = Number.isInteger(state.selectedParent) ? state.selectedParent : null;
    const hitboxes = [];

    let innerAngle = -Math.PI / 2;
    cfg.inner_values.forEach((raw, i) => {
      const value = Math.max(0, Number(raw || 0));
      const slice = (value / innerTotal) * Math.PI * 2;
      const isDimmed = selectedParent !== null && selectedParent !== i;
      const isHovered = state.hovered === `inner-${i}`;
      ctx.beginPath();
      ctx.arc(cx, cy, midR - 6, innerAngle, innerAngle + slice);
      ctx.arc(cx, cy, innerHole, innerAngle + slice, innerAngle, true);
      ctx.closePath();
      ctx.fillStyle = colors[i % colors.length];
      ctx.globalAlpha = isDimmed ? 0.2 : (isHovered ? 1 : 0.88);
      ctx.fill();
      ctx.globalAlpha = 1;
      if (isHovered || selectedParent === i) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = 'rgba(255,255,255,.92)';
        ctx.stroke();
      }
      hitboxes.push({ index:`inner-${i}`, parentIndex:i, x:cx, y:cy, r0:innerHole, r1:midR - 6, start:innerAngle, end:innerAngle + slice, label:cfg.inner_labels[i], value, layer:'inner' });
      innerAngle += slice;
    });

    let outerAngle = -Math.PI / 2;
    cfg.outer_values.forEach((raw, i) => {
      const value = Math.max(0, Number(raw || 0));
      const slice = (value / innerTotal) * Math.PI * 2;
      const parentIndex = Number(cfg.outer_parents[i] || 0);
      const baseColor = colors[parentIndex % colors.length];
      const isDimmed = selectedParent !== null && selectedParent !== parentIndex;
      const isHovered = state.hovered === `outer-${i}`;
      ctx.beginPath();
      ctx.arc(cx, cy, outerR, outerAngle, outerAngle + slice);
      ctx.arc(cx, cy, midR + 6, outerAngle + slice, outerAngle, true);
      ctx.closePath();
      ctx.fillStyle = hexToRgba(baseColor, isDimmed ? 0.18 : 0.55);
      ctx.fill();
      if (isHovered || selectedParent === parentIndex) {
        ctx.lineWidth = 2;
        ctx.strokeStyle = baseColor;
        ctx.stroke();
      }
      const fullLabel = `${cfg.outer_city_labels[i]} · ${cfg.outer_labels[i]}`;
      hitboxes.push({ index:`outer-${i}`, parentIndex, x:cx, y:cy, r0:midR + 6, r1:outerR, start:outerAngle, end:outerAngle + slice, label:fullLabel, value, layer:'outer' });
      outerAngle += slice;
    });

    ctx.fillStyle = cssVar('--text', '#111827');
    ctx.font = '700 16px system-ui';
    ctx.textAlign = 'center';
    ctx.fillText(formatValue(innerTotalRaw, false), cx, cy + 2);
    ctx.font = '12px system-ui';
    ctx.fillStyle = cssVar('--muted', '#64748b');
    ctx.fillText('participants', cx, cy + 20);

    chartStates.set(id, {
      ...state,
      type:'nested-doughnut',
      hitboxes,
      redraw:() => makeNestedDoughnut(id, cfg),
      money:false,
      selectedParent,
    });

    drawLegendItems(
      canvas,
      cfg.inner_labels.map((label, i) => ({ label, color: colors[i % colors.length], value: cfg.inner_values[i] })),
      false,
      {
        onEnter: (i) => { const s = chartStates.get(id) || {}; s.hovered = `inner-${i}`; chartStates.set(id, s); redraw(id); },
        onLeave: () => { const s = chartStates.get(id) || {}; s.hovered = null; chartStates.set(id, s); redraw(id); clearTooltip(canvas); },
        onClick: (i) => {
          const s = chartStates.get(id) || {};
          s.selectedParent = s.selectedParent === i ? null : i;
          s.hovered = `inner-${i}`;
          chartStates.set(id, s);
          redraw(id);
        }
      },
      { show: cfg.showLegend !== false }
    );

    const topQuartiers = cfg.outer_values.map((v, i) => ({ label: `${cfg.outer_city_labels[i]} · ${cfg.outer_labels[i]}`, value: v })).sort((a, b) => Number(b.value) - Number(a.value)).slice(0, 4);
    setMeta(canvas, topQuartiers, false);
  }

  function redraw(id) {
    const state = chartStates.get(id);
    if (state && typeof state.redraw === 'function') state.redraw();
  }

  function registerPointer(id) {
    const canvas = document.getElementById(id);
    if (!canvas || canvas.dataset.pointerWired === '1') return;
    canvas.dataset.pointerWired = '1';
    canvas.addEventListener('mousemove', (e) => {
      const state = chartStates.get(id); if (!state) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      let hit = null;
      if (state.type === 'doughnut' || state.type === 'nested-doughnut') {
        hit = state.hitboxes.find(h => hitArc(h.x, h.y, h.r0, h.r1, h.start, h.end, x, y));
      } else {
        hit = state.hitboxes.find(h => x >= (h.x - 6) && x <= (h.x + (h.w || 12) + 6) && y >= (h.y || 0) && y <= ((h.y || 0) + (h.h || canvas.clientHeight)));
      }
      const hovered = hit ? hit.index : null;
      if (state.hovered !== hovered) {
        state.hovered = hovered;
        chartStates.set(id, state);
        redraw(id);
      }
    });
    canvas.addEventListener('mouseleave', () => {
      const state = chartStates.get(id);
      if (!state) return;
      state.hovered = null;
      chartStates.set(id, state);
      redraw(id);
    });
  }

  function bootDashboardCharts() {
    makeDoughnut('chartBudget', { ...(charts.budget || charts.budget_donut || {}), money: true });
    makeBar('chartDepenses', { ...(charts.depenses || charts.depenses_bar || {}), money: true });
    makeLine('chartSessions', charts.sessions || charts.sessions_line);
    makeDoughnut('chartPublic', { ...(charts.public || charts.public_pie || {}), money: false });
    makeDoughnut('chartGender', { ...(charts.gender || {}), showLegend: false });
    makeHorizontalBars('chartAges', { ...(charts.ages || {}), showLegend: false });
    makeNestedDoughnut('chartLocations', charts.locations || {});
  }

  function wirePointers() {
    ['chartBudget', 'chartDepenses', 'chartSessions', 'chartPublic', 'chartGender', 'chartAges', 'chartLocations'].forEach(registerPointer);
  }

  function initDashboardCharts() {
    bootDashboardCharts();
    wirePointers();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDashboardCharts, { once:true });
  } else {
    initDashboardCharts();
  }

  document.addEventListener('pointermove', (e) => {
    if (e.target.closest('.chart-shell')) return;
    clearAllTooltips();
  });

  window.addEventListener('scroll', clearAllTooltips, { passive:true });

  window.addEventListener('resize', () => {
    clearAllTooltips();
    clearTimeout(window.__dashResize);
    window.__dashResize = setTimeout(bootDashboardCharts, 120);
  });
