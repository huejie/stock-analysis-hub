import type { EChartsOption } from 'echarts'
import type { StockRecord } from '../types'
import { CHART_COLORS, CHART_BASE, fmtShort } from './theme'

const PALETTE = [CHART_COLORS.accent, CHART_COLORS.amber, CHART_COLORS.red, CHART_COLORS.green, CHART_COLORS.cyan]

function makeGradient(c1: string, c2: string) {
  return { type: 'linear' as const, x: 0, y: 0, x2: 1, y2: 0, colorStops: [{ offset: 0, color: c1 }, { offset: 1, color: c2 }] }
}

export function buildSectorChartOption(records: StockRecord[]): EChartsOption {
  const count: Record<string, number> = {}
  records.forEach(r => (r.sector_tags || []).forEach(t => { count[t] = (count[t] || 0) + 1 }))
  const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10)
  return {
    ...CHART_BASE,
    xAxis: {
      type: 'value', splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 12 },
    },
    yAxis: {
      type: 'category', data: sorted.map(s => s[0]).reverse(),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text, fontSize: 13 },
    },
    series: [{
      type: 'bar', data: sorted.map(s => s[1]).reverse(),
      itemStyle: { color: makeGradient(CHART_COLORS.accent, CHART_COLORS.purple), borderRadius: [0, 4, 4, 0] },
      barWidth: '60%',
      label: { show: true, position: 'right', color: CHART_COLORS.text, fontSize: 12 },
    }],
    animationEasing: 'cubicOut',
  }
}

export function buildFrequencyChartOption(records: StockRecord[]): EChartsOption {
  const count: Record<string, number> = {}
  records.forEach(r => { count[r.stock_name] = (count[r.stock_name] || 0) + 1 })
  const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10)
  return {
    ...CHART_BASE,
    xAxis: {
      type: 'value', splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 12 },
    },
    yAxis: {
      type: 'category', data: sorted.map(s => s[0]).reverse(),
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text, fontSize: 13 },
    },
    series: [{
      type: 'bar', data: sorted.map(s => s[1]).reverse(),
      itemStyle: { color: makeGradient(CHART_COLORS.amber, '#ef4444'), borderRadius: [0, 4, 4, 0] },
      barWidth: '60%',
      label: { show: true, position: 'right', color: CHART_COLORS.text, fontSize: 12 },
    }],
    animationEasing: 'cubicOut',
  }
}

export function buildTrendChartOption(records: StockRecord[]): EChartsOption {
  const stockCount: Record<string, number> = {}
  records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1 })
  const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0])
  const dates = [...new Set(records.map(r => r.date))].sort()
  return {
    ...CHART_BASE,
    grid: { left: 60, right: 20, top: 40, bottom: 30, containLabel: false },
    legend: { data: top5, top: 0, textStyle: { color: CHART_COLORS.text, fontSize: 13 }, itemWidth: 14, itemHeight: 3, itemGap: 16 },
    xAxis: {
      type: 'category', data: dates.map(fmtShort),
      axisLabel: { color: CHART_COLORS.textMuted, rotate: 30, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '热度(万)', nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } }, axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13 },
    },
    tooltip: { trigger: 'axis', backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 } },
    series: top5.map((name, i) => ({
      name, type: 'line' as const,
      data: dates.map(d => { const r = records.find(r => r.date === d && r.stock_name === name); return r ? r.heat_value : null }),
      connectNulls: true,
      lineStyle: { width: 2.5, color: PALETTE[i] },
      itemStyle: { color: PALETTE[i] },
      areaStyle: {
        opacity: 0.06,
        color: { type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: PALETTE[i]! }, { offset: 1, color: 'transparent' }] },
      },
    })),
    animationEasing: 'cubicOut',
  }
}

export function buildHoldersTrendChartOption(records: StockRecord[]): EChartsOption {
  const stockCount: Record<string, number> = {}
  records.forEach(r => { stockCount[r.stock_name] = (stockCount[r.stock_name] || 0) + 1 })
  const top5 = Object.entries(stockCount).sort((a, b) => b[1] - a[1]).slice(0, 5).map(s => s[0])
  const dates = [...new Set(records.map(r => r.date))].sort()
  return {
    ...CHART_BASE,
    grid: { left: 60, right: 20, top: 40, bottom: 30, containLabel: false },
    legend: { data: top5, top: 0, textStyle: { color: CHART_COLORS.text, fontSize: 13 }, itemWidth: 14, itemHeight: 3, itemGap: 16 },
    xAxis: {
      type: 'category', data: dates.map(fmtShort),
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '持仓人数', nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } }, axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13 },
    },
    tooltip: { trigger: 'axis', backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 } },
    series: top5.map((name, i) => ({
      name, type: 'line' as const,
      data: dates.map(d => { const r = records.find(r => r.date === d && r.stock_name === name); return r ? r.holders_today : null }),
      connectNulls: true,
      lineStyle: { width: 2.5, color: PALETTE[i] },
      itemStyle: { color: PALETTE[i] },
      symbolSize: 5,
    })),
  }
}

export function buildHeatMapChartOption(records: StockRecord[]): EChartsOption {
  const stocks = [...new Set(records.map(r => r.stock_name))]
  const dates = [...new Set(records.map(r => r.date))].sort()
  const heatData = records.map(r => [dates.indexOf(r.date), stocks.indexOf(r.stock_name), r.heat_value || 0])
  return {
    ...CHART_BASE,
    grid: { left: 90, right: 60, top: 10, bottom: 40, containLabel: false },
    xAxis: {
      type: 'category', data: dates.map(fmtShort),
      splitArea: { show: true, areaStyle: { color: ['rgba(30,41,59,0.3)', 'transparent'] } },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 12, rotate: 30 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    yAxis: {
      type: 'category', data: stocks,
      splitArea: { show: true, areaStyle: { color: ['rgba(30,41,59,0.3)', 'transparent'] } },
      axisLabel: { color: CHART_COLORS.text, fontSize: 12 },
      axisLine: { show: false }, axisTick: { show: false },
    },
    visualMap: {
      min: 0, max: 1500, right: 0, top: 'center', orient: 'vertical',
      inRange: { color: ['#0c1018', '#1e40af', '#3b82f6', '#f59e0b', '#ef4444'] },
      textStyle: { color: CHART_COLORS.textMuted, fontSize: 12 },
      itemWidth: 12, itemHeight: 100,
    },
    tooltip: {
      backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 12 },
      formatter: (p: unknown) => {
        const v = (p as { value: number[] }).value
        return `${dates[v[0]!]} - ${stocks[v[1]!]}<br/>热度: ${v[2]}w`
      },
    },
    series: [{
      type: 'heatmap', data: heatData,
      label: {
        show: true,
        formatter: (p: unknown) => { const v = (p as { value: number[] }).value; return v[2] ? v[2] + 'w' : '' },
        fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: '#f1f5f9',
      },
      itemStyle: { borderColor: '#0c1018', borderWidth: 2, borderRadius: 3 },
    }],
  }
}
