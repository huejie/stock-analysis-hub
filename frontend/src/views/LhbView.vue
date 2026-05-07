<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import type { LhbSignal, LhbAnalysis, LhbTradingDesk } from '../types'
import { useApi } from '../composables/useApi'
import { CHART_COLORS, CHART_BASE } from '../charts/theme'
import ChartBox from '../components/ChartBox.vue'
import * as echarts from 'echarts'

const api = useApi()

const signalDates = ref<string[]>([])
const selectedDate = ref('')
const signals = ref<LhbSignal[]>([])
const analysis = ref<LhbAnalysis | null>(null)
const loading = ref(false)
const crawling = ref(false)
const toastMsg = ref('')
const toastVisible = ref(false)
const analysisMonths = ref(3)
const sectorChartRef = ref()

const expandedCode = ref<string | null>(null)
const deskMap = ref<Map<string, LhbTradingDesk[]>>(new Map())

const foreignSignals = computed(() => signals.value.filter(s => s.signal_type === 'foreign'))
const instDenseSignals = computed(() => signals.value.filter(s => s.signal_type === 'inst_dense'))

function showToast(msg: string) {
  toastMsg.value = msg
  toastVisible.value = true
  setTimeout(() => { toastVisible.value = false }, 2500)
}

function fmtAmt(val: number | null): string {
  if (val == null) return '-'
  if (Math.abs(val) >= 10000) return (val / 10000).toFixed(2) + '亿'
  return val.toFixed(0) + '万'
}

function fmtChange(val: number | null): string {
  if (val == null) return '-'
  const prefix = val >= 0 ? '+' : ''
  return prefix + val.toFixed(2) + '%'
}

function changeClass(val: number | null): string {
  if (val == null) return ''
  return val >= 0 ? 'ct-up' : 'ct-down'
}

async function toggleExpand(code: string) {
  if (expandedCode.value === code) {
    expandedCode.value = null
    return
  }
  expandedCode.value = code
  if (!deskMap.value.has(code) && selectedDate.value) {
    try {
      const desks = await api.fetchLhbTradingDesk(selectedDate.value, code)
      deskMap.value = new Map(deskMap.value).set(code, desks)
    } catch {
      deskMap.value = new Map(deskMap.value).set(code, [])
    }
  }
}

function getBuyDesks(code: string) {
  return (deskMap.value.get(code) || []).filter(d => d.side === 'buy')
}

function getSellDesks(code: string) {
  return (deskMap.value.get(code) || []).filter(d => d.side === 'sell')
}

async function loadDates() {
  const res = await api.fetchLhbSignalDates()
  signalDates.value = res.dates
  if (res.dates.length > 0 && !selectedDate.value) {
    selectedDate.value = res.dates[0]!
  }
}

async function loadSignals() {
  if (!selectedDate.value) return
  loading.value = true
  expandedCode.value = null
  deskMap.value = new Map()
  try {
    signals.value = await api.fetchLhbSignals(selectedDate.value)
  } catch {
    signals.value = []
  }
  loading.value = false
}

async function loadAnalysis() {
  try {
    analysis.value = await api.fetchLhbAnalysis(analysisMonths.value)
    await nextTick()
    renderSectorChart()
  } catch {
    analysis.value = null
  }
}

async function handleCrawlLhb() {
  crawling.value = true
  try {
    const res = await api.crawlLhb()
    showToast(res.message)
    await loadDates()
    if (selectedDate.value) await loadSignals()
  } catch (e: unknown) {
    showToast('爬取失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    crawling.value = false
  }
}

function renderSectorChart() {
  if (!sectorChartRef.value || !analysis.value) return
  const data = analysis.value.sector_distribution.slice(0, 15)
  const chart = sectorChartRef.value.getChart()
  if (!chart) return

  chart.setOption({
    ...CHART_BASE,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1a2236',
      borderColor: '#243044',
      textStyle: { color: '#dce4ec', fontSize: 12 },
      formatter: (params: any) => {
        const p = params[0]
        const s = data[p.dataIndex]
        const avg = s?.avg_change != null ? fmtChange(s.avg_change) : '-'
        return `${p.name}<br/>出现次数: ${p.value}<br/>平均涨跌: ${avg}`
      },
    },
    grid: { left: 100, right: 24, top: 12, bottom: 30, containLabel: false },
    xAxis: {
      type: 'value',
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
    },
    yAxis: {
      type: 'category',
      data: data.map(d => d.sector).reverse(),
      axisLabel: { color: CHART_COLORS.text, fontSize: 12, width: 80, overflow: 'truncate' },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
      axisTick: { show: false },
    },
    series: [{
      type: 'bar',
      data: data.map(d => d.count).reverse(),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
          { offset: 0, color: '#3b82f6' },
          { offset: 1, color: '#8b5cf6' },
        ]),
        borderRadius: [0, 4, 4, 0],
      },
      barWidth: 16,
    }],
  })
}

