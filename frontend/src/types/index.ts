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

export type ViewTab = 'daily' | 'pnl' | 'range' | 'lhb' | 'ai' | 'trading'
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
  seat_index: number
  dept_name: string
  buy_amt: number | null
  sell_amt: number | null
  net_amt: number | null
}

export interface LhbPoolItem {
  signal_date: string
  stock_code: string
  stock_name: string
  signal_types: string
  entry_price: number | null
  concept_tags: string[]
  d1_change: number | null
  d3_change: number | null
  d5_change: number | null
  d10_change: number | null
  d20_change: number | null
  d30_change: number | null
  latest_price: number | null
  latest_date: string | null
  tracking_days: number
}

// 连续上榜统计
export interface StreakItem {
  stock_code: string
  stock_name: string
  streak_days: number
  dates: string[]
  ranks: number[]
  heat_values: (number | null)[]
  rank_trend: 'rising' | 'stable' | 'falling'
  is_dark_horse: boolean
  latest_change: number | null
  sector_tags: string[]
}

export interface StreakResponse {
  start_date: string
  end_date: string
  streaks: StreakItem[]
}

// 个股详情
export interface StockHistoryRecord {
  date: string
  rank: number
  heat_value: number | null
  price_change_pct: number | null
  turnover_amount: number | null
  holders_today: number | null
  sector_tags: string[]
}

export interface StockLhbSignal {
  date: string
  signal_type: string
  close_price: number | null
  change_rate: number | null
  net_amt: number | null
  concept_tags: string[]
}

export interface StockLhbDesk {
  date: string
  side: 'buy' | 'sell'
  dept_name: string
  buy_amt: number | null
  sell_amt: number | null
  net_amt: number | null
}

export interface StockDetailResponse {
  stock_code: string
  stock_name: string
  records: StockHistoryRecord[]
  lhb_signals: StockLhbSignal[]
  lhb_trading_desk: StockLhbDesk[]
}

// 每日复盘报告
// 注:ReportItem 用单一 interface(全部可选)而非 discriminated union,
// 因为 Vue 模板的 v-if 字符串分支无法做 TS narrowing。后端按 type 返回
// 对应字段子集,前端按 section.title 选择渲染分支。
export interface Top3ReportItem {
  type: 'TOP3_NEW' | 'TOP3_UP' | 'TOP3_STABLE' | 'TOP3_EXIT'
  text: string
  stock_name: string
  stock_code: string
  rank: number
  prev_rank?: number
}

export interface StreakReportItem {
  type: 'STREAK' | 'DARK_HORSE'
  text: string
  stock_name: string
  stock_code: string
  streak_days: number
  rank_trend: string
  first_rank: number
  last_rank: number
  latest_change: number | null
}

export interface LhbReportItem {
  type: 'FOREIGN' | 'INST'
  text: string
  stock_name: string
  stock_code: string
  net_amt: number
  change_rate: number | null
  concept_tags: string[]
}

export interface SectorReportItem {
  type: 'SECTOR_HOT' | 'SECTOR_COOL'
  text: string
  sector: string
  prev_count: number
  curr_count: number
  diff: number
}

// 统一宽松类型:模板可安全访问任何属性(undefined-safe),按 type 判别后使用。
// 保留上面具体 interface 供代码路径明确时强类型使用。
export interface ReportItem {
  type: Top3ReportItem['type'] | StreakReportItem['type'] | LhbReportItem['type'] | SectorReportItem['type']
  text: string
  // 以下字段按 type 不同而存在,全部声明为可选
  stock_name?: string
  stock_code?: string
  rank?: number
  prev_rank?: number
  streak_days?: number
  rank_trend?: string
  first_rank?: number
  last_rank?: number
  latest_change?: number | null
  net_amt?: number
  change_rate?: number | null
  concept_tags?: string[]
  sector?: string
  prev_count?: number
  curr_count?: number
  diff?: number
  [key: string]: unknown  // 允许后端返回额外字段
}

export interface ReportSection {
  title: string
  items: ReportItem[]
}

export interface DailyReportResponse {
  date: string
  sections: ReportSection[]
}

// 胜率回测
export interface BacktestPeriodStat {
  period: string
  count: number
  win_rate: number
  avg_change: number
  median_change: number | null
  max_change: number | null
  min_change: number | null
}

export interface BacktestHorizonStat {
  horizon: string
  win_rate: number
  avg_change: number
}

export interface BacktestSectorStat {
  sector: string
  count: number
  win_rate: number
  avg_change: number
}

export interface BacktestResponse {
  signal_type: string
  total_signals: number
  overall_win_rate: number
  overall_avg_change: number
  period_stats: BacktestPeriodStat[]
  horizon_stats: BacktestHorizonStat[]
  sector_stats: BacktestSectorStat[]
}

// ---- AI 分析 ----

export interface AIAnalysisResult {
  date: string
  generated_at: string
  model: string
  tokens_used?: number
  duration_ms?: number
  content: string | null
  message?: string
  error?: string
}

export interface AIStockPickRequest {
  strategy: 'aggressive' | 'balanced' | 'conservative'
  custom_prompt?: string
  sector_filter?: string[]
  max_results?: number
}

export interface AISignalDiagnosis extends AIAnalysisResult {
  period_days?: number
  start_date?: string
  end_date?: string
  backtest_summary?: {
    overall_win_rate: number | null
    total_signals: number
    foreign_win_rate: number | null
    inst_win_rate: number | null
  }
}

export interface AIHistoryRecord {
  id: number
  analysis_type: string
  date: string
  model_name: string
  tokens_used: number
  duration_ms: number
  created_at: string
}
