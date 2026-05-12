<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import type { LhbSignal, LhbAnalysis, LhbTradingDesk, LhbPoolItem } from '../types'
import { useApi } from '../composables/useApi'
import { CHART_COLORS, CHART_BASE } from '../charts/theme'
import ChartBox from '../components/ChartBox.vue'
import * as echarts from 'echarts'

const api = useApi()

type LhbTab = 'foreign' | 'inst_dense' | 'analysis' | 'pool'
const activeTab = ref<LhbTab>('foreign')

const signalDates = ref<string[]>([])
const selectedDate = ref('')
const signals = ref<LhbSignal[]>([])
const analysis = ref<LhbAnalysis | null>(null)
const loading = ref(false)
const crawling = ref(false)
const crawlStart = ref('')
const crawlEnd = ref('')
const toastMsg = ref('')
const toastVisible = ref(false)
const analysisMonths = ref(3)
const sectorChartRef = ref()

const poolData = ref<LhbPoolItem[]>([])
const poolLoading = ref(false)
const poolUpdating = ref(false)
const poolFilter = ref<'all' | 'foreign' | 'inst_dense'>('all')
const poolSearch = ref('')
const poolSortKey = ref<string>('signal_date')
const poolSortAsc = ref(false)

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
  if (Math.abs(val) >= 1e8) return (val / 1e8).toFixed(2) + '亿'
  if (Math.abs(val) >= 1e4) return (val / 1e4).toFixed(2) + '万'
  return val.toFixed(0) + '元'
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
  } catch (e) {
    console.error('板块分析加载失败:', e)
    analysis.value = null
  }
}

