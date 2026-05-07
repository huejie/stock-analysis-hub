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

export type ViewTab = 'daily' | 'pnl' | 'range' | 'lhb'
export type PresetDays = 7 | 30 | 60 | 90 | 0 | 'custom'

export interface LhbSignal {
  id?: number
  date: string
  stock_code: string
  stock_name: string
  signal_type: 'foreign' | 'inst_dense'
  close_price: number | null
  change_rate: number | null
  buy_amt: number | null
  sell_amt: number | null
  net_amt: number | null
  inst_count: number | null
  concept_tags: string[]
}

export interface LhbAnalysis {
  start_date: string
  end_date: string
  total_signals: number
  sector_distribution: { sector: string; count: number; avg_change: number | null }[]
  signal_type_stats: Record<string, { count: number; total_net: number }>
}

export interface LhbTradingDesk {
  date: string
  stock_code: string
  stock_name: string
  side: 'buy' | 'sell'
  dept_name: string
  buy_amt: number | null
  sell_amt: number | null
  net_amt: number | null
}
