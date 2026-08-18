import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StrategySettingsPanel from '../StrategySettingsPanel.vue'

const api = vi.hoisted(() => ({
  listStrategies: vi.fn(),
  createStrategy: vi.fn(),
  activateStrategy: vi.fn(),
}))

vi.mock('../../../composables/useTradingApi', () => ({
  useTradingApi: () => api,
}))

const legacyActiveStrategy = {
  id: 1,
  strategy_code: 'default',
  version_no: 1,
  name: '旧策略',
  params_json: {
    min_score: 72,
    risk_per_trade: 0.01,
    min_stop_distance: 0.02,
    max_stop_distance: 0.15,
    max_single_position: 0.15,
    max_total_exposure: 0.6,
  },
  params_hash: 'legacy-hash',
  status: 'ACTIVE' as const,
  created_at: '2026-08-19T00:00:00',
  activated_at: '2026-08-19T00:00:00',
}

async function mountPanel() {
  const wrapper = mount(StrategySettingsPanel)
  await flushPromises()
  return wrapper
}

describe('StrategySettingsPanel', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    api.listStrategies.mockResolvedValue({ strategies: [legacyActiveStrategy] })
  })

  it('only renders controls for effective strategy parameters', async () => {
    const wrapper = await mountPanel()

    await wrapper.get('.btn-primary').trigger('click')

    expect(wrapper.find('input[name="min_score"]').exists()).toBe(true)
    expect(wrapper.find('input[name="min_stop_distance"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('固定规则：最小止损距离 3%')
    expect(wrapper.text()).toContain('账户风险参数请在账户管理中修改')
  })

  it('shows the effective max risk for legacy strategies without that key', async () => {
    const wrapper = await mountPanel()
    const parameterRows = wrapper.findAll('.param-table tbody tr')

    expect(parameterRows).toHaveLength(3)
    expect(parameterRows[2]!.text()).toContain('1.00%')
  })

  it('preserves the legacy max risk fallback without re-emitting non-editable keys', async () => {
    const wrapper = await mountPanel()
    api.createStrategy.mockResolvedValue({
      ...legacyActiveStrategy,
      id: 2,
      version_no: 2,
      name: '新策略',
      status: 'DRAFT',
      params_json: { min_score: 75, risk_per_trade: 0.01, max_risk: 0.01 },
    })

    await wrapper.get('.btn-primary').trigger('click')
    await wrapper.get('input[name="min_score"]').setValue('75')
    await wrapper.get('.modal .btn-primary').trigger('click')
    await flushPromises()

    expect(api.createStrategy).toHaveBeenCalledWith(expect.objectContaining({
      params_json: {
        min_score: 75,
        risk_per_trade: 0.01,
        max_risk: 0.01,
      },
    }))
  })
})
