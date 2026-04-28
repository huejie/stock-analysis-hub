// ========== State ==========
let currentView = 'daily';
let parsedData = null;
let availableDates = [];
const chartInstances = new Map();

// ========== DOM ==========
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const datePicker = $('#datePicker');
const dateHistory = $('#dateHistory');
const uploadArea = $('#uploadArea');
const fileInput = $('#fileInput');
const uploadBtn = $('#uploadBtn');
const confirmModal = $('#confirmModal');
const editTableBody = $('#editTableBody');
const confirmBtn = $('#confirmBtn');
const cancelBtn = $('#cancelBtn');
const toast = $('#toast');

// ========== Chart Theme ==========
const CHART_COLORS = {
    text: '#94a3b8',
    textMuted: '#475569',
    line: '#1e293b',
    accent: '#3b82f6',
    red: '#ef4444',
    green: '#10b981',
    amber: '#f59e0b',
    purple: '#8b5cf6',
    cyan: '#06b6d4',
};

const CHART_BASE = {
    backgroundColor: 'transparent',
    grid: { left: 70, right: 24, top: 20, bottom: 30, containLabel: false },
    textStyle: { fontFamily: "'DM Sans', 'PingFang SC', sans-serif" },
};

function getChart(id) {
    if (chartInstances.has(id)) {
        chartInstances.get(id).dispose();
    }
    const el = document.getElementById(id);
    if (!el) return null;
    const chart = echarts.init(el, null, { renderer: 'canvas' });
    chartInstances.set(id, chart);
    return chart;
}

// ========== Init ==========
document.addEventListener('DOMContentLoaded', async () => {
    // Re-assign DOM refs in case they weren't ready at parse time
    const dp = document.getElementById('datePicker');
    const d = new Date();
    const today = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    dp.value = today;
    await loadDates();
    loadView();
});

// Debounced resize
let resizeTimer;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        chartInstances.forEach(c => c && !c.isDisposed() && c.resize());
    }, 150);
});

// ========== Toast ==========
function showToast(msg, type = 'success') {
    toast.textContent = msg;
    toast.style.borderColor = type === 'success' ? CHART_COLORS.green : CHART_COLORS.red;
    toast.style.color = type === 'success' ? CHART_COLORS.green : CHART_COLORS.red;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

// ========== Nav ==========
$$('.tabs .btn').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.tabs .btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentView = btn.dataset.view;

        if (currentView === 'daily') {
            $('#rangeGroup').style.display = 'none';
            loadView();
            return;
        }

        const rangeType = btn.dataset.range;
        const end = new Date();
        const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
        const endStr = fmt(end);

        if (rangeType === 'custom') {
            $('#rangeGroup').style.display = 'flex';
            return;
        }

        // Preset range: 7, 30, 90 days
        $('#rangeGroup').style.display = 'none';
        const days = parseInt(rangeType);
        const start = new Date(end);
        start.setDate(start.getDate() - days + 1);
        $('#rangeStart').value = fmt(start);
        $('#rangeEnd').value = endStr;
        loadRangeView();
    });
});

datePicker.addEventListener('change', loadView);

$('#prevDateBtn').addEventListener('click', () => {
    // 跳到上一个有数据的交易日
    const idx = availableDates.indexOf(datePicker.value);
    if (idx >= 0 && idx < availableDates.length - 1) {
        datePicker.value = availableDates[idx + 1]; // dates are DESC
        loadView();
    }
});
$('#nextDateBtn').addEventListener('click', () => {
    const idx = availableDates.indexOf(datePicker.value);
    if (idx > 0) {
        datePicker.value = availableDates[idx - 1]; // dates are DESC
        loadView();
    }
});
dateHistory.addEventListener('change', () => {
    if (dateHistory.value) {
        datePicker.value = dateHistory.value;
        loadView();
    }
});

uploadBtn.addEventListener('click', () => {
    const visible = uploadArea.style.display !== 'none';
    uploadArea.style.display = visible ? 'none' : 'block';
    if (!visible) uploadArea.scrollIntoView({ behavior: 'smooth' });
});

