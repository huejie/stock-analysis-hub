import type { EChartsOption } from 'echarts'
import type { StockRecord } from '../types'
import { CHART_COLORS, CHART_BASE, fmtShort } from './theme'

const PALETTE = [CHART_COLORS.accent, CHART_COLORS.amber, CHART_COLORS.red, CHART_COLORS.green, CHART_COLORS.cyan]
const PALETTE2 = ['#a78bfa', '#f472b6', '#34d399', '#fbbf24', '#60a5fa']

export function buildSectorChartOption(records: StockRecord[]): EChartsOption {
  const count: Record<string, number> = {}
  records.forEach(r => (r.sector_tags || []).forEach(t => { count[t] = (count[t] || 0) + 1 }))
  const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10)
  const data = sorted.map(s => s[1]).reverse()
  const names = sorted.map(s => s[0]).reverse()

  return {
    ...CHART_BASE,
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    xAxis: { show: false },
    yAxis: {
      type: 'category', data: names,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text, fontSize: 13, fontWeight: 500 },
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (p: unknown) => {
        const d = (p as Array<{ name: string; value: number }>)[0]!
        return `${d.name}: <b>${d.value}次</b>`
      },
    },
    series: [{
      type: 'bar', data: data.map((v, i) => ({
        value: v,
        itemStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [
              { offset: 0, color: `${CHART_COLORS.accent}33` },
              { offset: 1, color: CHART_COLORS.accent },
            ],
          },
          borderRadius: [0, 4, 4, 0],
        },
      })),
      barWidth: '55%',
      label: {
        show: true, position: 'right', color: CHART_COLORS.textMuted, fontSize: 12,
        fontFamily: "'JetBrains Mono', monospace",
        formatter: (p: unknown) => `${(p as { value: number }).value}次`,
      },
      showBackground: true,
      backgroundStyle: { color: 'rgba(30,41,59,0.3)', borderRadius: [0, 4, 4, 0] },
    }],
    animationEasing: 'cubicOut',
  }
}

export function buildFrequencyChartOption(records: StockRecord[]): EChartsOption {
  const count: Record<string, number> = {}
  records.forEach(r => { count[r.stock_name] = (count[r.stock_name] || 0) + 1 })
  const sorted = Object.entries(count).sort((a, b) => b[1] - a[1]).slice(0, 10)
  const data = sorted.map(s => s[1]).reverse()
  const names = sorted.map(s => s[0]).reverse()
  const maxVal = Math.max(...data)

  return {
    ...CHART_BASE,
    grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
    xAxis: { show: false },
    yAxis: {
      type: 'category', data: names,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text, fontSize: 13, fontWeight: 500 },
    },
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'shadow' },
      backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (p: unknown) => {
        const d = (p as Array<{ name: string; value: number }>)[0]!
        return `${d.name}: <b>${d.value}次</b>`
      },
    },
    series: [{
      type: 'bar',
      data: data.map(v => ({
        value: v,
        itemStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
            colorStops: [
              { offset: 0, color: `${CHART_COLORS.amber}33` },
              { offset: 1, color: v === maxVal ? '#ef4444' : CHART_COLORS.amber },
            ],
          },
          borderRadius: [0, 4, 4, 0],
        },
      })),
      barWidth: '55%',
      label: {
        show: true, position: 'right', color: CHART_COLORS.textMuted, fontSize: 12,
        fontFamily: "'JetBrains Mono', monospace",
        formatter: (p: unknown) => `${(p as { value: number }).value}次`,
      },
      showBackground: true,
      backgroundStyle: { color: 'rgba(30,41,59,0.3)', borderRadius: [0, 4, 4, 0] },
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
    legend: {
      data: top5, top: 0,
      textStyle: { color: CHART_COLORS.text, fontSize: 13 },
      itemWidth: 14, itemHeight: 3, itemGap: 16,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (params: unknown) => {
        const ps = params as Array<{ axisValue: string; seriesName: string; value: number | null; marker: string }>
        let s = `<b>${ps[0]!.axisValue}</b><br/>`
        for (const p of ps) {
          if (p.value == null) continue
          s += `${p.marker} ${p.seriesName}: <b>${p.value}w</b><br/>`
        }
        return s
      },
    },
    xAxis: {
      type: 'category', data: dates.map(fmtShort),
      axisLabel: { color: CHART_COLORS.textMuted, rotate: 30, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '热度(w)',
      nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13 },
    },
    series: top5.map((name, i) => ({
      name, type: 'line' as const,
      data: dates.map(d => { const r = records.find(r => r.date === d && r.stock_name === name); return r ? r.heat_value : null }),
      connectNulls: true,
      lineStyle: { width: 2.5, color: PALETTE[i] },
      itemStyle: { color: PALETTE[i] },
      symbolSize: 6, symbol: 'circle',
      areaStyle: {
        opacity: 0.08,
        color: { type: 'linear' as const, x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: PALETTE[i]! }, { offset: 1, color: 'transparent' }] },
      },
      emphasis: {
        focus: 'series',
        lineStyle: { width: 4 },
        itemStyle: { borderWidth: 2, borderColor: '#fff' },
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
    legend: {
      data: top5, top: 0,
      textStyle: { color: CHART_COLORS.text, fontSize: 13 },
      itemWidth: 14, itemHeight: 3, itemGap: 16,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111622', borderColor: '#1e293b', textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (params: unknown) => {
        const ps = params as Array<{ axisValue: string; seriesName: string; value: number | null; marker: string }>
        let s = `<b>${ps[0]!.axisValue}</b><br/>`
        for (const p of ps) {
          if (p.value == null) continue
          s += `${p.marker} ${p.seriesName}: <b>${(p.value / 10000).toFixed(1)}万</b>人<br/>`
        }
        return s
      },
    },
    xAxis: {
      type: 'category', data: dates.map(fmtShort),
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } }, axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '持仓(万人)',
      nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13, formatter: (v: number) => `${(v / 10000).toFixed(0)}万` },
    },
    series: top5.map((name, i) => ({
      name, type: 'line' as const,
      data: dates.map(d => { const r = records.find(r => r.date === d && r.stock_name === name); return r ? r.holders_today : null }),
      connectNulls: true,
      lineStyle: { width: 2, color: PALETTE2[i] },
      itemStyle: { color: PALETTE2[i] },
      symbolSize: 4, symbol: 'circle',
      emphasis: {
        focus: 'series',
        lineStyle: { width: 4 },
        itemStyle: { borderWidth: 2, borderColor: '#fff' },
      },
    })),
    animationEasing: 'cubicOut',
  }
}
