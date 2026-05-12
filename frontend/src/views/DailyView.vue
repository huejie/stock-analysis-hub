<script setup lang="ts">
import { ref, computed, watch, nextTick, inject, type Ref } from 'vue'
import type { StockRecord, DailyStatsResponse } from '../types'
import { useApi } from '../composables/useApi'
import { buildChangeChartOption, buildHoldersChartOption } from '../charts/daily'
import { CHART_COLORS, CHART_BASE } from '../charts/theme'
import ChartBox from '../components/ChartBox.vue'
import StockCard from '../components/StockCard.vue'
import EmptyState from '../components/EmptyState.vue'

const api = useApi()
const isAdmin = inject<Ref<boolean>>('isAdmin', ref(false))

const date = ref(new Date().toISOString().slice(0, 10))
const availableDates = ref<string[]>([])
const data = ref<DailyStatsResponse | null>(null)
const loading = ref(false)
const crawling = ref(false)
const crawlTarget = ref('')
const toastMsg = ref('')
const toastVisible = ref(false)
const historyMap = ref<Map<string, StockRecord[]>>(new Map())
const selectedCode = ref<string | null>(null)

const changeChartRef = ref()
const holdersChartRef = ref()
const trendChartEl = ref<HTMLElement | null>(null)

// 5个历史交易日（按时间正序：最早 → 最近）
const prevDates = computed(() => {
  const idx = availableDates.value.indexOf(date.value)
  if (idx < 0) return []
  // slice从idx+1开始（DESC），取5个，再reverse成正序
  return availableDates.value.slice(idx + 1, idx + 5).reverse()
})

// 表格行数据：当天在榜的股票，每行包含该股票5日记录
const tableRows = computed(() => {
  if (!data.value) return []
  return data.value.records.map(r => {
    const hist = historyMap.value.get(r.stock_code) || []
    const days = prevDates.value.map(d => {
      const rec = hist.find(h => h.date === d)
      return rec ? { date: d, rank: rec.rank, change: rec.price_change_pct, holders: rec.holders_today } : null
    })
    return { name: r.stock_name, code: r.stock_code, todayRank: r.rank, todayChange: r.price_change_pct, todayHolders: r.holders_today, days }
  })
})

function getRankChange(code: string, today: StockRecord[], prev: StockRecord[]): string {
  const t = today.find(r => r.stock_code === code)
  const p = prev.find(r => r.stock_code === code)
  if (!t || !p) return ''
  const diff = p.rank - t.rank
  if (diff === 0) return ''
  if (diff > 0) return `<span class="badge badge-up">&#9650;${diff}</span>`
  return `<span class="badge badge-down">&#9660;${Math.abs(diff)}</span>`
}

function showToast(msg: string) {
  toastMsg.value = msg
  toastVisible.value = true
  setTimeout(() => { toastVisible.value = false }, 2500)
}

