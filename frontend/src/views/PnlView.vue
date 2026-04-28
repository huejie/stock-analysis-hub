<script setup lang="ts">
import { ref, inject, type Ref } from 'vue'
import type { SeasonDailyStat, PresetDays } from '../types'
import { useApi } from '../composables/useApi'
import { fmtShort, fmtDate } from '../charts/theme'
import { buildPnlChartOption, buildPositionChartOption } from '../charts/pnl'
import ChartBox from '../components/ChartBox.vue'
import TimeFilter from '../components/TimeFilter.vue'
import EmptyState from '../components/EmptyState.vue'

const api = useApi()
const isAdmin = inject<Ref<boolean>>('isAdmin', ref(false))

const rows = ref<SeasonDailyStat[]>([])
const dateLabel = ref('')
const filterDays = ref<PresetDays>(60)
const pnlStart = ref('')
const pnlEnd = ref('')
const showInputModal = ref(false)
const inputRows = ref<Array<{ date: string; pnl: string; pos: string }>>([{ date: new Date().toISOString().slice(0, 10), pnl: '', pos: '' }])

const pnlChartRef = ref()
const positionChartRef = ref()

const presets = [
  { label: '近30天', days: 30 },
  { label: '近60天', days: 60 },
  { label: '近90天', days: 90 },
  { label: '全部', days: 0 },
  { label: '自定义', days: 'custom' as const },
]

function buildUrl(): string {
  if (filterDays.value === 'custom') {
    if (pnlStart.value && pnlEnd.value)
      return `/api/season-stats?start=${pnlStart.value}&end=${pnlEnd.value}`
    return '/api/season-stats'
  }
  const days = filterDays.value as number
  if (days > 0) {
    const end = fmtDate(new Date())
    const d = new Date(); d.setDate(d.getDate() - days + 1)
    return `/api/season-stats?start=${fmtDate(d)}&end=${end}`
  }
  return '/api/season-stats'
}

async function load() {
  try {
    rows.value = await api.fetchSeasonStats(
      ...(await (async () => {
        const url = new URL(buildUrl(), 'http://localhost')
        return [url.searchParams.get('start') || undefined, url.searchParams.get('end') || undefined] as [string | undefined, string | undefined]
      })())
    )
    if (rows.value.length === 0) { rows.value = []; return }
    dateLabel.value = `${fmtShort(rows.value[0]!.date)} - ${fmtShort(rows.value[rows.value.length - 1]!.date)} (${rows.value.length}天)`
    await new Promise(r => requestAnimationFrame(r))
    pnlChartRef.value?.setOption(buildPnlChartOption(rows.value))
    positionChartRef.value?.setOption(buildPositionChartOption(rows.value))
  } catch {
    rows.value = []
  }
}

function addInputRow() {
  inputRows.value.push({ date: new Date().toISOString().slice(0, 10), pnl: '', pos: '' })
}

function removeInputRow(i: number) {
  inputRows.value.splice(i, 1)
}

async function saveInputRows() {
  const records = inputRows.value
    .filter(r => r.date)
    .map(r => ({
      date: r.date,
      per_capital_pnl: r.pnl ? parseFloat(r.pnl) : null,
      per_capital_position: r.pos ? parseFloat(r.pos) : null,
    }))
  if (records.length === 0) return
  await api.saveSeasonStats(records)
  showInputModal.value = false
  load()
}

load()
</script>

<template>
  <div>
    <div class="section-title">
      <span class="section-icon">&#9670;</span> 盈亏走势
      <span class="section-date">{{ dateLabel }}</span>
      <button v-if="isAdmin" class="btn btn-primary btn-sm" style="margin-left:auto" @click="showInputModal = true; inputRows = [{ date: new Date().toISOString().slice(0, 10), pnl: '', pos: '' }]">录入数据</button>
    </div>

    <TimeFilter v-model="filterDays" :presets="presets" @query="load">
      <input v-model="pnlStart" type="date">
      <span class="range-sep">至</span>
      <input v-model="pnlEnd" type="date">
    </TimeFilter>

    <EmptyState v-if="rows.length === 0" />
    <template v-else>
      <ChartBox ref="pnlChartRef" title="每日盈亏 & 人均仓位" height="400px" />
      <ChartBox ref="positionChartRef" title="累计盈亏" height="360px" />
    </template>

    <!-- PnL Input Modal -->
    <div v-if="showInputModal" class="modal-overlay show">
      <div class="modal" style="max-width:700px">
        <div class="modal-header">
          <h2>录入盈亏数据</h2>
          <span class="modal-count">{{ inputRows.length }} 行</span>
        </div>
        <p class="modal-hint">按日期录入每天的人均盈亏(%)和人均仓位(%)，已有日期会自动覆盖</p>
        <div class="table-wrap">
          <table class="edit-table">
            <thead><tr><th>日期</th><th>人均盈亏(%)</th><th>人均仓位(%)</th><th></th></tr></thead>
            <tbody>
              <tr v-for="(row, i) in inputRows" :key="i">
                <td><input v-model="row.date" type="date"></td>
                <td><input v-model="row.pnl" type="number" step="0.01" placeholder="如 2.35"></td>
                <td><input v-model="row.pos" type="number" step="0.01" placeholder="如 81.95"></td>
                <td><button class="btn-icon" title="删除" @click="removeInputRow(i)">&#10005;</button></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="actions" style="gap:12px">
          <button class="btn btn-outline" @click="addInputRow">+ 添加一行</button>
          <div style="flex:1" />
          <button class="btn btn-outline" @click="showInputModal = false">取消</button>
          <button class="btn btn-primary" @click="saveInputRows">保存</button>
        </div>
      </div>
    </div>
  </div>
</template>
