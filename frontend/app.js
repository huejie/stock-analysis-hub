// ========== State ==========
let currentView = 'daily';
let parsedData = null;
let availableDates = [];

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

// ========== Init ==========
document.addEventListener('DOMContentLoaded', async () => {
    datePicker.value = new Date().toISOString().slice(0, 10);
    await loadDates();
    loadView();
});

// ========== Toast ==========
function showToast(msg, type = 'success') {
    toast.textContent = msg;
    toast.style.borderColor = type === 'success' ? 'var(--green)' : 'var(--red)';
    toast.style.color = type === 'success' ? 'var(--green)' : 'var(--red)';
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

// ========== Nav ==========
$$('.tabs .btn').forEach(btn => {
    btn.addEventListener('click', () => {
        $$('.tabs .btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        currentView = btn.dataset.view;
        loadView();
    });
});

datePicker.addEventListener('change', loadView);

// Date navigation
$('#prevDateBtn').addEventListener('click', () => {
    const d = new Date(datePicker.value);
    d.setDate(d.getDate() - 1);
    datePicker.value = d.toISOString().slice(0, 10);
    loadView();
});
$('#nextDateBtn').addEventListener('click', () => {
    const d = new Date(datePicker.value);
    d.setDate(d.getDate() + 1);
    datePicker.value = d.toISOString().slice(0, 10);
    loadView();
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
        uploadBtn.textContent = '📷 上传图片';
        uploadBtn.disabled = false;
    }
}

// ========== Confirm Modal ==========
function showConfirmModal(data) {
    editTableBody.innerHTML = '';
    $('#modalCount').textContent = `识别到 ${data.records.length} 只股票`;

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
        showToast(`保存成功！共 ${parsedData.records.length} 只股票`);
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

    // Populate date history dropdown
    dateHistory.innerHTML = '<option value="">📅 历史记录</option>';
    availableDates.slice().reverse().forEach(d => {
        const opt = document.createElement('option');
        opt.value = d;
        opt.textContent = d;
        dateHistory.appendChild(opt);
    });

    // If there's saved data, jump to the latest date
    if (availableDates.length > 0) {
        const latestDate = availableDates[availableDates.length - 1];
        const today = new Date().toISOString().slice(0, 10);
        // Only auto-switch if current date has no data
        if (!availableDates.includes(datePicker.value)) {
            datePicker.value = latestDate;
        }
    }
}

async function loadView() {
    const date = datePicker.value;
    $('#emptyState').style.display = 'none';
    $('#dailyView').style.display = 'none';
    $('#weeklyView').style.display = 'none';
    $('#monthlyView').style.display = 'none';

    try {
        if (currentView === 'daily') {
            const resp = await fetch(`/api/stats/daily?date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderDaily(data);
            $('#dailyView').style.display = 'block';
        } else if (currentView === 'weekly') {
            const resp = await fetch(`/api/stats/weekly?end_date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderWeekly(data);
            $('#weeklyView').style.display = 'block';
        } else {
            const resp = await fetch(`/api/stats/monthly?end_date=${date}`);
            if (!resp.ok) throw new Error('no data');
            const data = await resp.json();
            renderMonthly(data);
            $('#monthlyView').style.display = 'block';
        }
    } catch {
        $('#emptyState').style.display = 'block';
    }
}

// ========== Daily Render ==========
function renderDaily(data) {
    const dateEl = $('#dailyDate');
    dateEl.textContent = data.date;
    renderCards(data.records);
    renderChangeChart(data.records);
    renderHoldersChart(data.records);
}

function renderCards(records) {
    const grid = $('#cardsGrid');
    grid.innerHTML = records.map(r => {
        const changeClass = r.price_change_pct >= 0 ? 'change-up' : 'change-down';
        const changeSign = r.price_change_pct >= 0 ? '+' : '';
        const diff = (r.holders_today || 0) - (r.holders_yesterday || 0);
        const diffClass = diff >= 0 ? 'diff-up' : 'diff-down';
        const diffSign = diff >= 0 ? '+' : '';
        const rankClass = r.rank <= 3 ? `rank-${r.rank}` : '';
        return `
            <div class="stock-card">
                <div class="card-header">
                    <span class="rank ${rankClass}">${r.rank}</span>
                    <div class="info">
                        <div class="name">${r.stock_name}</div>
                        <div class="code">${r.stock_code}</div>
                    </div>
                    <div class="heat">${r.heat_value}w</div>
                </div>
                <div class="change ${changeClass}">${changeSign}${r.price_change_pct}%</div>
                <div class="meta">
                    <span>💰 ${(r.turnover_amount || 0)}亿</span>
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

function renderChangeChart(records) {
    const chart = echarts.init($('#changeChart'), 'dark');
    const sorted = [...records].sort((a, b) => a.price_change_pct - b.price_change_pct);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 40, top: 10, bottom: 30 },
        xAxis: { type: 'value', axisLabel: { formatter: '{value}%' }, splitLine: { lineStyle: { color: '#1e2a45' } } },
        yAxis: { type: 'category', data: sorted.map(r => r.stock_name), inverse: true, axisLine: { show: false } },
        series: [{
            type: 'bar',
            data: sorted.map(r => ({
                value: r.price_change_pct,
                itemStyle: { color: r.price_change_pct >= 0 ? '#ef4444' : '#22c55e', borderRadius: r.price_change_pct >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] }
            })),
            label: { show: true, position: 'right', formatter: (p) => (p.value >= 0 ? '+' : '') + p.value + '%', fontSize: 12 },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderHoldersChart(records) {
    const chart = echarts.init($('#holdersChart'), 'dark');
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 40, bottom: 30 },
        legend: { data: ['今日', '昨日'], top: 0, textStyle: { fontSize: 12 } },
        xAxis: { type: 'category', data: records.map(r => r.stock_name), axisLabel: { rotate: 30, fontSize: 11 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e2a45' } } },
        series: [
            { name: '今日', type: 'bar', data: records.map(r => r.holders_today), itemStyle: { color: '#3b82f6', borderRadius: [4, 4, 0, 0] } },
            { name: '昨日', type: 'bar', data: records.map(r => r.holders_yesterday), itemStyle: { color: '#334155', borderRadius: [4, 4, 0, 0] } },
        ],
    });
    window.addEventListener('resize', () => chart.resize());
}

// ========== Weekly Render ==========
function renderWeekly(data) {
    renderSectorChart(data.records);
    renderFrequencyChart(data.records);
    renderTrendChart(data.records);
}

function renderSectorChart(records) {
    const chart = echarts.init($('#sectorChart'), 'dark');
    const count = {};
    records.forEach(r => (r.sector_tags || []).forEach(t => { count[t] = (count[t] || 0) + 1; }));
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 30, top: 10, bottom: 30 },
        xAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e2a45' } } },
        yAxis: { type: 'category', data: sorted.map(s => s[0]).reverse(), axisLine: { show: false } },
        series: [{
            type: 'bar',
            data: sorted.map(s => s[1]).reverse(),
            itemStyle: { color: '#3b82f6', borderRadius: [0, 4, 4, 0] },
            label: { show: true, position: 'right' },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderFrequencyChart(records) {
    const chart = echarts.init($('#frequencyChart'), 'dark');
    const count = {};
    records.forEach(r => { count[r.stock_name] = (count[r.stock_name] || 0) + 1; });
    const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 30, top: 10, bottom: 30 },
        xAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e2a45' } } },
        yAxis: { type: 'category', data: sorted.map(s => s[0]).reverse(), axisLine: { show: false } },
        series: [{
            type: 'bar',
            data: sorted.map(s => s[1]).reverse(),
            itemStyle: { color: '#f59e0b', borderRadius: [0, 4, 4, 0] },
            label: { show: true, position: 'right' },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderTrendChart(records) {
    const chart = echarts.init($('#trendChart'), 'dark');
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    const series = top5.map(name => ({
        name,
        type: 'line',
        data: dates.map(d => {
            const r = records.find(r => r.date === d && r.stock_name === name);
            return r ? r.heat_value : null;
        }),
        connectNulls: true,
        smooth: true,
        symbolSize: 6,
    }));
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 60, right: 20, top: 40, bottom: 30 },
        legend: { data: top5, top: 0, textStyle: { fontSize: 12 } },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
        yAxis: { type: 'value', name: '热度(万)', splitLine: { lineStyle: { color: '#1e2a45' } } },
        tooltip: { trigger: 'axis' },
        series,
    });
    window.addEventListener('resize', () => chart.resize());
}

// ========== Monthly Render ==========
function renderMonthly(data) {
    renderRotationChart(data.records);
    renderMonthlyHoldersChart(data.records);
    renderMonthlyHeatChart(data.records);
}

function renderRotationChart(records) {
    const chart = echarts.init($('#rotationChart'), 'dark');
    const weekSectors = {};
    records.forEach(r => {
        const weekStart = r.date;
        if (!weekSectors[weekStart]) weekSectors[weekStart] = {};
        (r.sector_tags || []).forEach(t => { weekSectors[weekStart][t] = (weekSectors[weekStart][t] || 0) + 1; });
    });
    const allSectors = [...new Set(records.flatMap(r => r.sector_tags || []))];
    const dates = Object.keys(weekSectors).sort();
    const top10 = allSectors.slice(0, 10);
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 80, right: 20, top: 40, bottom: 30 },
        legend: { data: top10, top: 0, type: 'scroll', textStyle: { fontSize: 11 } },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
        yAxis: { type: 'value', splitLine: { lineStyle: { color: '#1e2a45' } } },
        tooltip: { trigger: 'axis' },
        series: top10.map(name => ({
            name, type: 'line', stack: 'total', smooth: true,
            data: dates.map(d => weekSectors[d]?.[name] || 0),
            areaStyle: { opacity: 0.3 },
        })),
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderMonthlyHoldersChart(records) {
    const chart = echarts.init($('#monthlyHoldersChart'), 'dark');
    const stockCount = {};
    records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1; });
    const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0]);
    const dates = [...new Set(records.map(r => r.date))].sort();
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 60, right: 20, top: 40, bottom: 30 },
        legend: { data: top5, top: 0, textStyle: { fontSize: 12 } },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 11 } },
        yAxis: { type: 'value', name: '持仓人数', splitLine: { lineStyle: { color: '#1e2a45' } } },
        tooltip: { trigger: 'axis' },
        series: top5.map(name => ({
            name, type: 'line', smooth: true,
            data: dates.map(d => {
                const r = records.find(r => r.date === d && r.stock_name === name);
                return r ? r.holders_today : null;
            }),
            connectNulls: true,
        })),
    });
    window.addEventListener('resize', () => chart.resize());
}

function renderMonthlyHeatChart(records) {
    const chart = echarts.init($('#monthlyHeatChart'), 'dark');
    const stocks = [...new Set(records.map(r => r.stock_name))];
    const dates = [...new Set(records.map(r => r.date))].sort();
    const heatData = [];
    records.forEach(r => {
        const x = dates.indexOf(r.date);
        const y = stocks.indexOf(r.stock_name);
        heatData.push([x, y, r.heat_value || 0]);
    });
    chart.setOption({
        backgroundColor: 'transparent',
        grid: { left: 100, right: 60, top: 10, bottom: 40 },
        xAxis: { type: 'category', data: dates, splitArea: { show: true }, axisLabel: { fontSize: 11 } },
        yAxis: { type: 'category', data: stocks, splitArea: { show: true }, axisLabel: { fontSize: 11 } },
        visualMap: {
            min: 0, max: 1500, right: 0, top: 'center', orient: 'vertical',
            inRange: { color: ['#141b2d', '#3b82f6', '#f59e0b', '#ef4444'] },
            textStyle: { fontSize: 11 },
        },
        tooltip: { formatter: (p) => `${dates[p.value[0]]} - ${stocks[p.value[1]]}<br/>热度: ${p.value[2]}w` },
        series: [{
            type: 'heatmap', data: heatData,
            label: { show: true, formatter: (p) => p.value[2] ? p.value[2] + 'w' : '', fontSize: 10 },
        }],
    });
    window.addEventListener('resize', () => chart.resize());
}