async function handleCrawl() {
  crawling.value = true
  try {
    const res = await api.crawlToday()
    showToast(res.message)
    await loadDates()
    await load()
  } catch (e: unknown) {
    showToast('抓取失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    crawling.value = false
  }
}

async function loadDates() {
  const res = await api.fetchDates()
  availableDates.value = res.dates
  if (res.dates.length > 0 && !res.dates.includes(date.value)) {
    date.value = res.dates[0]!
  }
}

async function load() {
  loading.value = true
  try {
    data.value = await api.fetchDailyStats(date.value)
  } catch {
    data.value = null
  }
  loading.value = false
}

async function loadHistory() {
  if (!data.value || prevDates.value.length === 0) {
    historyMap.value = new Map()
    return
  }
  const start = prevDates.value[0]!
  const end = prevDates.value[prevDates.value.length - 1]!
  const records = await api.fetchRangeRecords(start, end)
  const map = new Map<string, StockRecord[]>()
  for (const r of records) {
    const arr = map.get(r.stock_code) || []
    arr.push(r)
    map.set(r.stock_code, arr)
  }
  historyMap.value = map
}

function prevDate() {
  const idx = availableDates.value.indexOf(date.value)
  if (idx >= 0 && idx < availableDates.value.length - 1) {
    date.value = availableDates.value[idx + 1]!
    load()
  }
}

function nextDate() {
  const idx = availableDates.value.indexOf(date.value)
  if (idx > 0) {
    date.value = availableDates.value[idx - 1]!
    load()
  }
}

// 所有日期标签（当天 + 历史日，正序）
const allTrendDates = computed(() => {
  if (!data.value) return []
  return [...prevDates.value, data.value.date]
})

function selectRow(code: string) {
  selectedCode.value = selectedCode.value === code ? null : code
}

import * as echarts from 'echarts'
let trendChart: echarts.ECharts | null = null

watch(selectedCode, async (code) => {
  if (!code || !data.value) {
    trendChart?.dispose()
    trendChart = null
    return
  }
  await nextTick()
  if (!trendChartEl.value) return
  if (!trendChart || trendChart.isDisposed()) {
    trendChart = echarts.init(trendChartEl.value)
  }

  const row = tableRows.value.find(r => r.code === code)
  if (!row) return

  // 合并历史日 + 当天的数据点
  const points = [
    ...row.days.map(d => d ? { rank: d.rank, change: d.change, holders: d.holders } : null),
    { rank: row.todayRank, change: data.value!.records.find(r => r.stock_code === code)?.price_change_pct ?? null, holders: row.todayHolders },
  ]
  const labels = allTrendDates.value.map(d => d.slice(5))

  trendChart.setOption({
    ...CHART_BASE,
    title: {
      text: `${row.name} 近${labels.length}日趋势`,
      left: 12, top: 8,
      textStyle: { color: CHART_COLORS.text, fontSize: 14, fontWeight: 700, fontFamily: "'DM Sans', 'PingFang SC', sans-serif" },
    },
    tooltip: { trigger: 'axis', backgroundColor: '#1a2236', borderColor: '#243044', textStyle: { color: '#dce4ec', fontSize: 12 } },
    legend: {
      data: ['排名', '涨跌幅', '持仓人数'],
      top: 0, left: 'center',
      textStyle: { color: CHART_COLORS.text, fontSize: 12 },
      itemWidth: 12, itemHeight: 8,
    },
    grid: { left: 55, right: 140, top: 56, bottom: 28, containLabel: false },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 12 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '排名',
        inverse: true, min: 1, max: 10,
        axisLabel: { color: CHART_COLORS.accent, fontSize: 11 },
        splitLine: { lineStyle: { color: CHART_COLORS.line } },
        axisLine: { show: false }, axisTick: { show: false },
      },
      {
        type: 'value', name: '涨跌幅%',
        position: 'right',
        axisLabel: { color: CHART_COLORS.red, fontSize: 11, formatter: '{value}%' },
        splitLine: { show: false },
        axisLine: { show: false }, axisTick: { show: false },
      },
      {
        type: 'value', name: '持仓人数',
        position: 'right', offset: 80,
        axisLabel: { color: CHART_COLORS.purple, fontSize: 11 },
        splitLine: { show: false },
        axisLine: { show: false }, axisTick: { show: false },
      },
    ],
    series: [
      {
        name: '排名', type: 'line', yAxisIndex: 0,
        data: points.map(p => p?.rank ?? null),
        lineStyle: { color: CHART_COLORS.accent, width: 2 },
        itemStyle: { color: CHART_COLORS.accent },
        symbol: 'circle', symbolSize: 6,
      },
      {
        name: '涨跌幅', type: 'line', yAxisIndex: 1,
        data: points.map(p => p?.change ?? null),
        lineStyle: { color: CHART_COLORS.red, width: 2 },
        itemStyle: { color: CHART_COLORS.red },
        symbol: 'circle', symbolSize: 6,
      },
      {
        name: '持仓人数', type: 'line', yAxisIndex: 2,
        data: points.map(p => p?.holders ?? null),
        lineStyle: { color: CHART_COLORS.purple, width: 2 },
        itemStyle: { color: CHART_COLORS.purple },
        symbol: 'circle', symbolSize: 6,
      },
    ],
  })
})

async function init() {
  await loadDates()
  await load()
  await loadHistory()
  if (data.value) {
    await new Promise(r => requestAnimationFrame(r))
    changeChartRef.value?.setOption(buildChangeChartOption(data.value.records))
    holdersChartRef.value?.setOption(buildHoldersChartOption(data.value.records))
  }
}

init()
</script>

