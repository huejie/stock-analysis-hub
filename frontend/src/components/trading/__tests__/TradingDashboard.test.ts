import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TradingDashboard from '../TradingDashboard.vue'

const api = vi.hoisted(() => ({
  listAccounts: vi.fn(),
  getAccount: vi.fn(),
  getEquitySnapshot: vi.fn(),
  listPlanRuns: vi.fn(),
  getPlanRun: vi.fn(),
  getDataHealth: vi.fn(),
}))

vi.mock('../../../composables/useTradingApi', () => ({
  useTradingApi: () => api,
}))

const account = {
  id: 1,
  name: '测试账户',
  initial_equity: 100000,
  cash_balance: 100000,
  risk_per_trade: 0.01,
  max_single_position: 0.2,
  max_total_exposure: 0.8,
  max_sector_exposure: 0.3,
  max_positions: 5,
  max_drawdown_limit: 0.15,
  is_active: true,
  created_at: '2026-08-18T00:00:00',
  updated_at: '2026-08-18T00:00:00',
}

const summary = {
  id: 20,
  account_id: 1,
  status: 'READY',
  signal_date: '2026-08-18',
  target_trade_date: '2026-08-19',
  market_regime: 'ATTACK',
  market_score: 80,
  recommended_exposure: 0.6,
  warnings: [],
  error: null,
  created_at: '2026-08-18T00:00:00',
  published_at: null,
}

const detail = {
  ...summary,
  degraded: false,
  items: [{
    id: 1,
    stock_code: '000001.SZ',
    stock_name: '平安银行',
    action: 'CONDITIONAL_BUY',
    score: 88,
    rank_no: 1,
    trigger_price: 10,
    do_not_chase_price: 10.2,
    stop_price: 9.5,
    target_2r_price: 11,
    suggested_quantity: 100,
    suggested_position_pct: 0.1,
    risk_amount: 500,
    risk_pct: 0.005,
    rule_hits: [],
    rule_misses: [],
    invalidation_reason: null,
    execution_status: 'PENDING',
  }],
}

