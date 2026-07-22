import type {
  StockPoolImportResult,
  StockPoolListResponse,
  StockPoolVersion,
  DataHealth,
  DataJob,
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
  }
}