// ========== Upload ==========
uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', (e) => { e.preventDefault(); uploadArea.classList.add('dragover'); });
uploadArea.addEventListener('dragleave', () => uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener('change', () => { if (fileInput.files.length) handleFile(fileInput.files[0]); });

async function handleFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    uploadBtn.textContent = '识别中...';
    uploadBtn.disabled = true;

    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error(await resp.text());
        parsedData = await resp.json();
        showConfirmModal(parsedData);
    } catch (e) {
        showToast('识别失败: ' + e.message, 'error');
    } finally {
        uploadBtn.textContent = '上传图片';
        uploadBtn.disabled = false;
    }
}

// ========== Confirm Modal ==========
function showConfirmModal(data) {
    editTableBody.innerHTML = '';
    $('#modalCount').textContent = `识别到 ${data.records.length} 只股票`;

    // Set date in modal
    const modalDate = $('#modalDatePicker');
    modalDate.value = data.date;
    const hint = $('#dateDetectHint');
    hint.textContent = '从截图中识别' + (data.date === new Date().toISOString().slice(0, 10) ? '（默认今日）' : '');

    data.records.forEach((r, i) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${r.rank}</strong></td>
            <td><input value="${r.stock_name || ''}" data-field="stock_name"></td>
            <td><input value="${r.stock_code || ''}" data-field="stock_code" maxlength="6"></td>
            <td><input type="number" value="${r.heat_value || ''}" data-field="heat_value" step="0.01"></td>
            <td><input type="number" value="${r.price_change_pct ?? ''}" data-field="price_change_pct" step="0.1"></td>
            <td><input type="number" value="${r.turnover_amount || ''}" data-field="turnover_amount" step="0.1"></td>
            <td><input type="number" value="${r.holders_today || ''}" data-field="holders_today"></td>
            <td><input type="number" value="${r.holders_yesterday || ''}" data-field="holders_yesterday"></td>
        `;
        tr.querySelectorAll('input').forEach(inp => {
            inp.addEventListener('change', () => {
                const field = inp.dataset.field;
                parsedData.records[i][field] = inp.type === 'number' ? parseFloat(inp.value) : inp.value;
            });
        });
        editTableBody.appendChild(tr);
    });
    confirmModal.classList.add('show');
}

cancelBtn.addEventListener('click', () => { confirmModal.classList.remove('show'); parsedData = null; });

confirmBtn.addEventListener('click', async () => {
    // Use the date from modal picker
    parsedData.date = $('#modalDatePicker').value;
    try {
        const resp = await fetch('/api/records', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(parsedData),
        });
        if (!resp.ok) throw new Error(await resp.text());
        confirmModal.classList.remove('show');
        uploadArea.style.display = 'none';
        datePicker.value = parsedData.date;
        showToast(`保存成功，共 ${parsedData.records.length} 只股票`);
        await loadDates();
        loadView();
    } catch (e) {
        showToast('保存失败: ' + e.message, 'error');
    }
});

// ========== Data Loading ==========
async function loadDates() {
    const resp = await fetch('/api/dates');
    const data = await resp.json();
    availableDates = data.dates || [];

    dateHistory.innerHTML = '<option value="">历史记录</option>';
    availableDates.slice().reverse().forEach(d => {
        const opt = document.createElement('option');
        opt.value = d;
        opt.textContent = d;
        dateHistory.appendChild(opt);
    });

    if (availableDates.length > 0) {
        if (!datePicker.value || !availableDates.includes(datePicker.value)) {
            datePicker.value = availableDates[0]; // most recent
        }
    }
}

async function loadView() {
    // Dispose old charts
    chartInstances.forEach(c => c && !c.isDisposed() && c.dispose());
    chartInstances.clear();

    // Hide all views first
    $('#emptyState').style.display = 'none';
    $('#dailyView').style.display = 'none';
    $('#rangeView').style.display = 'none';

    if (currentView === 'range') {
        // Range view needs explicit query, don't auto-load
        return;
    }

    const date = datePicker.value;

    try {
        if (currentView === 'daily') {
            const resp = await fetch(`/api/stats/daily?date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            $('#dailyView').style.display = 'block';
            await new Promise(r => requestAnimationFrame(r));
            renderDaily(data);
        }
    } catch {
        $('#emptyState').style.display = 'block';
    }
}

