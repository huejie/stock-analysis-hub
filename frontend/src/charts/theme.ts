export const CHART_COLORS = {
  text: '#dce4ec',
  textMuted: '#b0c0d2',
  line: '#1e293b',
  accent: '#3b82f6',
  red: '#ef4444',
  green: '#10b981',
  amber: '#f59e0b',
  purple: '#8b5cf6',
  cyan: '#06b6d4',
}

export const CHART_BASE = {
  backgroundColor: 'transparent',
  grid: { left: 70, right: 24, top: 20, bottom: 30, containLabel: false },
  textStyle: { fontFamily: "'DM Sans', 'PingFang SC', sans-serif", fontSize: 13 },
}

export function fmtShort(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const parts = dateStr.split('-')
  return `${parseInt(parts[1]!)}/${parseInt(parts[2]!)}`
}

export function fmtDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
