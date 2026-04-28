export interface StockRecord {
  id?: number
  date: string
  rank: number
  stock_name: string
  stock_code: string
  heat_value: number | null
  price_change_pct: number | null
  turnover_amount: number | null
  holders_today: number | null
  holders_yesterday: number | null
  sector_tags: string[]
  per_capital_pnl?: number | null
  per_capital_position?: number | null
}

export interface DailyStatsResponse {
  date: string
  records: StockRecord[]
  prev_records: StockRecord[]
  summary: {
    total_stocks: number
    avg_holders_change: number
  }
}

export interface SeasonDailyStat {
  date: string
  per_capital_pnl: number | null
  per_capital_position: number | null
}

export interface SaveResponse {
  status: string
  count: number
}

export type ViewTab = 'daily' | 'pnl' | 'range'
export type PresetDays = 7 | 30 | 60 | 90 | 0 | 'custom'
