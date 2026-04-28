<script setup lang="ts">
import { ref } from 'vue'
import type { StockRecord, PresetDays } from '../types'
import { useApi } from '../composables/useApi'
import { fmtShort, fmtDate } from '../charts/theme'
import {
  buildSectorChartOption,
  buildFrequencyChartOption,
  buildTrendChartOption,
  buildHoldersTrendChartOption,
  buildHeatMapChartOption,
} from '../charts/range'
import ChartBox from '../components/ChartBox.vue'
import TimeFilter from '../components/TimeFilter.vue'
import EmptyState from '../components/EmptyState.vue'

const api = useApi()

const records = ref<StockRecord[]>([])
const dateLabel = ref('')
const filterDays = ref<PresetDays>(30)
const rangeStart = ref('')
const rangeEnd = ref('')

const sectorRef = ref()
const frequencyRef = ref()
const trendRef = ref()
const holdersTrendRef = ref()
const heatMapRef = ref()

const presets = [
  { label: '近7天', days: 7 },
  { label: '近30天', days: 30 },
  { label: '近90天', days: 90 },
  { label: '全部', days: 0 },
  { label: '自定义', days: 'custom' as const },
]

async function load() {
  let start = ''
  let end = ''
  if (filterDays.value === 'custom') {
    start = rangeStart.value
    end = rangeEnd.value
    if (!start || !end) return
  } else if ((filterDays.value as number) > 0) {
    end = fmtDate(new Date())
    const d = new Date(); d.setDate(d.getDate() - (filterDays.value as number) + 1)
    start = fmtDate(d)
  }
  try {
    const res = await api.fetchRangeRecords(start, end)
    res.forEach(r => {
      if (typeof r.sector_tags === 'string') {
        try { r.sector_tags = JSON.parse(r.sector_tags as unknown as string) } catch { r.sector_tags = [] }
      }
    })
    records.value = res
    if (res.length === 0) { records.value = []; return }
    dateLabel.value = `${fmtShort(start)} ~ ${fmtShort(end)} (${res.length}条)`
    await new Promise(r => requestAnimationFrame(r))
    sectorRef.value?.setOption(buildSectorChartOption(res))
    frequencyRef.value?.setOption(buildFrequencyChartOption(res))
    trendRef.value?.setOption(buildTrendChartOption(res))
    holdersTrendRef.value?.setOption(buildHoldersTrendChartOption(res))
    heatMapRef.value?.setOption(buildHeatMapChartOption(res))
  } catch {
    records.value = []
  }
}

load()
</script>

<template>
  <div>
    <div class="section-title">
      <span class="section-icon">&#9670;</span> 时段分析
      <span class="section-date">{{ dateLabel }}</span>
    </div>

    <TimeFilter v-model="filterDays" :presets="presets" @query="load">
      <input v-model="rangeStart" type="date">
      <span class="range-sep">至</span>
      <input v-model="rangeEnd" type="date">
    </TimeFilter>

    <EmptyState v-if="records.length === 0" />
    <template v-else>
      <div class="charts-row">
        <ChartBox ref="sectorRef" title="板块热度 Top 10" />
        <ChartBox ref="frequencyRef" title="个股上榜频次" />
      </div>
      <ChartBox ref="trendRef" title="热度趋势" height="400px" />
      <div class="charts-row">
        <ChartBox ref="holdersTrendRef" title="持仓变化趋势" />
        <ChartBox ref="heatMapRef" title="综合热力图" />
      </div>
    </template>
  </div>
</template>
