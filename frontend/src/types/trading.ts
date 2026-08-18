export interface StockPoolItem {
  stock_code: string
  stock_name: string | null
  sector_name: string | null
  manual_blacklist: boolean
  note: string
}

export interface StockPoolVersion {
  id: number
  pool_name: string
  version_no: number
  items_hash: string
  source: string
  created_at: string
  items: StockPoolItem[]
}

export interface StockPoolListItem {
  id: number
  pool_name: string
  version_no: number
  items_count: number
  created_at: string
}

export interface StockPoolListResponse {
  versions: StockPoolListItem[]
}

export interface StockPoolImportResult {
  id: number
  version_no: number
  items_hash: string
  items_count: number
  reused: boolean
}

export interface DataIssue {
  severity: 'INFO' | 'WARNING' | 'BLOCKING'
  issue_code: string
  message: string
  stock_code: string | null
  details: Record<string, unknown>
}

export interface DataHealth {
  trade_date: string
  overall_status: 'OK' | 'PARTIAL' | 'BLOCKED'
  benchmark_codes: string[]
  benchmark_updated: Record<string, boolean>
  pool_total: number
  pool_available: number
  pool_missing: string[]
  pool_missing_ratio: number
  issues: DataIssue[]
  generated_at: string
}

export interface DataJob {
  id: number
  job_type: string
  job_key: string
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
  progress: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_json: Record<string, unknown> | null
}

// ---- Phase 2: 账户/持仓/成交/净值 ----
export interface Account {
  id: number
  name: string
  initial_equity: number
  cash_balance: number
  risk_per_trade: number
  max_single_position: number
  max_total_exposure: number
  max_sector_exposure: number
  max_positions: number
  max_drawdown_limit: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface AccountCreateRequest {
  name: string
  initial_equity: number
  cash_balance: number
  risk_per_trade?: number
  max_single_position?: number
  max_total_exposure?: number
  max_sector_exposure?: number
  max_positions?: number
  max_drawdown_limit?: number
  is_active?: boolean
}

export interface AccountUpdateRequest {
  cash_balance?: number
  risk_per_trade?: number
  max_single_position?: number
  max_total_exposure?: number
  max_sector_exposure?: number
  max_positions?: number
  max_drawdown_limit?: number
  is_active?: boolean
}

export interface Position {
  id: number
  account_id: number
  stock_code: string
  stock_name: string | null
  quantity: number
  available_quantity: number
  average_cost: number
  initial_stop: number | null
  trailing_stop: number | null
  opened_at: string | null
  updated_at: string
}

export interface Execution {
  id: number
  account_id: number
  stock_code: string
  side: 'BUY' | 'SELL'
  trade_date: string
  price: number
  quantity: number
  commission: number
  tax: number
  note: string
  client_execution_id: string | null
  plan_item_id: number | null
  created_at: string
}

export interface ExecutionCreateRequest {
  account_id: number
  stock_code: string
  side: 'BUY' | 'SELL'
  trade_date: string
  price: number
  quantity: number
  commission?: number
  tax?: number
  note?: string
  client_execution_id: string
  plan_item_id?: number | null
}

export interface EquitySnapshot {
  account_id: number
  trade_date: string
  cash: number
  market_value: number
  total_equity: number
  exposure: number
  peak_equity: number
  drawdown: number
  created_at: string
}

// ---- Phase 3: 策略/计划 ----
export interface Strategy {
  id: number
  strategy_code: string
  version_no: number
  name: string
  params_json: Record<string, unknown>
  params_hash: string
  status: 'DRAFT' | 'ACTIVE' | 'RETIRED'
  created_at: string
  activated_at: string | null
}

export interface StrategyCreateRequest {
  strategy_code: string
  name: string
  params_json: Record<string, unknown>
  notes?: string
}

export interface StrategyActivateResponse {
  id: number
  status: string
  warnings: string[]
  retired_previous_id: number | null
}

export interface PlanRunCreateRequest {
  account_id: number
  signal_date: string
  stock_pool_version_id: number
  strategy_version_id: number
  force_new_version?: boolean
}

export interface PlanRunResponse {
  id: number
  run_key: string
  status: string
  signal_date: string
  target_trade_date: string
  reused: boolean
}

export interface PlanItem {
  id: number
  stock_code: string
  stock_name: string | null
  action: string
  score: number | null
  rank_no: number | null
  trigger_price: number | null
  do_not_chase_price: number | null
  stop_price: number | null
  target_2r_price: number | null
  suggested_quantity: number
  suggested_position_pct: number
  risk_amount: number
  risk_pct: number
  rule_hits: string[]
  rule_misses: string[]
  invalidation_reason: string | null
  execution_status: string
}

export interface PlanRunDetail {
  id: number
  status: string
  signal_date: string
  target_trade_date: string
  market_regime: string | null
  market_score: number | null
  degraded: boolean
  recommended_exposure: number | null
  warnings: string[]
  items: PlanItem[]
  created_at: string
  published_at: string | null
}

export interface PlanRunSummary {
  id: number
  account_id: number
  status: string
  signal_date: string
  target_trade_date: string
  market_regime: string | null
  market_score: number | null
  recommended_exposure: number | null
  warnings: string[]
  error: Record<string, unknown> | null
  created_at: string
  published_at: string | null
}

export interface PlanPublishResponse {
  id: number
  status: string
  published_at: string
}

// ---- 审计日志 / K线(spec §11.1/§12.3) ----
export interface AuditLog {
  id: number
  actor: string
  action: string
  entity_type: string
  entity_id: string | null
  before_json: Record<string, unknown> | null
  after_json: Record<string, unknown> | null
  request_id: string | null
  created_at: string
}

export interface StockBar {
  stock_code: string
  trade_date: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number | null
}