// ========== Daily Render ==========
let prevRecords = [];

function getRankChange(code, todayRecords, prevRecs) {
    const today = todayRecords.find(r => r.stock_code === code);
    const prev = prevRecs.find(r => r.stock_code === code);
    if (!today || !prev) return '';
    const diff = prev.rank - today.rank; // positive = rank improved
    if (diff === 0) return '';
    if (diff > 0) return `<span class="badge badge-up">&#9650;${diff}</span>`;
    return `<span class="badge badge-down">&#9660;${Math.abs(diff)}</span>`;
}

function renderDaily(data) {
    $('#dailyDate').textContent = data.date;
    prevRecords = data.prev_records || [];
    const prevCodes = new Set(prevRecords.map(r => r.stock_code));
    const todayCodes = new Set(data.records.map(r => r.stock_code));
    renderCards(data.records, prevCodes);
    renderRemovedCards(prevRecords, todayCodes);
    requestAnimationFrame(() => {
        renderChangeChart(data.records);
        renderHoldersChart(data.records);
    });
}

function renderCards(records, prevCodes) {
    const grid = $('#cardsGrid');
    grid.innerHTML = records.map(r => {
        const changeClass = r.price_change_pct >= 0 ? 'change-up' : 'change-down';
        const changeSign = r.price_change_pct >= 0 ? '+' : '';
        const diff = (r.holders_today || 0) - (r.holders_yesterday || 0);
        const diffClass = diff >= 0 ? 'diff-up' : 'diff-down';
        const diffSign = diff >= 0 ? '+' : '';
        const rankClass = r.rank <= 3 ? `rank-${r.rank}` : '';
        const isNew = !prevCodes.has(r.stock_code);
        const newBadge = isNew ? '<span class="badge badge-new">NEW</span>' : '';
        const rankChange = !isNew ? getRankChange(r.stock_code, records, prevRecords) : '';
        return `
            <div class="stock-card ${isNew ? 'card-new' : ''}">
                <div class="card-header">
                    <span class="rank ${rankClass}">${r.rank}</span>
                    ${newBadge}
                    ${rankChange}
                    <div class="info">
                        <div class="name">${r.stock_name}</div>
                        <div class="code">${r.stock_code}</div>
                    </div>
                    <div class="heat">${r.heat_value}w</div>
                </div>
                <div class="change ${changeClass}">${changeSign}${r.price_change_pct}%</div>
                <div class="meta">
                    <span>${(r.turnover_amount || 0)}亿</span>
                </div>
                <div class="holders">
                    <span>今: ${r.holders_today}人</span>
                    <span>昨: ${r.holders_yesterday}人</span>
                    <span class="${diffClass}">${diffSign}${diff}</span>
                </div>
                <div class="tags">${(r.sector_tags || []).map(t => `<span class="tag">${t}</span>`).join('')}</div>
            </div>
        `;
    }).join('');
}

function renderRemovedCards(prevRecords, todayCodes) {
    const removed = prevRecords.filter(r => !todayCodes.has(r.stock_code));
    const container = $('#removedGrid');
    const title = $('#removedTitle');
    if (!container) return;
    if (removed.length === 0) {
        container.innerHTML = '';
        if (title) title.style.display = 'none';
        return;
    }
    if (title) title.style.display = 'flex';
    container.innerHTML = removed.map(r => {
        const changeClass = r.price_change_pct >= 0 ? 'change-up' : 'change-down';
        const changeSign = r.price_change_pct >= 0 ? '+' : '';
        return `
            <div class="stock-card card-removed">
                <span class="badge badge-out">OUT</span>
                <div class="card-header">
                    <span class="rank">${r.rank}</span>
                    <div class="info">
                        <div class="name">${r.stock_name}</div>
                        <div class="code">${r.stock_code}</div>
                    </div>
                    <div class="heat">${r.heat_value}w</div>
                </div>
                <div class="change ${changeClass}">${changeSign}${r.price_change_pct}%</div>
                <div class="tags">${(r.sector_tags || []).map(t => `<span class="tag">${t}</span>`).join('')}</div>
            </div>
        `;
    }).join('');
}

