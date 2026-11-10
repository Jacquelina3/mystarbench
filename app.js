'use strict';

/*
 * 涨星榜 —— 前端展示逻辑
 *
 * 页面只读两份数据文件：data/latest.json（今日）、data/weekly.json（近 7 天）。
 * 数字来自 GitHub 官方 Trending 页面标注的 stars today / stars this week，
 * 按数值从高到低重排后取前 10。页面不再自行调用 GitHub API 计算，避免两套口径打架。
 */

const TOP_N = 10;
const LINE_TOP_N = 5;
const DAILY_KEY = 'ghd.daily.v3';
const WEEKLY_KEY = 'ghd.weekly.v3';
const HISTORY_KEY = 'ghd.history.v3';
const VIEW_KEY = 'ghd.view.v1';
const SORT_KEY = 'ghd.sort.v1';
const LINE_COLORS = ['#e5484d', '#3b82f6', '#10b981', '#f59e0b', '#a855f7'];

const LANG_COLOR = {
  TypeScript: '#3178c6', JavaScript: '#f1e05a', Python: '#3572a5',
  Rust: '#dea584', Go: '#00add8', 'C++': '#f34b7d', C: '#555555',
  Java: '#b07219', Ruby: '#701516', Swift: '#f05138', Kotlin: '#a97bff',
  Shell: '#89e051', HTML: '#e34c26', CSS: '#563d7c', Vue: '#41b883',
  PHP: '#4f5d95', Dart: '#00b4ab', Lua: '#000080', Zig: '#ec915c',
  Jupyter: '#da5b0b', Markdown: '#083fa1'
};

const $ = (id) => document.getElementById(id);
let boards = { daily: null, weekly: null, history: { days: {} } };
let view = 'daily';
let sortBy = 'gain';
let dailySortBy = 'gain';   // 今日卡的排序依据：gain = 今日新增，pct = 今日涨幅 %

// ---------------------------------------------------------------- 工具
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}
const num = (n) => Number(n || 0).toLocaleString('zh-CN');

function readJSON(key) {
  try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; }
}
function writeJSON(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* 隐私模式忽略 */ }
}
function fmtTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function setStatus(kind, text) {
  const dot = $('statusDot');
  dot.className = 'status-dot' + (kind === 'ok' ? ' ok' : kind === 'busy' ? ' busy' : kind === 'warn' ? ' warn' : '');
  $('statusText').textContent = text;
}
function progress(pct) {
  $('progress').classList.add('on');
  $('progressBar').style.width = Math.max(0, Math.min(100, pct)) + '%';
}

// ---------------------------------------------------------------- 数据
async function loadBoard(kind) {
  const inline = kind === 'daily' ? window.__INITIAL_BOARD__ : window.__WEEKLY_BOARD__;
  if (inline) return inline; // 单文件离线版：直接用内嵌快照
  const path = kind === 'daily' ? 'data/latest.json' : 'data/weekly.json';
  const key = kind === 'daily' ? DAILY_KEY : WEEKLY_KEY;
  let data = null;
  try {
    const resp = await fetch(path, { cache: 'no-store' });
    if (resp.ok) {
      const parsed = await resp.json();
      if (parsed && parsed.items && parsed.items.length) {
        data = parsed;
        writeJSON(key, data);
      }
    }
  } catch (e) { /* 单文件版或离线时走下面的兜底 */ }
  if (!data) data = readJSON(key);
  if (!data) data = inline || null;
  return data;
}

async function loadHistory() {
  if (window.__HISTORY__) return window.__HISTORY__;
  try {
    const resp = await fetch('data/history.json', { cache: 'no-store' });
    if (resp.ok) {
      const parsed = await resp.json();
      if (parsed && parsed.days) {
        writeJSON(HISTORY_KEY, parsed);
        return parsed;
      }
    }
  } catch (e) { /* 单文件版或离线 */ }
  return readJSON(HISTORY_KEY) || { days: {} };
}

function dateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function todayKey() {
  return dateKey(new Date());
}

// 相对增幅：近 7 天新增 ÷ 7 天前的星数
function growthPct(it) {
  const before = Math.max(1, Number(it.stars) - Number(it.gain));
  return (Number(it.gain) / before) * 100;
}