async function init() {
  await loadDates()
  if (selectedDate.value) await loadSignals()
  await loadAnalysis()
}

init()
</script>

<template>
  <div>
    <div class="lhb-header">
      <div class="lhb-controls">
        <button class="btn btn-primary" :disabled="crawling" @click="handleCrawlLhb">
          {{ crawling ? '抓取中...' : '抓取龙虎榜' }}
        </button>
        <select v-model="selectedDate" class="lhb-select" @change="loadSignals">
          <option value="">选择日期</option>
          <option v-for="d in signalDates" :key="d" :value="d">{{ d }}</option>
        </select>
      </div>
      <div v-if="toastVisible" class="toast-inline">{{ toastMsg }}</div>
    </div>

    <div v-if="loading" class="lhb-loading">加载中...</div>

    <template v-if="!loading && selectedDate">
      <!-- 境外机构信号 -->
      <div class="section-title">
        <span class="section-icon">&#9733;</span> 境外机构买入
        <span class="ct-hint">共 {{ foreignSignals.length }} 只</span>
      </div>
      <div v-if="foreignSignals.length === 0" class="lhb-empty">当日无境外机构信号</div>
      <div v-else class="lhb-table-wrap">
        <table class="compare-table">
          <thead>
            <tr>
              <th class="lhb-col-name">股票</th>
              <th>涨跌幅</th>
              <th>买入金额</th>
              <th>卖出金额</th>
              <th>净买入</th>
              <th class="lhb-col-tags">概念板块</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="s in foreignSignals" :key="s.stock_code">
              <tr class="ct-clickable" :class="{ 'ct-selected': expandedCode === s.stock_code }" @click="toggleExpand(s.stock_code)">
                <td class="lhb-col-name">
                  <span class="ct-name">{{ s.stock_name }}</span>
                  <span class="lhb-code">{{ s.stock_code }}</span>
                  <button class="lhb-expand-btn" :class="{ active: expandedCode === s.stock_code }" title="查看营业部明细">&#9654;</button>
                </td>
                <td :class="changeClass(s.change_rate)">{{ fmtChange(s.change_rate) }}</td>
                <td>{{ fmtAmt(s.buy_amt) }}</td>
                <td>{{ fmtAmt(s.sell_amt) }}</td>
                <td :class="(s.net_amt ?? 0) >= 0 ? 'ct-up' : 'ct-down'" class="lhb-bold">
                  {{ fmtAmt(s.net_amt) }}
                </td>
                <td class="lhb-col-tags">
                  <span v-for="tag in (s.concept_tags || []).slice(0, 3)" :key="tag" class="tag">{{ tag }}</span>
                </td>
              </tr>
              <!-- 展开的营业部明细 -->
              <tr v-if="expandedCode === s.stock_code" class="lhb-desk-row">
                <td colspan="6" class="lhb-desk-cell">
                  <div class="lhb-desk-wrap">
                    <div class="lhb-desk-side">
                      <div class="lhb-desk-title lhb-buy-title">买入营业部</div>
                      <div v-for="d in getBuyDesks(s.stock_code)" :key="d.dept_name" class="lhb-desk-item">
                        <span class="lhb-desk-name">{{ d.dept_name }}</span>
                        <span class="lhb-desk-amt">{{ fmtAmt(d.buy_amt) }}</span>
                      </div>
                      <div v-if="getBuyDesks(s.stock_code).length === 0" class="lhb-desk-empty">-</div>
                    </div>
                    <div class="lhb-desk-side">
                      <div class="lhb-desk-title lhb-sell-title">卖出营业部</div>
                      <div v-for="d in getSellDesks(s.stock_code)" :key="d.dept_name" class="lhb-desk-item">
                        <span class="lhb-desk-name">{{ d.dept_name }}</span>
                        <span class="lhb-desk-amt">{{ fmtAmt(d.sell_amt) }}</span>
                      </div>
                      <div v-if="getSellDesks(s.stock_code).length === 0" class="lhb-desk-empty">-</div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <!-- 机构密集信号 -->
      <div class="section-title lhb-section-gap">
        <span class="section-icon">&#9632;</span> 机构密集（≥6席）
        <span class="ct-hint">共 {{ instDenseSignals.length }} 只</span>
      </div>
      <div v-if="instDenseSignals.length === 0" class="lhb-empty">当日无机构密集信号</div>
      <div v-else class="lhb-table-wrap">
        <table class="compare-table">
          <thead>
            <tr>
              <th class="lhb-col-name">股票</th>
              <th>涨跌幅</th>
              <th>机构席位数</th>
              <th>买入金额</th>
              <th>卖出金额</th>
              <th>净买入</th>
              <th class="lhb-col-tags">概念板块</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="s in instDenseSignals" :key="s.stock_code">
              <tr class="ct-clickable" :class="{ 'ct-selected': expandedCode === s.stock_code }" @click="toggleExpand(s.stock_code)">
                <td class="lhb-col-name">
                  <span class="ct-name">{{ s.stock_name }}</span>
                  <span class="lhb-code">{{ s.stock_code }}</span>
                  <button class="lhb-expand-btn" :class="{ active: expandedCode === s.stock_code }" title="查看营业部明细">&#9654;</button>
                </td>
                <td :class="changeClass(s.change_rate)">{{ fmtChange(s.change_rate) }}</td>
                <td class="lhb-inst-count">{{ s.inst_count }}席</td>
                <td>{{ fmtAmt(s.buy_amt) }}</td>
                <td>{{ fmtAmt(s.sell_amt) }}</td>
                <td :class="(s.net_amt ?? 0) >= 0 ? 'ct-up' : 'ct-down'" class="lhb-bold">
                  {{ fmtAmt(s.net_amt) }}
                </td>
                <td class="lhb-col-tags">
                  <span v-for="tag in (s.concept_tags || []).slice(0, 3)" :key="tag" class="tag">{{ tag }}</span>
                </td>
              </tr>
              <!-- 展开的营业部明细 -->
              <tr v-if="expandedCode === s.stock_code" class="lhb-desk-row">
                <td colspan="7" class="lhb-desk-cell">
                  <div class="lhb-desk-wrap">
                    <div class="lhb-desk-side">
                      <div class="lhb-desk-title lhb-buy-title">买入营业部</div>
                      <div v-for="d in getBuyDesks(s.stock_code)" :key="d.dept_name" class="lhb-desk-item">
                        <span class="lhb-desk-name">{{ d.dept_name }}</span>
                        <span class="lhb-desk-amt">{{ fmtAmt(d.buy_amt) }}</span>
                      </div>
                      <div v-if="getBuyDesks(s.stock_code).length === 0" class="lhb-desk-empty">-</div>
                    </div>
                    <div class="lhb-desk-side">
                      <div class="lhb-desk-title lhb-sell-title">卖出营业部</div>
                      <div v-for="d in getSellDesks(s.stock_code)" :key="d.dept_name" class="lhb-desk-item">
                        <span class="lhb-desk-name">{{ d.dept_name }}</span>
                        <span class="lhb-desk-amt">{{ fmtAmt(d.sell_amt) }}</span>
                      </div>
                      <div v-if="getSellDesks(s.stock_code).length === 0" class="lhb-desk-empty">-</div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </template>

    <!-- 板块分析 -->
    <template v-if="analysis">
      <div class="section-title lhb-section-gap">
        <span class="section-icon">&#9670;</span> 板块分析（近{{ analysisMonths }}个月）
        <span class="ct-hint">共 {{ analysis.total_signals }} 条信号</span>
      </div>
      <div class="lhb-analysis-controls">
        <select v-model="analysisMonths" class="lhb-select" @change="loadAnalysis">
          <option :value="1">近1个月</option>
          <option :value="3">近3个月</option>
          <option :value="6">近6个月</option>
        </select>
      </div>
      <div class="charts-row">
        <ChartBox ref="sectorChartRef" title="概念板块出现频率 Top 15" />
      </div>
      <div v-if="analysis.sector_distribution.length === 0" class="lhb-empty">暂无板块分析数据</div>
      <div v-else class="lhb-table-wrap">
        <table class="compare-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>概念板块</th>
              <th>出现次数</th>
              <th>平均涨跌幅</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, i) in analysis.sector_distribution" :key="item.sector">
              <td class="lhb-rank">{{ i + 1 }}</td>
              <td class="ct-name">{{ item.sector }}</td>
              <td>{{ item.count }}</td>
              <td :class="changeClass(item.avg_change)">{{ fmtChange(item.avg_change) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
