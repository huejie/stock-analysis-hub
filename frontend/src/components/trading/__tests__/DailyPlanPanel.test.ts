import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DailyPlanPanel from '../DailyPlanPanel.vue'

const api = vi.hoisted(() => ({
  listStockPools: vi.fn(),
  listStrategies: vi.fn(),
  listPlanRuns: vi.fn(),
  getPlanRun: vi.fn(),
  createPlanRun: vi.fn(),
  publishPlanRun: vi.fn(),
}))

vi.mock('../../../composables/useTradingApi', () => ({
  useTradingApi: () => api,
}))

const summaryA = {
  id: 101,
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

const summaryB = {
  ...summaryA,
  id: 202,
  account_id: 2,
  signal_date: '2026-08-17',
}

const item = {
  id: 1,
  stock_code: '000001.SZ',
  stock_name: '平安银行',
  action: 'HOLD',
  score: 88,
  rank_no: 1,
  trigger_price: null,
  do_not_chase_price: null,
  stop_price: 9.5,
  target_2r_price: null,
  suggested_quantity: 100,
  suggested_position_pct: 0.1,
  risk_amount: 500,
  risk_pct: 0.005,
  rule_hits: [],
  rule_misses: [],
  invalidation_reason: null,
  execution_status: 'PENDING',
}

const detailA = { ...summaryA, degraded: false, items: [item] }
const detailB = {
  ...summaryB,
  degraded: false,
  items: [{ ...item, id: 2, stock_code: '600000.SH', stock_name: '浦发银行' }],
}

const options = {
  pools: {
    versions: [{
      id: 1,
      pool_name: 'default',
      version_no: 1,
      items_count: 1,
      created_at: '2026-08-18T00:00:00',
    }],
  },
  strategies: {
    strategies: [{
      id: 1,
      strategy_code: 'default',
      version_no: 1,
      status: 'ACTIVE',
      name: 'v1',
      params_json: {},
      params_hash: 'strategy-v1',
      created_at: '2026-08-18T00:00:00',
      activated_at: '2026-08-18T00:00:00',
    }],
  },
}

async function mountPanel() {
  const wrapper = mount(DailyPlanPanel, { props: { accountId: 1 } })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('DailyPlanPanel account isolation', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.listStockPools.mockResolvedValue({ versions: [] })
    api.listStrategies.mockResolvedValue({ strategies: [] })
    api.getPlanRun.mockImplementation((id: number) =>
      Promise.resolve(id === summaryA.id ? detailA : detailB),
    )
    api.publishPlanRun.mockImplementation((id: number) =>
      Promise.resolve({ id, status: 'PUBLISHED', published_at: '2026-08-18T12:00:00' }),
    )
  })

  it('filters mixed results and selects only the current account plan', async () => {
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summaryB, summaryA] })

    const wrapper = await mountPanel()

    expect(api.listPlanRuns).toHaveBeenCalledWith(undefined, undefined, 1)
    expect(wrapper.text()).toContain('#101')
    expect(wrapper.text()).not.toContain('#202')
    expect(api.getPlanRun).toHaveBeenCalledWith(101)
    expect(api.getPlanRun).not.toHaveBeenCalledWith(202)
  })

  it('ignores stale detail from the previous account after switching', async () => {
    let resolveOldDetail!: (value: typeof detailA) => void
    const oldDetail = new Promise<typeof detailA>((resolve) => {
      resolveOldDetail = resolve
    })
    api.listPlanRuns
      .mockResolvedValueOnce({ plan_runs: [summaryA] })
      .mockResolvedValueOnce({ plan_runs: [summaryB] })
    api.getPlanRun.mockImplementation((id: number) =>
      id === 101 ? oldDetail : Promise.resolve(detailB),
    )

    const wrapper = mount(DailyPlanPanel, { props: { accountId: 1 } })
    await flushPromises()
    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('#202')
    resolveOldDetail(detailA)
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('#202')
    expect(wrapper.text()).not.toContain('#101')
  })

  it('publishes only a plan present in the current account results', async () => {
    api.listPlanRuns.mockResolvedValue({ plan_runs: [summaryB, summaryA] })
    const wrapper = await mountPanel()

    await wrapper.get('.detail-header .btn-publish').trigger('click')
    await wrapper.get('.modal .btn-publish').trigger('click')
    await flushPromises()

    expect(api.publishPlanRun).toHaveBeenCalledOnce()
    expect(api.publishPlanRun).toHaveBeenCalledWith(101)
    expect(api.publishPlanRun).not.toHaveBeenCalledWith(202)
  })

  it('does not apply a completed generation after switching accounts', async () => {
    let resolveGeneration!: (value: {
      id: number
      run_key: string
      status: string
      signal_date: string
      target_trade_date: string
      reused: boolean
    }) => void
    const generation = new Promise<{
      id: number
      run_key: string
      status: string
      signal_date: string
      target_trade_date: string
      reused: boolean
    }>((resolve) => {
      resolveGeneration = resolve
    })
    api.listStockPools.mockResolvedValue({
      versions: [{
        id: 1,
        pool_name: 'default',
        version_no: 1,
        items_count: 1,
        created_at: '2026-08-18T00:00:00',
      }],
    })
    api.listStrategies.mockResolvedValue({
      strategies: [{
        id: 1,
        strategy_code: 'default',
        version_no: 1,
        status: 'ACTIVE',
        name: 'v1',
        params_json: {},
        params_hash: 'strategy-v1',
        created_at: '2026-08-18T00:00:00',
        activated_at: '2026-08-18T00:00:00',
      }],
    })
    api.listPlanRuns
      .mockResolvedValue({ plan_runs: [summaryB] })
      .mockResolvedValueOnce({ plan_runs: [] })
    api.createPlanRun.mockReturnValue(generation)
    const wrapper = await mountPanel()

    await wrapper.get('.btn-primary').trigger('click')
    expect(api.createPlanRun).toHaveBeenCalledWith(
      expect.objectContaining({ account_id: 1 }),
    )
    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    resolveGeneration({
      id: 101,
      run_key: 'account-a-run',
      status: 'READY',
      signal_date: '2026-08-18',
      target_trade_date: '2026-08-19',
      reused: false,
    })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).not.toContain('计划已生成 #101')
    expect(wrapper.text()).toContain('#202')
  })


  it('keeps account B generation locked when account A finally resolves', async () => {
    let resolveA!: (value: Record<string, unknown>) => void
    let resolveB!: (value: Record<string, unknown>) => void
    api.listStockPools.mockResolvedValue(options.pools)
    api.listStrategies.mockResolvedValue(options.strategies)
    api.listPlanRuns.mockImplementation(
      (_signal: unknown, _status: unknown, accountId: number) =>
        Promise.resolve({ plan_runs: accountId === 2 ? [summaryB] : [] }),
    )
    api.createPlanRun
      .mockReturnValueOnce(new Promise(resolve => { resolveA = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { resolveB = resolve }))

    const wrapper = await mountPanel()
    await wrapper.get('.btn-primary').trigger('click')
    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    await wrapper.get('.btn-primary').trigger('click')
    expect(api.createPlanRun).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.btn-primary').attributes('disabled')).toBeDefined()

    resolveA({
      id: 101,
      run_key: 'account-a-run',
      status: 'READY',
      signal_date: '2026-08-18',
      target_trade_date: '2026-08-19',
      reused: false,
    })
    await flushPromises()

    expect(wrapper.get('.btn-primary').attributes('disabled')).toBeDefined()
    await wrapper.get('.btn-primary').trigger('click')
    expect(api.createPlanRun).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('计划已生成 #101')

    resolveB({
      id: 202,
      run_key: 'account-b-run',
      status: 'READY',
      signal_date: '2026-08-18',
      target_trade_date: '2026-08-19',
      reused: false,
    })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('计划已生成 #202')
  })


  it('ignores an old publish after A to B to A and keeps the new publish active', async () => {
    let resolveOld!: (value: Record<string, unknown>) => void
    let resolveNew!: (value: Record<string, unknown>) => void
    api.listPlanRuns.mockImplementation(
      (_signal: unknown, _status: unknown, accountId: number) =>
        Promise.resolve({ plan_runs: accountId === 1 ? [summaryA] : [summaryB] }),
    )
    api.publishPlanRun
      .mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve }))
      .mockReturnValueOnce(new Promise(resolve => { resolveNew = resolve }))

    const wrapper = await mountPanel()
    await wrapper.get('.detail-header .btn-publish').trigger('click')
    await wrapper.get('.modal .btn-publish').trigger('click')
    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    await flushPromises()
    await wrapper.setProps({ accountId: 1 })
    await flushPromises()
    await flushPromises()
    await wrapper.get('.detail-header .btn-publish').trigger('click')
    await wrapper.get('.modal .btn-publish').trigger('click')
    expect(api.publishPlanRun).toHaveBeenCalledTimes(2)

    resolveOld({
      id: 101,
      status: 'PUBLISHED',
      published_at: '2026-08-18T12:00:00',
    })
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.modal').exists()).toBe(true)
    expect(wrapper.get('.modal .btn-publish').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).not.toContain('2026-08-18T12:00:00')

    resolveNew({
      id: 101,
      status: 'PUBLISHED',
      published_at: '2026-08-18T12:05:00',
    })
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('.modal').exists()).toBe(false)
    expect(wrapper.text()).toContain('2026-08-18T12:05:00')
  })


  it('clears stale detail loading immediately on account switch', async () => {
    api.listPlanRuns
      .mockResolvedValueOnce({ plan_runs: [summaryA] })
      .mockResolvedValueOnce({ plan_runs: [] })
    api.getPlanRun.mockReturnValue(new Promise(() => {}))

    const wrapper = mount(DailyPlanPanel, { props: { accountId: 1 } })
    await flushPromises()
    expect(wrapper.text()).toContain('加载详情')

    await wrapper.setProps({ accountId: 2 })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).not.toContain('加载详情')
  })
})
