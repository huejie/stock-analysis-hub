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
