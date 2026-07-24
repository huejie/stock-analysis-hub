import type {
  StockPoolImportResult,
  StockPoolListResponse,
  StockPoolVersion,
  DataHealth,
  DataJob,
  Account,
  AccountCreateRequest,
  AccountUpdateRequest,
  Position,
  Execution,
  ExecutionCreateRequest,
  EquitySnapshot,
  Strategy,
  StrategyCreateRequest,
  StrategyActivateResponse,
  PlanRunCreateRequest,
  PlanRunResponse,
  PlanRunDetail,
  PlanPublishResponse,
} from '../types/trading'

export function useTradingApi() {
  async function request<T>(url: string, options?: RequestInit): Promise<T> {
    const resp = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    return resp.json()
  }

  return {
    importStockPool: (body: { pool_name: string; source: 'text' | 'csv'; text_body: string }) =>
      request<StockPoolImportResult>('/api/trading/stock-pools/import', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    listStockPools: (poolName = 'default') =>
      request<StockPoolListResponse>(`/api/trading/stock-pools?pool_name=${poolName}`),
    getStockPool: (versionId: number) =>
      request<StockPoolVersion>(`/api/trading/stock-pools/${versionId}`),
    getDataHealth: (tradeDate?: string) =>
      request<DataHealth>(`/api/trading/data-health${tradeDate ? `?trade_date=${tradeDate}` : ''}`),
    createDataJob: (body: { job_type: string; trade_date?: string }) =>
      request<{ job_id: number; status: string; job_key: string }>('/api/trading/data-jobs', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    getDataJob: (jobId: number) =>
      request<DataJob>(`/api/trading/data-jobs/${jobId}`),

    // Accounts
    createAccount: (body: AccountCreateRequest) =>
      request<Account>('/api/trading/accounts', { method: 'POST', body: JSON.stringify(body) }),
    listAccounts: (activeOnly = false) =>
      request<{ accounts: Account[] }>(`/api/trading/accounts${activeOnly ? '?active_only=true' : ''}`),
    getAccount: (id: number) =>
      request<Account>(`/api/trading/accounts/${id}`),
    updateAccount: (id: number, body: AccountUpdateRequest) =>
      request<Account>(`/api/trading/accounts/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

    // Positions
    getPositions: (accountId: number) =>
      request<{ positions: Position[] }>(`/api/trading/positions?account_id=${accountId}`),
    manualCorrectPosition: (stockCode: string, body: { account_id: number; quantity: number; available_quantity: number; average_cost: number; initial_stop?: number; trailing_stop?: number; note?: string }) =>
      request<Position>(`/api/trading/positions/${stockCode}`, { method: 'PUT', body: JSON.stringify(body) }),

    // Executions
    recordExecution: (body: ExecutionCreateRequest) =>
      request<{ execution: Execution; position: Position | null }>('/api/trading/executions', { method: 'POST', body: JSON.stringify(body) }),
    listExecutions: (accountId: number, start?: string, end?: string) => {
      const qs = new URLSearchParams({ account_id: String(accountId) })
      if (start) qs.set('start', start)
      if (end) qs.set('end', end)
      return request<{ executions: Execution[] }>(`/api/trading/executions?${qs}`)
    },

    // Equity
    getEquitySnapshot: (accountId: number, tradeDate: string) =>
      request<EquitySnapshot>(`/api/trading/equity-snapshots/${accountId}?trade_date=${tradeDate}`),

    // Strategies
    listStrategies: (strategyCode?: string) =>
      request<{ strategies: Strategy[] }>(`/api/trading/strategies${strategyCode ? `?strategy_code=${strategyCode}` : ''}`),
    createStrategy: (body: StrategyCreateRequest) =>
      request<Strategy>('/api/trading/strategies', { method: 'POST', body: JSON.stringify(body) }),
    activateStrategy: (id: number) =>
      request<StrategyActivateResponse>(`/api/trading/strategies/${id}/activate`, { method: 'POST' }),

    // Plan Runs
    createPlanRun: (body: PlanRunCreateRequest) =>
      request<PlanRunResponse>('/api/trading/plan-runs', { method: 'POST', body: JSON.stringify(body) }),
    listPlanRuns: (signalDate?: string, status?: string) => {
      const qs = new URLSearchParams()
      if (signalDate) qs.set('signal_date', signalDate)
      if (status) qs.set('status', status)
      const s = qs.toString()
      return request<{ plan_runs: PlanRunDetail[] }>(`/api/trading/plan-runs${s ? '?' + s : ''}`)
    },
    getPlanRun: (id: number) =>
      request<PlanRunDetail>(`/api/trading/plan-runs/${id}`),
    publishPlanRun: (id: number) =>
      request<PlanPublishResponse>(`/api/trading/plan-runs/${id}/publish`, { method: 'POST' }),
  }
}