async function handleCrawl() {
  if (!crawlStart.value) {
    showToast('请选择开始日期')
    return
  }
  const end = crawlEnd.value || crawlStart.value
  crawling.value = true
  try {
    const res = await api.crawlLhbBatch(crawlStart.value, end)
    showToast(res.message)
    await loadDates()
    const target = signalDates.value.find(d => d <= end) || signalDates.value[0] || ''
    if (target) {
      selectedDate.value = target
      await loadSignals()
    }
  } catch (e: unknown) {
    showToast('抓取失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    crawling.value = false
  }
}

function renderSectorChart() {
  if (!sectorChartRef.value || !analysis.value) return
  const data = analysis.value.sector_distribution.slice(0, 15)

  sectorChartRef.value.setOption({
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

// ---- 股池 ----

function currentChange(p: LhbPoolItem): number | null {
  if (p.entry_price && p.latest_price) {
    return (p.latest_price - p.entry_price) / p.entry_price * 100
  }
  return null
}

const filteredPool = computed(() => {
  let list = poolData.value
  if (poolFilter.value !== 'all') {
    list = list.filter(p => p.signal_types.includes(poolFilter.value))
  }
  if (poolSearch.value.trim()) {
    const q = poolSearch.value.trim().toLowerCase()
    list = list.filter(p =>
      p.stock_name.toLowerCase().includes(q) ||
      p.stock_code.includes(q)
    )
  }
  const key = poolSortKey.value
  const dir = poolSortAsc.value ? 1 : -1
  return [...list].sort((a, b) => {
    let va: number | string
    let vb: number | string
    if (key === 'current_change') {
      va = currentChange(a) ?? -Infinity
      vb = currentChange(b) ?? -Infinity
    } else {
      const k = key as keyof LhbPoolItem
      va = (a[k] ?? '') as number | string
      vb = (b[k] ?? '') as number | string
    }
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
    return String(va).localeCompare(String(vb)) * dir
  })
})

function toggleSort(key: string) {
  if (poolSortKey.value === key) {
    poolSortAsc.value = !poolSortAsc.value
  } else {
    poolSortKey.value = key
    poolSortAsc.value = key === 'signal_date'
  }
}

function sortIndicator(key: string): string {
  if (poolSortKey.value !== key) return ''
  return poolSortAsc.value ? ' ↑' : ' ↓'
}

async function loadPool() {
  poolLoading.value = true
  try {
    poolData.value = await api.fetchLhbPool()
  } catch {
    poolData.value = []
  }
  poolLoading.value = false
}

async function handleUpdatePool() {
  poolUpdating.value = true
  try {
    const res = await api.updateLhbPool()
    showToast(res.message)
    await loadPool()
  } catch (e: unknown) {
    showToast('更新失败: ' + (e instanceof Error ? e.message : String(e)))
  } finally {
    poolUpdating.value = false
  }
}

function signalTypeLabel(types: string): string {
  const parts = types.split(',')
  const labels: string[] = []
  if (parts.includes('foreign')) labels.push('境外')
  if (parts.includes('inst_dense')) labels.push('密集')
  return labels.join('+')
}

function signalTypeClass(types: string): string {
  if (types.includes('foreign') && types.includes('inst_dense')) return 'lhb-tag-both'
  if (types.includes('foreign')) return 'lhb-tag-foreign'
  return 'lhb-tag-inst'
}

async function init() {
  await loadDates()
  if (selectedDate.value) await loadSignals()
  await loadAnalysis()
  await loadPool()
}

watch(activeTab, (tab) => {
  if (tab === 'analysis') {
    nextTick(() => renderSectorChart())
  }
})

init()
</script>

<template>
  <div>
    <div class="lhb-header">
      <div class="lhb-controls">
        <input type="date" v-model="crawlStart" class="lhb-date-input" />
        <span class="lhb-sep">~</span>
        <input type="date" v-model="crawlEnd" class="lhb-date-input" />
        <button class="btn btn-primary" :disabled="crawling" @click="handleCrawl">
          {{ crawling ? '抓取中...' : '抓取' }}
        </button>
        <span class="lhb-divider">|</span>
        <select v-model="selectedDate" class="lhb-select" @change="loadSignals">
          <option value="">选择日期</option>
          <option v-for="d in signalDates" :key="d" :value="d">{{ d }}</option>
        </select>
      </div>
      <div v-if="toastVisible" class="toast-inline">{{ toastMsg }}</div>
    </div>

    <!-- 子 Tab 切换 -->
    <div class="lhb-tabs">
      <button
        v-for="t in ([
          { key: 'foreign', label: '境外机构', count: foreignSignals.length },
          { key: 'inst_dense', label: '机构密集', count: instDenseSignals.length },
          { key: 'analysis', label: '板块分析', count: null },
          { key: 'pool', label: '股池', count: filteredPool.length },
        ] as const)"
        :key="t.key"
        class="lhb-tab"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        {{ t.label }}
        <span v-if="t.count !== null" class="lhb-tab-count">{{ t.count }}</span>
      </button>
    </div>

    <div v-if="loading" class="lhb-loading">加载中...</div>

    <!-- 境外机构 -->
    <template v-if="!loading && activeTab === 'foreign' && selectedDate">
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
                <td :class="(s.net_amt ?? 0) >= 0 ? 'ct-up' : 'ct-down'" class="lhb-bold">{{ fmtAmt(s.net_amt) }}</td>
                <td class="lhb-col-tags">
                  <span v-for="tag in (s.concept_tags || []).slice(0, 3)" :key="tag" class="tag">{{ tag }}</span>
                </td>
              </tr>
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
    </template>

    <!-- 机构密集 -->
    <template v-if="!loading && activeTab === 'inst_dense' && selectedDate">
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
                <td :class="(s.net_amt ?? 0) >= 0 ? 'ct-up' : 'ct-down'" class="lhb-bold">{{ fmtAmt(s.net_amt) }}</td>
                <td class="lhb-col-tags">
                  <span v-for="tag in (s.concept_tags || []).slice(0, 3)" :key="tag" class="tag">{{ tag }}</span>
                </td>
              </tr>
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
    <template v-if="activeTab === 'analysis' && analysis">
      <div class="lhb-analysis-controls">
        <select v-model="analysisMonths" class="lhb-select" @change="loadAnalysis">
          <option :value="1">近1个月</option>
          <option :value="3">近3个月</option>
          <option :value="6">近6个月</option>
        </select>
        <span class="ct-hint">共 {{ analysis.total_signals }} 条信号</span>
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

    <!-- 龙虎榜股池 -->
    <template v-if="activeTab === 'pool'">
      <div class="lhb-analysis-controls">
        <input v-model="poolSearch" class="lhb-search" placeholder="搜索股票名称/代码" />
        <select v-model="poolFilter" class="lhb-select">
          <option value="all">全部信号</option>
          <option value="foreign">境外机构</option>
          <option value="inst_dense">机构密集</option>
        </select>
        <button class="btn btn-outline btn-sm" :disabled="poolUpdating" @click="handleUpdatePool">
          {{ poolUpdating ? '更新中...' : '更新数据' }}
        </button>
      </div>
      <div v-if="poolLoading" class="lhb-loading">加载中...</div>
      <div v-else-if="filteredPool.length === 0" class="lhb-empty">暂无股池数据</div>
      <div v-else class="lhb-table-wrap">
        <table class="compare-table lhb-pool-table">
          <thead>
            <tr>
              <th class="lhb-sort-th" @click="toggleSort('stock_name')">股票{{ sortIndicator('stock_name') }}</th>
              <th>信号</th>
              <th class="lhb-sort-th" @click="toggleSort('signal_date')">入选日{{ sortIndicator('signal_date') }}</th>
              <th class="lhb-sort-th" @click="toggleSort('entry_price')">入选价{{ sortIndicator('entry_price') }}</th>
              <th class="lhb-sort-th" @click="toggleSort('latest_price')">当前价{{ sortIndicator('latest_price') }}</th>
              <th class="lhb-sort-th" @click="toggleSort('current_change')">当前涨幅{{ sortIndicator('current_change') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in filteredPool" :key="p.stock_code">
              <td class="lhb-col-name">
                <span class="ct-name">{{ p.stock_name }}</span>
                <span class="lhb-code">{{ p.stock_code }}</span>
              </td>
              <td class="lhb-signal-tag" :class="signalTypeClass(p.signal_types)">
                {{ signalTypeLabel(p.signal_types) }}
              </td>
              <td>{{ p.signal_date }}</td>
              <td class="lhb-mono">{{ p.entry_price?.toFixed(2) ?? '-' }}</td>
              <td class="lhb-mono">{{ p.latest_price?.toFixed(2) ?? '-' }}</td>
              <td :class="changeClass(currentChange(p))" class="lhb-bold">{{ fmtChange(currentChange(p)) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
