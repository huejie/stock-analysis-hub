import type {
  DailyStatsResponse,
  StockRecord,
  SeasonDailyStat,
  SaveResponse,
} from '../types'

async function request<T>(urlOrInit: string | (RequestInit & { url: string })): Promise<T> {
  const isObj = typeof urlOrInit !== 'string'
  const url = isObj ? urlOrInit.url : urlOrInit
  const init = isObj ? urlOrInit : undefined
  const resp = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!resp.ok) throw new Error(await resp.text())
  return resp.json()
}

export function useApi() {
  return {
    fetchDates: () =>
      request<{ dates: string[] }>('/api/dates'),

    fetchDailyStats: (date: string) =>
      request<DailyStatsResponse>(`/api/stats/daily?date=${date}`),

    fetchRangeRecords: (start: string, end: string) =>
      request<StockRecord[]>(`/api/records/range?start=${start}&end=${end}`),

    fetchSeasonStats: (start?: string, end?: string) => {
      const params = new URLSearchParams()
      if (start) params.set('start', start)
      if (end) params.set('end', end)
      const qs = params.toString()
      return request<SeasonDailyStat[]>(`/api/season-stats${qs ? '?' + qs : ''}`)
    },

    uploadImage: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      const resp = await fetch('/api/upload', { method: 'POST', body: form })
      if (!resp.ok) throw new Error(await resp.text())
      return resp.json() as Promise<{ date: string; records: StockRecord[] }>
    },

    saveRecords: (data: { date: string; records: StockRecord[] }) =>
      request<SaveResponse>({ url: '/api/records', method: 'POST', body: JSON.stringify(data) }),

    saveSeasonStats: (records: SeasonDailyStat[]) =>
      request<SaveResponse>({ url: '/api/season-stats', method: 'POST', body: JSON.stringify({ records }) }),

    crawlToday: () =>
      request<{ status: string; message: string }>({ url: '/api/crawl', method: 'POST' }),
  }
}