// 每日涨幅：今日新增 ÷ 昨日星数。取不到昨日星数就返回 null，页面照实说明，不估算。
function dailyPct(it) {
  if (it.growth_prev_pct != null && isFinite(it.growth_prev_pct)) return Number(it.growth_prev_pct);
  const prev = Number(it.stars_prev);
  if (prev > 0) return (Number(it.gain) / prev) * 100;
  return null;
}
// 今日排序用的数值：按涨幅排时，没有昨日数据的条目排到最后，不假装它是 0%
function dailyMetric(it) {
  if (dailySortBy !== 'pct') return Number(it.gain) || 0;
  const p = dailyPct(it);
  return p == null ? -Infinity : p;
}

function sortItems(arr) {
  if (view === 'weekly') {
    return arr.sort((a, b) => sortBy === 'pct'
      ? (growthPct(b) - growthPct(a) || b.gain - a.gain)
      : (b.gain - a.gain || b.stars - a.stars));
  }
  return arr.sort((a, b) => {
    const d = dailyMetric(b) - dailyMetric(a);
    return d || (b.gain - a.gain) || (b.stars - a.stars);
  });
}

function currentItems() {
  const board = boards[view];
  if (!board || !board.items) return [];
  return sortItems(board.items.slice()).slice(0, TOP_N).map((x, i) => Object.assign({}, x, { rank: i + 1 }));
}

// ---------------------------------------------------------------- 渲染
// 「每日涨幅」小块：今日新增、今日涨幅、昨日星数，三行对齐，缺数据如实标注
function dailyGainBarsHTML(it) {
  const pct = dailyPct(it);
  const prev = it.stars_prev;
  let barW;
  if (dailySortBy === 'pct') {
    const cap = Math.max(1, ...boards.daily.items.map((x) => Math.max(0, dailyPct(x) || 0)));
    barW = pct == null ? 0 : Math.min(100, (Math.max(0, pct) / cap) * 100);
  } else {
    const cap = Math.max(1, ...boards.daily.items.map((x) => Number(x.gain) || 0));
    barW = Math.min(100, ((Number(it.gain) || 0) / cap) * 100);
  }
  const pctText = pct == null ? '—' : `+${pct.toFixed(1)}%`;
  const prevText = prev == null ? '无昨日数据' : `${num(prev)} 星 → ${num(it.stars)} 星`;
  return `<div class="dgain">
    <div class="dg-row"><span class="dg-k">今日涨幅</span><span class="dg-v${pct == null ? ' na' : ''}">${pctText}</span></div>
    <div class="dg-track"><div class="dg-bar" style="width:${barW.toFixed(1)}%"></div></div>
    <div class="dg-row"><span class="dg-k">昨日星数</span><span class="dg-s">${prevText}</span></div>
  </div>`;
}

function cardHTML(it, isWeekly) {
  const podium = it.rank <= 3 ? ' podium' : '';
  const langColor = LANG_COLOR[it.language] || 'var(--ink-3)';
  const byPct = isWeekly && sortBy === 'pct';
  // 右侧大数字始终等于「当前排序依据」，另一个指标降为参考，避免看起来排序颠倒
  let mainValue, mainLabel, secondary;
  if (isWeekly) {
    mainValue = byPct ? '+' + growthPct(it).toFixed(1) + '%' : '+' + num(it.gain);
    mainLabel = byPct ? '7 天增幅' : '7 天涨星';
    secondary = byPct
      ? `<span class="total">+${num(it.gain)} 新增</span>`
      : `<span class="total">增幅 +${growthPct(it).toFixed(1)}%</span>`;
  } else {
    const pct = dailyPct(it);
    mainValue = dailySortBy === 'pct'
      ? (pct == null ? '—' : '+' + pct.toFixed(1) + '%')
      : '+' + num(it.gain);
    mainLabel = dailySortBy === 'pct' ? '今日涨幅' : '今日涨星';
    secondary = dailySortBy === 'pct'
      ? `<span class="total">+${num(it.gain)} 新增</span>`
      : (pct == null ? '' : `<span class="total">涨幅 +${pct.toFixed(1)}%</span>`);
  }
  return `<a class="card${podium}" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">
    <div class="rank">${String(it.rank).padStart(2, '0')}</div>
    <div class="body">
      <div class="title"><span class="owner">${esc(it.owner)}/</span>${esc(it.name)}</div>
      ${it.description ? `<p class="desc">${esc(it.description)}</p>` : ''}
      <div class="tags">
        ${it.language ? `<span class="lang"><i class="swatch" style="background:${langColor}"></i>${esc(it.language)}</span>` : ''}
        <span class="total">★ ${num(it.stars)} 总星</span>
        ${secondary}
      </div>
      ${isWeekly ? '' : dailyGainBarsHTML(it)}
    </div>
    <div class="gain">
      <div class="gain-num">${mainValue}</div>
      <div class="gain-label">${mainLabel}</div>
    </div>
    <span class="arrow">↗</span>
  </a>`;
}