function renderChangeChart(records) {
    const chart = getChart('changeChart');
    if (!chart) return;
    const sorted = [...records].sort((a, b) => a.price_change_pct - b.price_change_pct);
    chart.setOption({
        ...CHART_BASE,
        xAxis: {
            type: 'value',
            axisLabel: { color: CHART_COLORS.textMuted, formatter: '{value}%', fontSize: 11 },
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false },
            axisTick: { show: false },
        },
        yAxis: {
            type: 'category',
            data: sorted.map(r => r.stock_name),
            inverse: true,
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.text, fontSize: 12, fontWeight: 600 },
        },
        series: [{
            type: 'bar',
            data: sorted.map(r => ({
                value: r.price_change_pct,
                itemStyle: {
                    color: r.price_change_pct >= 0 ? CHART_COLORS.red : CHART_COLORS.green,
                    borderRadius: r.price_change_pct >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
                },
            })),
            barWidth: '60%',
            label: {
                show: true,
                position: 'right',
                formatter: (p) => (p.value >= 0 ? '+' : '') + p.value + '%',
                fontSize: 11,
                fontFamily: "'JetBrains Mono', monospace",
                color: CHART_COLORS.text,
            },
            animationDelay: (idx) => idx * 60,
        }],
        animationEasing: 'cubicOut',
    });
}

function renderHoldersChart(records) {
    const chart = getChart('holdersChart');
    if (!chart) return;
    chart.setOption({
        ...CHART_BASE,
        grid: { left: 70, right: 16, top: 40, bottom: 30, containLabel: false },
        legend: {
            data: ['今日', '昨日'],
            top: 0,
            textStyle: { color: CHART_COLORS.text, fontSize: 11 },
            itemWidth: 12, itemHeight: 8, itemGap: 16,
        },
        xAxis: {
            type: 'category',
            data: records.map(r => r.stock_name),
            axisLabel: { color: CHART_COLORS.textMuted, rotate: 25, fontSize: 10 },
            axisLine: { lineStyle: { color: CHART_COLORS.line } },
            axisTick: { show: false },
        },
        yAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false },
            axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
        },
        series: [
            {
                name: '今日', type: 'bar',
                data: records.map(r => r.holders_today),
                itemStyle: { color: CHART_COLORS.accent, borderRadius: [3, 3, 0, 0] },
                barGap: '20%',
                animationDelay: (idx) => idx * 60,
            },
            {
                name: '昨日', type: 'bar',
                data: records.map(r => r.holders_yesterday),
                itemStyle: { color: '#334155', borderRadius: [3, 3, 0, 0] },
                animationDelay: (idx) => idx * 60 + 30,
            },
        ],
        animationEasing: 'cubicOut',
    });
}

// ========== Range Query ==========
$('#rangeQueryBtn').addEventListener('click', loadRangeView);

// Set default range dates
$('#rangeStart').value = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
$('#rangeEnd').value = new Date().toISOString().slice(0, 10);

async function loadRangeView() {
    const start = $('#rangeStart').value;
    const end = $('#rangeEnd').value;
    if (!start || !end) { showToast('请选择起止日期', 'error'); return; }

    chartInstances.forEach(c => c && !c.isDisposed() && c.dispose());
    chartInstances.clear();
    $('#emptyState').style.display = 'none';
    $('#dailyView').style.display = 'none';
    $('#rangeView').style.display = 'none';

    try {
        const resp = await fetch(`/api/records/range?start=${start}&end=${end}`);
        if (!resp.ok) throw new Error('no data');
        const records = await resp.json();
        // Parse sector_tags
        records.forEach(r => {
            if (typeof r.sector_tags === 'string') {
                try { r.sector_tags = JSON.parse(r.sector_tags); } catch { r.sector_tags = []; }
            }
        });
        if (records.length === 0) throw new Error('no data');

        $('#rangeDateLabel').textContent = `${start} ~ ${end} (${records.length}条)`;
        $('#rangeView').style.display = 'block';
        await new Promise(r => requestAnimationFrame(r));
        requestAnimationFrame(() => {
            renderSectorChart(records);
            renderFrequencyChart(records);
            renderTrendChart(records);
            renderHoldersTrendChart(records);
            renderHeatMapChart(records);
        });
    } catch {
        $('#emptyState').style.display = 'block';
    }
}