async function mountDashboard() {
  const wrapper = mount(TradingDashboard, { props: { accountId: 1 } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('TradingDashboard', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.getAccount.mockResolvedValue(account)
    api.getEquitySnapshot.mockResolvedValue({
      account_id: 1,
      trade_date: '2026-08-18',
      cash: 100000,
      market_value: 0,
      total_equity: 100000,
      exposure: 0,
      peak_equity: 100000,
      drawdown: 0,
      created_at: '2026-08-18T00:00:00',
    })
    api.getDataHealth.mockResolvedValue({
      overall_status: 'OK',
      benchmark: { stock_code: '000300.SH', available: true, latest_date: '2026-08-18' },
      pool_total: 1,
      pool_available: 1,
      pool_missing: [],
      issues: [],
      generated_at: '2026-08-18T00:00:00',
    })
  })

  it('loads detail after selecting a list summary that has no items', async () => {
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summary] })
    api.getPlanRun.mockResolvedValue(detail)

    const wrapper = await mountDashboard()

    expect(api.listPlanRuns).toHaveBeenCalledWith(undefined, undefined, 1)
    expect(api.getPlanRun).toHaveBeenCalledWith(20)
    expect(wrapper.text()).toContain('条件买入: 1')
    expect(wrapper.text()).not.toContain('加载失败')
  })

  it('shows a plan list error without breaking the dashboard', async () => {
    api.listPlanRuns.mockRejectedValue(new Error('list failed'))

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('计划列表加载失败')
    expect(api.getPlanRun).not.toHaveBeenCalled()
  })

  it('shows a detail error instead of throwing when a selected plan cannot load', async () => {
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summary] })
    api.getPlanRun.mockRejectedValue(new Error('detail failed'))

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('计划详情加载失败')
  })

  it('retries a failed plan list and renders the recovered detail', async () => {
    api.listPlanRuns.mockRejectedValueOnce(new Error('list failed'))

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('计划列表加载失败')

    api.listPlanRuns.mockResolvedValue({ plan_runs: [summary] })
    api.getPlanRun.mockResolvedValue(detail)
    await wrapper.get('button').trigger('click')
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).not.toContain('计划列表加载失败')
    expect(wrapper.text()).toContain('条件买入: 1')
    expect(api.getPlanRun).toHaveBeenCalledWith(20)
  })

  it('keeps plan data visible when equity and health loading fail', async () => {
    api.getEquitySnapshot.mockRejectedValue(new Error('equity failed'))
    api.getDataHealth.mockRejectedValue(new Error('health failed'))
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summary] })
    api.getPlanRun.mockResolvedValue(detail)

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('净值加载失败')
    expect(wrapper.text()).toContain('数据健康加载失败')
    expect(wrapper.text()).toContain('条件买入: 1')
  })

  it('keeps plan and health data visible when account loading fails', async () => {
    api.getAccount.mockRejectedValue(new Error('account failed'))
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summary] })
    api.getPlanRun.mockResolvedValue(detail)

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('账户加载失败')
    expect(wrapper.text()).toContain('条件买入: 1')
    expect(wrapper.text()).toContain('OK')
    expect(api.listPlanRuns).toHaveBeenCalledOnce()
    expect(api.getPlanRun).toHaveBeenCalledWith(20)
    expect(api.getEquitySnapshot).not.toHaveBeenCalled()
    expect(api.getDataHealth).toHaveBeenCalledOnce()
  })

  it('keeps the empty plan state when the list has no plans', async () => {
    api.listPlanRuns.mockResolvedValue({ plan_runs: [] })

    const wrapper = await mountDashboard()

    expect(wrapper.text()).toContain('暂无计划')
    expect(api.getPlanRun).not.toHaveBeenCalled()
  })

  it('discards an old account plan response after the account changes', async () => {
    let resolveOldList!: (value: { plan_runs: typeof summary[] }) => void
    const oldList = new Promise<{ plan_runs: typeof summary[] }>((resolve) => {
      resolveOldList = resolve
    })
    const otherAccount = { ...account, id: 2, name: '账户 B', is_active: false }
    const otherSummary = { ...summary, id: 30, account_id: 2 }
    const otherDetail = {
      ...detail,
      ...otherSummary,
      items: [{ ...detail.items[0], id: 2, action: 'HOLD' }],
    }
    api.getAccount.mockImplementation((id: number) =>
      Promise.resolve(id === 1 ? account : otherAccount),
    )
    api.listPlanRuns
      .mockReturnValueOnce(oldList)
      .mockResolvedValueOnce({ plan_runs: [otherSummary] })
    api.getPlanRun.mockImplementation((id: number) =>
      Promise.resolve(id === 20 ? detail : otherDetail),
    )

    const wrapper = mount(TradingDashboard, { props: { accountId: 1 } })
    await flushPromises()
    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('持有: 1')
    expect(api.getPlanRun).toHaveBeenCalledWith(30)

    resolveOldList({ plan_runs: [summary] })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('持有: 1')
    expect(wrapper.text()).not.toContain('条件买入: 1')
    expect(api.getPlanRun).not.toHaveBeenCalledWith(20)
  })


  it('retries only the plan while health and equity from loadAll remain current', async () => {
    let resolveHealth!: (value: Record<string, unknown>) => void
    let resolveEquity!: (value: Record<string, unknown>) => void
    api.getDataHealth.mockReturnValue(new Promise(resolve => { resolveHealth = resolve }))
    api.getEquitySnapshot.mockReturnValue(new Promise(resolve => { resolveEquity = resolve }))
    api.listPlanRuns
      .mockRejectedValueOnce(new Error('list failed'))
      .mockResolvedValueOnce({ plan_runs: [summary] })
    api.getPlanRun.mockResolvedValue(detail)

    const wrapper = mount(TradingDashboard, { props: { accountId: 1 } })
    await flushPromises()
    expect(wrapper.text()).toContain('计划列表加载失败')
    expect(wrapper.find('.loading-hint').exists()).toBe(true)

    await wrapper.get('.msg.error button').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('条件买入: 1')

    resolveHealth({
      overall_status: 'OK',
      benchmark: { stock_code: '000300.SH', available: true, latest_date: '2026-08-18' },
      pool_total: 1,
      pool_available: 1,
      pool_missing: [],
      issues: [],
      generated_at: '2026-08-18T00:00:00',
    })
    resolveEquity({
      account_id: 1,
      trade_date: '2026-08-18',
      cash: 123456,
      market_value: 0,
      total_equity: 123456,
      exposure: 0,
      peak_equity: 123456,
      drawdown: 0,
      created_at: '2026-08-18T00:00:00',
    })
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.loading-hint').exists()).toBe(false)
    expect(wrapper.text()).toContain('OK')
    expect(wrapper.text()).toContain('123,456')
  })
})
