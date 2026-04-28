import type { EChartsOption } from 'echarts'
import type { StockRecord } from '../types'
import { CHART_COLORS, CHART_BASE } from './theme'

export function buildChangeChartOption(records: StockRecord[]): EChartsOption {
  const sorted = [...records].sort((a, b) => (a.price_change_pct ?? 0) - (b.price_change_pct ?? 0))
  return {
    ...CHART_BASE,
    xAxis: {
      type: 'value',
      axisLabel: { color: CHART_COLORS.textMuted, formatter: '{value}%', fontSize: 12 },
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
    },
    yAxis: {
      type: 'category',
      data: sorted.map(r => r.stock_name),
      inverse: true,
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.text, fontSize: 14, fontWeight: 600 },
    },
    series: [{
      type: 'bar',
      data: sorted.map(r => ({
        value: r.price_change_pct ?? 0,
        itemStyle: {
          color: (r.price_change_pct ?? 0) >= 0 ? CHART_COLORS.red : CHART_COLORS.green,
          borderRadius: (r.price_change_pct ?? 0) >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4],
        },
      })),
      barWidth: '60%',
      label: {
        show: true, position: 'right',
        formatter: (p) => ((p.value as number) >= 0 ? '+' : '') + p.value + '%',
        fontSize: 13, fontFamily: "'JetBrains Mono', monospace",
        color: CHART_COLORS.text,
      },
    }],
    animationEasing: 'cubicOut',
  }
}

export function buildHoldersChartOption(records: StockRecord[]): EChartsOption {
  return {
    ...CHART_BASE,
    grid: { left: 70, right: 16, top: 40, bottom: 30, containLabel: false },
    legend: {
      data: ['今日', '昨日'], top: 0,
      textStyle: { color: CHART_COLORS.text, fontSize: 13 },
      itemWidth: 12, itemHeight: 8, itemGap: 16,
    },
    xAxis: {
      type: 'category', data: records.map(r => r.stock_name),
      axisLabel: { color: CHART_COLORS.textMuted, rotate: 25, fontSize: 12 },
      axisLine: { lineStyle: { color: CHART_COLORS.line } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: CHART_COLORS.line } },
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { color: CHART_COLORS.textMuted, fontSize: 12 },
    },
    series: [
      {
        name: '今日', type: 'bar',
        data: records.map(r => r.holders_today),
        itemStyle: { color: CHART_COLORS.accent, borderRadius: [3, 3, 0, 0] },
        barGap: '20%',
      },
      {
        name: '昨日', type: 'bar',
        data: records.map(r => r.holders_yesterday),
        itemStyle: { color: '#334155', borderRadius: [3, 3, 0, 0] },
      },
    ],
    animationEasing: 'cubicOut',
  }
}
