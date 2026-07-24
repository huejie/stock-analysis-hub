/** 统一格式化(spec §12.7:金额/百分比/价格使用统一格式化组件)。 */

export function formatMoney(amt: number | null | undefined): string {
  if (amt == null) return '-'
  return '¥' + amt.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function formatPercent(ratio: number | null | undefined, digits = 2): string {
  if (ratio == null) return '-'
  return (ratio * 100).toFixed(digits) + '%'
}

export function formatPrice(p: number | null | undefined): string {
  if (p == null) return '-'
  return p.toFixed(2)
}

export function formatQty(q: number | null | undefined): string {
  if (q == null) return '-'
  return q.toLocaleString('zh-CN')
}

/** 红涨绿跌(中国市场习惯),返回 CSS class 名 */
export function priceColorClass(change: number | null | undefined): string {
  if (change == null || change === 0) return 'text-muted'
  return change > 0 ? 'ct-up' : 'ct-down'
}

export function useFormat() {
  return { formatMoney, formatPercent, formatPrice, formatQty, priceColorClass }
}