// ========== Range Charts ==========
function renderSectorChart(records) {
    const chart = getChart('sectorChart');
    if (!chart) return;
    const count = {};
    records.forEach(r => (r.sector_tags || []).forEach(t => { count[t] = (count[t] || 0) + 1; }));
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        ...CHART_BASE,
        xAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
        },
        yAxis: {
            type: 'category',
            data: sorted.map(s => s[0]).reverse(),
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.text, fontSize: 12 },
        },
        series: [{
            type: 'bar',
            data: sorted.map(s => s[1]).reverse(),
            itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                    { offset: 0, color: CHART_COLORS.accent },
                    { offset: 1, color: CHART_COLORS.purple },
                ]),
                borderRadius: [0, 4, 4, 0],
            },
            barWidth: '60%',
            label: { show: true, position: 'right', color: CHART_COLORS.text, fontSize: 11 },
            animationDelay: (idx) => idx * 60,
        }],
        animationEasing: 'cubicOut',
    });
}

function renderFrequencyChart(records) {
    const chart = getChart('frequencyChart');
    if (!chart) return;
    const count = {};
    records.forEach(r => { count[r.stock_name] = (count[r.stock_name] || 0) + 1; });
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        ...CHART_BASE,
        xAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
        },
        yAxis: {
            type: 'category',
            data: sorted.map(s => s[0]).reverse(),
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.text, fontSize: 12 },
        },
        series: [{
            type: 'bar',
            data: sorted.map(s => s[1]).reverse(),
            itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                    { offset: 0, color: CHART_COLORS.amber },
                    { offset: 1, color: '#ef4444' },
                ]),
                borderRadius: [0, 4, 4, 0],
            },
            barWidth: '60%',
            label: { show: true, position: 'right', color: CHART_COLORS.text, fontSize: 11 },
            animationDelay: (idx) => idx * 60,
        }],
        animationEasing: 'cubicOut',
    });
}

function renderTrendChart(records) {
    const chart = getChart('trendChart');
    if (!chart) return;
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    const palette = [CHART_COLORS.accent, CHART_COLORS.amber, CHART_COLORS.red, CHART_COLORS.green, CHART_COLORS.cyan];
    chart.setOption({
        ...CHART_BASE,
        grid: { left: 60, right: 20, top: 40, bottom: 30, containLabel: false },
        legend: {
            data: top5, top: 0,
            textStyle: { color: CHART_COLORS.text, fontSize: 11 },
            itemWidth: 14, itemHeight: 3, itemGap: 16,
        },
        xAxis: {
            type: 'category', data: dates,
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
            axisLine: { lineStyle: { color: CHART_COLORS.line } },
            axisTick: { show: false },
        },
        yAxis: {
            type: 'value', name: '热度(万)',
            nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 11 },
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
        },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#111622',
            borderColor: '#1e293b',
            textStyle: { color: '#f1f5f9', fontSize: 12 },
        },
        series: top5.map((name, i) => ({
            name,
            type: 'line',
            data: dates.map(d => {
                const r = records.find(r => r.date === d && r.stock_name === name);
                return r ? r.heat_value : null;
            }),
            connectNulls: true,
            smooth: true,
            symbolSize: 6,
            symbol: 'circle',
            lineStyle: { width: 2.5, color: palette[i] },
            itemStyle: { color: palette[i] },
            areaStyle: {
                opacity: 0.06,
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                    { offset: 0, color: palette[i] },
                    { offset: 1, color: 'transparent' },
                ]),
            },
            animationDelay: i * 100,
        })),
        animationEasing: 'cubicOut',
    });
}

