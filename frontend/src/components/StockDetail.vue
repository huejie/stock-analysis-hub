<script setup lang="ts">
import { ref, watch, nextTick, onUnmounted } from 'vue'
import type { StockDetailResponse } from '../types'
import { useApi } from '../composables/useApi'
import { CHART_COLORS, CHART_BASE } from '../charts/theme'
import * as echarts from 'echarts'

const props = defineProps<{
  stockCode: string
  stockName: string
}>()

const emit = defineEmits<{
  close: []
}>()

const api = useApi()
const data = ref<StockDetailResponse | null>(null)
const loading = ref(false)
const chartEl = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

async function load() {
  loading.value = true
  try {
    data.value = await api.fetchStockHistory(props.stockCode)
    await nextTick()
    renderChart()
  } catch {
    data.value = null
  }
  loading.value = false
}

function fmtAmt(val: number | null): string {
  if (val == null) return '-'
  if (Math.abs(val) >= 1e8) return (val / 1e8).toFixed(2) + '亿'
  if (Math.abs(val) >= 1e4) return (val / 1e4).toFixed(2) + '万'
  return val.toFixed(0) + '元'
}

function fmtChange(val: number | null): string {
  if (val == null) return '-'
  return (val >= 0 ? '+' : '') + val.toFixed(2) + '%'
}

function changeClass(val: number | null): string {
  if (val == null) return ''
  return val >= 0 ? 'ct-up' : 'ct-down'
}

function signalTypeLabel(type: string): string {
  return type === 'foreign' ? '境外机构' : '机构密集'
}

function renderChart() {
  if (!chartEl.value || !data.value || data.value.records.length === 0) return
  if (!chart || chart.isDisposed()) {
    chart = echarts.init(chartEl.value)
  }
  const records = [...data.value.records].reverse()
  const labels = records.map(r => r.date.slice(5))
  const ranks = records.map(r => r.rank)
  const changes = records.map(r => r.price_change_pct)

  chart.setOption({
    ...CHART_BASE,
    title: {
      text: `${props.stockName} 热榜排名趋势`,
      left: 12, top: 8,
      textStyle: { color: CHART_COLORS.text, fontSize: 14, fontWeight: 700 },
    },
    tooltip: {
      trigger: 'axis', backgroundColor: '#1a2236', borderColor: '#243044',
      textStyle: { color: '#dce4ec', fontSize: 12 },
    },
    legend: {
      data: ['排名', '涨跌幅'], top: 0, left: 'center',
      textStyle: { color: CHART_COLORS.text, fontSize: 12 }, itemWidth: 12, itemHeight: 8,
    },
    grid: { left: 50, right: 50, top: 56, bottom: 28 },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 11 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
    },
    yAxis: [
      {
        type: 'value', name: '排名', inverse: true, min: 1,
        axisLabel: { color: CHART_COLORS.accent, fontSize: 11 },
        splitLine: { lineStyle: { color: CHART_COLORS.line } },
      },
      {
        type: 'value', name: '涨跌幅%', position: 'right',
        axisLabel: { color: CHART_COLORS.red, fontSize: 11, formatter: '{value}%' },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '排名', type: 'line', yAxisIndex: 0, data: ranks,
        lineStyle: { color: CHART_COLORS.accent, width: 2 },
        itemStyle: { color: CHART_COLORS.accent },
        symbol: 'circle', symbolSize: 6,
      },
      {
        name: '涨跌幅', type: 'bar', yAxisIndex: 1, data: changes,
        itemStyle: {
          color: (params: any) => (params.value ?? 0) >= 0 ? '#ef4444' : '#22c55e',
        },
      },
    ],
  })
}

onUnmounted(() => { chart?.dispose(); chart = null })

watch(() => props.stockCode, () => { if (props.stockCode) load() }, { immediate: true })
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal-content">
      <div class="modal-header">
        <div>
          <span class="modal-title">{{ stockName }}</span>
          <span class="lhb-code">{{ stockCode }}</span>
        </div>
        <button class="modal-close" @click="emit('close')">&#10005;</button>
      </div>

      <div v-if="loading" class="lhb-loading">加载中...</div>
      <div v-else-if="!data" class="lhb-empty">加载失败</div>
      <template v-else>
        <div v-if="data.records.length > 0 && data.records[0]!.sector_tags.length > 0" class="modal-tags">
          <span v-for="tag in data.records[0]!.sector_tags" :key="tag" class="tag">{{ tag }}</span>
        </div>

        <div v-if="data.records.length > 0" class="modal-chart" ref="chartEl"></div>

        <template v-if="data.lhb_signals.length > 0">
          <div class="section-title" style="margin-top:16px">
            <span class="section-icon">&#9670;</span> 龙虎榜信号
          </div>
          <table class="compare-table">
            <thead>
              <tr><th>日期</th><th>信号类型</th><th>涨跌幅</th><th>净买入</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in data.lhb_signals" :key="s.date + s.signal_type">
                <td>{{ s.date }}</td>
                <td>
                  <span class="lhb-signal-tag" :class="s.signal_type === 'foreign' ? 'lhb-tag-foreign' : 'lhb-tag-inst'">
                    {{ signalTypeLabel(s.signal_type) }}
                  </span>
                </td>
                <td :class="changeClass(s.change_rate)">{{ fmtChange(s.change_rate) }}</td>
                <td :class="(s.net_amt ?? 0) >= 0 ? 'ct-up' : 'ct-down'" class="lhb-bold">{{ fmtAmt(s.net_amt) }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex; justify-content: center; align-items: flex-start;
  padding-top: 60px; z-index: 1000;
}
.modal-content {
  background: #0f1729; border: 1px solid #243044; border-radius: 12px;
  width: 90%; max-width: 900px; max-height: 80vh; overflow-y: auto; padding: 20px;
}
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.modal-title { font-size: 1.3em; font-weight: 700; color: #e2e8f0; }
.modal-close { background: none; border: none; color: #64748b; font-size: 1.2em; cursor: pointer; padding: 4px 8px; }
.modal-close:hover { color: #e2e8f0; }
.modal-tags { margin-bottom: 12px; }
.modal-chart { height: 300px; margin-bottom: 12px; }
</style>