<template>
  <div>
    <!-- Date navigation -->
    <div class="lhb-header">
      <div class="date-group" style="margin-bottom:0">
        <button class="btn-icon" title="前一天" @click="prevDate">&#9664;</button>
        <input v-model="date" type="date" @change="load">
        <button class="btn-icon" title="后一天" @click="nextDate">&#9654;</button>
      </div>
      <button class="btn btn-primary btn-sm" :disabled="crawling" @click="handleCrawl">
        {{ crawling ? '抓取中...' : '抓取今日' }}
      </button>
      <div v-if="toastVisible" class="toast-inline">{{ toastMsg }}</div>
    </div>

    <EmptyState v-if="!data && !loading" />

    <template v-if="data">
      <div class="section-title">
        <span class="section-icon">&#9670;</span> 热榜 Top 10
        <span class="section-date">{{ data.date }}</span>
      </div>

      <div class="cards-grid">
        <StockCard
          v-for="r in data.records"
          :key="r.stock_code"
          :record="r"
          :is-new="!data.prev_records.some(p => p.stock_code === r.stock_code)"
          :rank-change="data.prev_records.some(p => p.stock_code === r.stock_code)
            ? getRankChange(r.stock_code, data.records, data.prev_records)
            : ''"
        />
      </div>

      <!-- 5日对比表格 -->
      <div v-if="prevDates.length > 0" class="compare-section">
        <div class="section-title">
          <span class="section-icon">&#9672;</span> 近{{ prevDates.length + 1 }}日对比
          <span v-if="!selectedCode" class="ct-hint">点击行或图表按钮查看趋势</span>
        </div>
        <!-- 趋势折线图 -->
        <div v-if="selectedCode" class="trend-chart-box">
          <div ref="trendChartEl" class="trend-chart-container"></div>
        </div>
        <div class="compare-table-wrap">
          <table class="compare-table">
            <thead>
              <tr>
                <th class="ct-stock">股票</th>
                <th v-for="d in prevDates" :key="d" class="ct-day">{{ d.slice(5) }}</th>
                <th class="ct-day ct-today">{{ data.date.slice(5) }}</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="row in tableRows"
                :key="row.code"
                class="ct-clickable"
                :class="{ 'ct-selected': selectedCode === row.code }"
                @click="selectRow(row.code)"
              >
                <td class="ct-stock" @click.stop>
                  <span class="ct-name">{{ row.name }}</span>
                  <button
                    class="ct-chart-btn"
                    :class="{ active: selectedCode === row.code }"
                    title="查看趋势"
                    @click="selectRow(row.code)"
                  >&#9650;</button>
                </td>
                <td v-for="(day, i) in row.days" :key="i" class="ct-day">
                  <template v-if="day">
                    <span class="ct-rank">#{{ day.rank }}</span>
                    <span class="ct-change" :class="(day.change ?? 0) >= 0 ? 'ct-up' : 'ct-down'">
                      {{ (day.change ?? 0) >= 0 ? '+' : '' }}{{ day.change }}%
                    </span>
                    <span class="ct-holders">{{ day.holders ?? '-' }}人</span>
                  </template>
                  <span v-else class="ct-miss">-</span>
                </td>
                <td class="ct-day ct-today">
                  <span class="ct-rank">#{{ row.todayRank }}</span>
                  <span class="ct-change" :class="(row.todayChange ?? 0) >= 0 ? 'ct-up' : 'ct-down'">
                    {{ (row.todayChange ?? 0) >= 0 ? '+' : '' }}{{ row.todayChange }}%
                  </span>
                  <span class="ct-holders">{{ row.todayHolders ?? '-' }}人</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <template v-if="data.prev_records.some(p => !data!.records.some(t => t.stock_code === p.stock_code))">
        <div class="section-title removed-title" style="display:flex">
          <span class="section-icon">&#9671;</span> 移出热榜
        </div>
        <div class="cards-grid cards-removed">
          <div
            v-for="r in data.prev_records.filter(p => !data!.records.some(t => t.stock_code === p.stock_code))"
            :key="'rm-' + r.stock_code"
            class="stock-card card-removed"
          >
            <span class="badge badge-out">OUT</span>
            <div class="card-header">
              <span class="rank">{{ r.rank }}</span>
              <div class="info">
                <div class="name-row">
                  <span class="name">{{ r.stock_name }}</span>
                  <span class="code">{{ r.stock_code }}</span>
                </div>
              </div>
            </div>
            <div class="card-body">
              <div class="change" :class="r.price_change_pct! >= 0 ? 'change-up' : 'change-down'">
                {{ r.price_change_pct! >= 0 ? '+' : '' }}{{ r.price_change_pct }}%
              </div>
              <div class="tags">
                <span v-for="tag in r.sector_tags" :key="tag" class="tag">{{ tag }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>

      <div class="charts-row">
        <ChartBox ref="changeChartRef" title="涨跌幅排行" />
        <ChartBox ref="holdersChartRef" title="持仓人数对比" />
      </div>
    </template>
  </div>
</template>
