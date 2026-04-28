import type { EChartsOption } from 'echarts'
import type { SeasonDailyStat } from '../types'
import { CHART_COLORS, CHART_BASE, fmtShort } from './theme'

export function buildPnlChartOption(rows: SeasonDailyStat[]): EChartsOption {
  const dates = rows.map(r => fmtShort(r.date))
  const pnl = rows.map(r => r.per_capital_pnl ?? 0)
  const positions = rows.map(r => r.per_capital_position)

  return {
    ...CHART_BASE,
    grid: { left: 60, right: 60, top: 40, bottom: 30, containLabel: false },
    legend: {
      data: ['每日盈亏', '人均仓位'], top: 0,
      textStyle: { color: CHART_COLORS.text, fontSize: 13 },
      itemWidth: 14, itemHeight: 3, itemGap: 20,
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111622', borderColor: '#1e293b',
      textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (params: unknown) => {
        const ps = params as Array<{ axisValue: string; seriesName: string; value: number; marker: string }>
        const daily = ps.find(p => p.seriesName === '每日盈亏')
        const pos = ps.find(p => p.seriesName === '人均仓位')
        let s = `${ps[0]!.axisValue}<br/>`
        if (daily) {
          const c = daily.value >= 0 ? '#ef4444' : '#10b981'
          s += `${daily.marker} 当日: <span style="color:${c}">${daily.value >= 0 ? '+' : ''}${daily.value}%</span><br/>`
        }
        if (pos) {
          s += `${pos.marker} 仓位: <span style="color:#06b6d4">${pos.value}%</span>`
        }
        return s
      },
    },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { color: CHART_COLORS.textMuted, rotate: 30, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '盈亏%',
        nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
        splitLine: { lineStyle: { color: CHART_COLORS.line } },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13, formatter: '{value}%' },
      },
      {
        type: 'value', name: '仓位%', min: 60,
        nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
        splitLine: { show: false },
        axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13, formatter: '{value}%' },
      },
    ],
    series: [
      {
        name: '每日盈亏', type: 'line', yAxisIndex: 0,
        data: pnl,
        lineStyle: { width: 2, color: '#94a3b8' },
        itemStyle: { color: (p: unknown) => ((p as { value: number }).value >= 0 ? '#ef4444' : '#10b981') },
        symbolSize: 5, symbol: 'circle',
        emphasis: { focus: 'series', lineStyle: { width: 4 }, itemStyle: { borderWidth: 2, borderColor: '#fff' } },
        markLine: {
          silent: true, data: [{ yAxis: 0 }],
          lineStyle: { color: '#475569', type: 'solid', width: 1 },
          label: { show: false },
        },
      },
      {
        name: '人均仓位', type: 'line', yAxisIndex: 1,
        data: positions,
        lineStyle: { width: 2.5, color: CHART_COLORS.cyan },
        itemStyle: { color: CHART_COLORS.cyan },
        symbolSize: 5, symbol: 'circle',
        emphasis: { focus: 'series', lineStyle: { width: 4 }, itemStyle: { borderWidth: 2, borderColor: '#fff' } },
        areaStyle: {
          opacity: 0.08,
          color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [{ offset: 0, color: CHART_COLORS.cyan }, { offset: 1, color: 'transparent' }],
          },
        },
      },
    ] as unknown as EChartsOption['series'],
    animationEasing: 'cubicOut',
  }
}

export function buildPositionChartOption(rows: SeasonDailyStat[]): EChartsOption {
  const dates = rows.map(r => fmtShort(r.date))
  const pnl = rows.map(r => r.per_capital_pnl ?? 0)
  let cum = 0
  const cumPnl = pnl.map(v => { cum += v; return Math.round(cum * 100) / 100 })

  return {
    ...CHART_BASE,
    grid: { left: 60, right: 20, top: 30, bottom: 30, containLabel: false },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#111622', borderColor: '#1e293b',
      textStyle: { color: '#f1f5f9', fontSize: 13 },
      formatter: (params: unknown) => {
        const ps = params as Array<{ axisValue: string; seriesName: string; value: number; marker: string }>
        const cum = ps.find(p => p.seriesName === '累计盈亏')
        let s = `${ps[0]!.axisValue}<br/>`
        if (cum) {
          const c = cum.value >= 0 ? '#f59e0b' : '#06b6d4'
          s += `${cum.marker} 累计: <span style="color:${c}">${cum.value >= 0 ? '+' : ''}${cum.value}%</span>`
        }
        return s
      },
    },
    xAxis: {
      type: 'category', data: dates,
      axisLabel: { color: CHART_COLORS.textMuted, rotate: 30, fontSize: 13 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '累计%',
      nameTextStyle: { color: CHART_COLORS.textMuted, fontSize: 13 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 13, formatter: '{value}%' },
    },
    series: [{
      name: '累计盈亏', type: 'line',
      data: cumPnl,
      lineStyle: { width: 2.5, color: '#f59e0b' },
      itemStyle: { color: '#f59e0b' },
      symbolSize: 5, symbol: 'circle',
      areaStyle: {
        opacity: 0.12,
        color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: '#f59e0b' }, { offset: 1, color: 'transparent' }],
        },
      },
      markLine: {
        silent: true, data: [{ yAxis: 0 }],
        lineStyle: { color: '#475569', type: 'dashed', width: 1 },
        label: { show: false },
      },
    }],
    animationEasing: 'cubicOut',
  }
}
