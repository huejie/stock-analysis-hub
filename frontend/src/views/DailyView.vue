<script setup lang="ts">
import { ref, inject, type Ref } from 'vue'
import type { StockRecord, DailyStatsResponse } from '../types'
import { useApi } from '../composables/useApi'
import { buildChangeChartOption, buildHoldersChartOption } from '../charts/daily'
import ChartBox from '../components/ChartBox.vue'
import StockCard from '../components/StockCard.vue'
import EmptyState from '../components/EmptyState.vue'

const api = useApi()
const isAdmin = inject<Ref<boolean>>('isAdmin', ref(false))

const date = ref(new Date().toISOString().slice(0, 10))
const availableDates = ref<string[]>([])
const data = ref<DailyStatsResponse | null>(null)
const loading = ref(false)

const changeChartRef = ref()
const holdersChartRef = ref()

function getRankChange(code: string, today: StockRecord[], prev: StockRecord[]): string {
  const t = today.find(r => r.stock_code === code)
  const p = prev.find(r => r.stock_code === code)
  if (!t || !p) return ''
  const diff = p.rank - t.rank
  if (diff === 0) return ''
  if (diff > 0) return `<span class="badge badge-up">&#9650;${diff}</span>`
  return `<span class="badge badge-down">&#9660;${Math.abs(diff)}</span>`
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

async function init() {
  await loadDates()
  await load()
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
    <!-- Date navigation (only for daily view) -->
    <div v-if="data" class="date-group">
      <button class="btn-icon" title="前一天" @click="prevDate">&#9664;</button>
      <input v-model="date" type="date" @change="load">
      <button class="btn-icon" title="后一天" @click="nextDate">&#9654;</button>
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
                <div class="name">{{ r.stock_name }}</div>
                <div class="code">{{ r.stock_code }}</div>
              </div>
              <div class="heat">{{ r.heat_value }}w</div>
            </div>
            <div class="change" :class="r.price_change_pct! >= 0 ? 'change-up' : 'change-down'">
              {{ r.price_change_pct! >= 0 ? '+' : '' }}{{ r.price_change_pct }}%
            </div>
            <div class="tags">
              <span v-for="tag in r.sector_tags" :key="tag" class="tag">{{ tag }}</span>
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