function renderChart(items) {
  const metric = (it) => sortBy === 'pct' ? growthPct(it) : it.gain;
  const max = Math.max(1, ...items.map(metric));
  $('chartSub').textContent = sortBy === 'pct'
    ? 'Top 10 · 按 7 天增幅 % 从高到低'
    : 'Top 10 · 按 7 天新增星数从高到低';
  $('chart').innerHTML = items.map((it, i) => {
    const value = metric(it);
    const width = Math.max(2, (value / max) * 100);
    const label = sortBy === 'pct' ? '+' + value.toFixed(1) + '%' : '+' + num(it.gain);
    return `<div class="chart-row">
      <span class="chart-rank">${String(i + 1).padStart(2, '0')}</span>
      <span class="chart-name" title="${esc(it.repo)}">${esc(it.name)}</span>
      <div class="chart-track"><div class="chart-bar" style="width:${width.toFixed(1)}%"></div></div>
      <span class="chart-val">${label}</span>
    </div>`;
  }).join('');
  $('chartNote').textContent = sortBy === 'pct'
    ? '增幅 = 近 7 天新增星数 ÷ 7 天前的星数（当前总星数 − 新增）。小体量项目更容易出现高增幅，适合找黑马。'
    : '条形长度表示近 7 天新增星数。点「按增幅 %」可切换为相对增长比例。';
  renderSortSwitch();
}