function renderHoldersTrendChart(records) {
    const chart = getChart('holdersTrendChart');
    if (!chart) return;
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    const palette = [CHART_COLORS.accent, CHART_COLORS.amber, CHART_COLORS.red, CHART_COLORS.green, CHART_COLORS.cyan];
    chart.setOption({
        ...CHART_BASE,
        grid: { left: 60, right: 20, top: 40, bottom: 30, containLabel: false },
        legend: {
            data: top5, top: 0,
            textStyle: { color: CHART_COLORS.text, fontSize: 11 },
            itemWidth: 14, itemHeight: 3, itemGap: 16,
        },
        xAxis: {
            type: 'category', data: dates,
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
            axisLine: { lineStyle: { color: CHART_COLORS.line } },
            axisTick: { show: false },
        },
        yAxis: {
            type: 'value', name: '持仓人数',
            nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 11 },
            splitLine: { lineStyle: { color: CHART_COLORS.line } },
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
        },
        tooltip: {
            trigger: 'axis',
            backgroundColor: '#111622', borderColor: '#1e293b',
            textStyle: { color: '#f1f5f9', fontSize: 12 },
        },
        series: top5.map((name, i) => ({
            name, type: 'line', smooth: true,
            data: dates.map(d => {
                const r = records.find(r => r.date === d && r.stock_name === name);
                return r ? r.holders_today : null;
            }),
            connectNulls: true,
            lineStyle: { width: 2.5, color: palette[i] },
            itemStyle: { color: palette[i] },
            symbolSize: 5,
        })),
    });
}

function renderHeatMapChart(records) {
    const chart = getChart('heatMapChart');
    if (!chart) return;
    const stocks = [...new Set(records.map(r => r.stock_name))];
    const dates = [...new Set(records.map(r => r.date))].sort();
    const heatData = [];
    records.forEach(r => {
        const x = dates.indexOf(r.date);
        const y = stocks.indexOf(r.stock_name);
        heatData.push([x, y, r.heat_value || 0]);
    });
    chart.setOption({
        ...CHART_BASE,
        grid: { left: 90, right: 60, top: 10, bottom: 40, containLabel: false },
        xAxis: {
            type: 'category', data: dates,
            splitArea: { show: true, areaStyle: { color: ['rgba(30,41,59,0.3)', 'transparent'] } },
            axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
            axisLine: { show: false }, axisTick: { show: false },
        },
        yAxis: {
            type: 'category', data: stocks,
            splitArea: { show: true, areaStyle: { color: ['rgba(30,41,59,0.3)', 'transparent'] } },
            axisLabel: { color: CHART_COLORS.text, fontSize: 11 },
            axisLine: { show: false }, axisTick: { show: false },
        },
        visualMap: {
            min: 0, max: 1500,
            right: 0, top: 'center', orient: 'vertical',
            inRange: { color: ['#0c1018', '#1e40af', '#3b82f6', '#f59e0b', '#ef4444'] },
            textStyle: { color: CHART_COLORS.textMuted, fontSize: 11 },
            itemWidth: 12, itemHeight: 100,
        },
        tooltip: {
            backgroundColor: '#111622', borderColor: '#1e293b',
            textStyle: { color: '#f1f5f9', fontSize: 12 },
            formatter: (p) => `${dates[p.value[0]]} - ${stocks[p.value[1]]}<br/>热度: ${p.value[2]}w`,
        },
        series: [{
            type: 'heatmap', data: heatData,
            label: {
                show: true,
                formatter: (p) => p.value[2] ? p.value[2] + 'w' : '',
                fontSize: 10,
                fontFamily: "'JetBrains Mono', monospace",
                color: '#f1f5f9',
            },
            itemStyle: { borderColor: '#0c1018', borderWidth: 2, borderRadius: 3 },
        }],
    });
}