function renderLineChart(items, board) {
  const top = items.slice(0, LINE_TOP_N);
  if (!top.length) return;
  const today = new Date(board.date + 'T12:00:00');
  if (isNaN(today.getTime())) return;

  const L = 56, R = 656, T = 24, B = 244, H = 300;
  const dates = [];
  for (let i = 7; i >= 0; i--) dates.push(new Date(today.getTime() - i * 86400000));
  const x = (i) => L + i * (R - L) / 7;
  const maxVal = Math.max(1, ...top.map((it) => it.stars));
  const yMax = Math.max(100000, Math.ceil(maxVal / 100000) * 100000);
  const y = (v) => B - (Math.max(0, v) / yMax) * (B - T);

  let grid = '';
  for (let g = 0; g <= 4; g++) {
    const v = yMax * g / 4;
    grid += `<line x1="${L}" y1="${y(v).toFixed(1)}" x2="${R}" y2="${y(v).toFixed(1)}" stroke="var(--line)" stroke-width="1"/>`;
    grid += `<text x="${L - 10}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--ink-3)">${Math.round(v / 1000)}k</text>`;
  }
  grid += `<line x1="${L}" y1="${B}" x2="${R}" y2="${B}" stroke="var(--line)" stroke-width="1"/>`;
  dates.forEach((d, i) => {
    const label = `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    grid += `<text x="${x(i).toFixed(1)}" y="${B + 22}" text-anchor="middle" font-size="11" fill="var(--ink-3)">${label}</text>`;
  });

  const hist = (boards.history && boards.history.days) || {};
  const measuredDays = Object.keys(hist).length;
  let lines = '';
  let legend = '';
  top.forEach((it, idx) => {
    const color = LINE_COLORS[idx];
    const start = Math.max(0, it.stars - it.gain);
    const x0 = x(0).toFixed(1), x1 = x(7).toFixed(1);
    const y0 = y(start).toFixed(1), y1 = y(it.stars).toFixed(1);
    // 两个真实端点之间的虚线：明确标注为示意，不是逐日实测
    lines += `<path d="M${x0} ${y0} L${x1} ${y1}" fill="none" stroke="${color}" stroke-width="2" stroke-dasharray="5 5" opacity="0.7"/>`;
    lines += `<circle cx="${x0}" cy="${y0}" r="4" fill="var(--surface)" stroke="${color}" stroke-width="2"/>`;
    lines += `<circle cx="${x1}" cy="${y1}" r="4.5" fill="${color}"/>`;

    // 逐日实测快照：攒够 2 天以上就用实线画真实走势
    const pts = [];
    dates.forEach((d, i) => {
      const rec = hist[dateKey(d)];
      if (rec && rec[it.repo] != null) pts.push([i, rec[it.repo]]);
    });
    if (pts.length >= 2) {
      const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}`).join(' ');
      lines += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>`;
      pts.forEach((p) => {
        lines += `<circle cx="${x(p[0]).toFixed(1)}" cy="${y(p[1]).toFixed(1)}" r="3.5" fill="${color}"/>`;
      });
    }
    legend += `<span class="legend-item"><i class="legend-dot" style="background:${color}"></i>${esc(it.name)}<span class="legend-val">${(it.stars / 1000).toFixed(1)}k</span></span>`;
  });

  $('lineChart').innerHTML = `<svg class="line-svg" viewBox="0 0 680 ${H}" role="img" aria-label="近 7 天前 ${top.length} 名工具的总星数走势折线图">${grid}${lines}</svg>`;
  $('lineLegend').innerHTML = legend;
  $('lineSub').textContent = `前 ${top.length} 名（${sortBy === 'pct' ? '按增幅 %' : '按新增星数'}）· 纵轴为总星数（k）`;
  $('lineNote').textContent = measuredDays >= 8
    ? '实线为逐日实测快照，已攒满完整 7 天区间。'
    : `目前只有两个真实端点：7 天前的数值由「当前总星 − 本周新增」严格反推，今天为页面实测值；中间虚线仅为示意，不代表逐日真实走势。已记录 ${measuredDays} 天实测快照，之后每天自动累积，攒满后虚线会变成真实逐日折线。`;
}

function renderSortSwitch() {
  document.querySelectorAll('#sortSwitch button').forEach((b) => {
    b.classList.toggle('on', b.dataset.sort === sortBy);
    b.setAttribute('aria-pressed', b.dataset.sort === sortBy ? 'true' : 'false');
  });
  document.querySelectorAll('#dailySortSwitch button').forEach((b) => {
    b.classList.toggle('on', b.dataset.sort === dailySortBy);
    b.setAttribute('aria-pressed', b.dataset.sort === dailySortBy ? 'true' : 'false');
  });
}

// 只重绘「榜单 + 图表 + 排序提示 + 页脚」。页头与状态栏由 renderShell 单独负责，
// 这样切换标签时不会把状态栏的「已载入今日榜单」冲掉。
function render() {
  const board = boards[view];
  const items = currentItems();
  const isWeekly = view === 'weekly';

  document.querySelectorAll('#tabs .tab').forEach((b) => {
    b.classList.toggle('active', b.dataset.view === view);
    b.setAttribute('aria-selected', b.dataset.view === view ? 'true' : 'false');
  });
  $('scopeLabel').textContent = isWeekly ? '近 7 天 Top 10' : '今日新增星数 Top 10';

  let hint = '';
  if (isWeekly) {
    hint = sortBy === 'pct'
      ? '当前排序：<b>按 7 天增幅 %</b> 从高到低 · 右侧大数字即排序依据'
      : '当前排序：<b>按 7 天新增星数</b> 从高到低 · 右侧大数字即排序依据';
  } else {
    hint = dailySortBy === 'pct'
      ? '当前排序：<b>按今日涨幅 %</b> 从高到低 · 没有昨日数据的排在最后'
      : '当前排序：<b>按今日新增星数</b> 从高到低';
  }
  $('sortHint').innerHTML = hint;

  $('chartPanel').hidden = !isWeekly;
  $('linePanel').hidden = !isWeekly;
  if (isWeekly && items.length) {
    renderChart(items);
    if (board) renderLineChart(items, board);
  }
  renderSortSwitch();

  // 今日视图里给卡片加「每日涨幅」说明；近 7 天视图不需要
  const noteEl = $('dailyNote');
  if (noteEl) noteEl.hidden = isWeekly;

  $('list').innerHTML = items.length
    ? items.map((it) => cardHTML(it, isWeekly)).join('')
    : '<div class="empty">这个榜单暂时没有数据。<br>点右上角「刷新」重试。</div>';

  const weekday = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  if (!board) {
    $('dateLabel').textContent = '—';
    $('footNote').textContent = '数据文件未找到。';
    $('updatedAt').textContent = '';
    return;
  }
  const d = new Date(board.date + 'T12:00:00');
  $('dateLabel').textContent = isNaN(d.getTime()) ? board.date : `${board.date} ${weekday[d.getDay()]}`;

  $('footNote').textContent = board.source
    ? '数据来源：' + board.source
    : '数据来源：GitHub 官方 Trending 页面。';
  const bits = [`榜单生成于 ${fmtTime(board.generated_at)}`];
  if (board.candidate_count) bits.push(`候选 ${board.candidate_count} 个`);
  if (board.stale) bits.push('缓存数据');
  // 「昨日星数」的取数口径只属于今日榜，近 7 天视图不显示
  if (!isWeekly && board.prev_note) bits.push(board.prev_note);
  $('updatedAt').textContent = bits.join(' · ');
}

// 页头日期 + 状态栏：只在载入/刷新数据后调用一次
function renderShell() {
  const board = boards[view];
  if (!board) {
    setStatus('warn', '数据未就绪 · 点「刷新」重试');
    return;
  }
  // 数据由云端每天自动更新，写清是哪一刻那份，不让人误以为是实时流
  const stamp = fmtTime(board.generated_at);
  const today = todayKey();
  if (board.date !== today) {
    const behind = daysBetween(board.date, today);
    setStatus('warn', `显示的是 ${board.date} 的榜单${behind > 0 ? `（落后 ${behind} 天）` : ''} · 今日数据尚未更新`);
  } else {
    setStatus('ok', `今日（${board.date}）榜单 · 云端 ${stamp} 更新`);
  }
}

function daysBetween(fromISO, toISO) {
  const a = new Date(fromISO + 'T00:00:00');
  const b = new Date(toISO + 'T00:00:00');
  if (isNaN(a.getTime()) || isNaN(b.getTime())) return 0;
  return Math.round((b - a) / 86400000);
}

// ---------------------------------------------------------------- 刷新
function savePrefs() {
  writeJSON(VIEW_KEY, view);
  writeJSON(SORT_KEY, { weekly: sortBy, daily: dailySortBy });
}

// 数据由 GitHub Actions 每天 09:00 自动更新到云端。
// 点「刷新」= 重新读取云端最新那份数据文件（绕开缓存），不依赖本地电脑。
async function refresh() {
  const btn = $('refreshBtn');
  btn.disabled = true;
  setStatus('busy', '正在读取云端最新榜单…');
  progress(35);
  const res = await Promise.all([loadBoard('daily'), loadBoard('weekly'), loadHistory()]);
  boards.daily = res[0];
  boards.weekly = res[1];
  boards.history = res[2];
  render();
  renderShell();
  progress(100);
  setTimeout(() => {
    $('progress').classList.remove('on');
    $('progressBar').style.width = '0';
  }, 800);
  btn.disabled = false;
}

// ---------------------------------------------------------------- 启动
(async function init() {
  // 先绑事件：哪怕数据加载失败，标签也必须能点，不能整页卡死
  const tabBox = $('tabs');
  if (tabBox && tabBox.addEventListener) {
    tabBox.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('.tab') : null;
      if (!btn) return;
      view = btn.dataset.view;
      savePrefs();
      render();
      renderShell();
    });
  }
  const sortBox = $('sortSwitch');
  if (sortBox && sortBox.addEventListener) {
    sortBox.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!btn || !btn.dataset.sort) return;
      sortBy = btn.dataset.sort;
      savePrefs();
      render();
    });
  }
  const refreshBtn = $('refreshBtn');
  if (refreshBtn && refreshBtn.addEventListener) refreshBtn.addEventListener('click', refresh);

  const dailySortBox = $('dailySortSwitch');
  if (dailySortBox && dailySortBox.addEventListener) {
    dailySortBox.addEventListener('click', (e) => {
      const btn = e.target && e.target.closest ? e.target.closest('button') : null;
      if (!btn || !btn.dataset.sort) return;
      dailySortBy = btn.dataset.sort;
      savePrefs();
      render();
    });
  }

  const savedSort = readJSON(SORT_KEY) || {};
  if (savedSort.weekly) sortBy = savedSort.weekly;
  if (savedSort.daily) dailySortBy = savedSort.daily;
  const savedView = readJSON(VIEW_KEY);
  if (savedView === 'weekly' || savedView === 'daily') view = savedView;

  try {
    if ('serviceWorker' in navigator && location.protocol !== 'file:') {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    }
  } catch (e) { /* 老浏览器或 file:// 下没有 SW，忽略 */ }

  try {
    const res = await Promise.all([loadBoard('daily'), loadBoard('weekly'), loadHistory()]);
    boards.daily = res[0];
    boards.weekly = res[1];
    boards.history = res[2];
    render();
    renderShell();
  } catch (err) {
    $('list').innerHTML = `<div class="empty">页面渲染出错：${esc(err && err.message ? err.message : err)}<br>点右上角「刷新」重试。</div>`;
    setStatus('warn', '渲染出错，数据可能不完整');
  }
})();
