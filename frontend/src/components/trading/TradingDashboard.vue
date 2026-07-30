<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import { useFormat } from '../../composables/useFormat'
import type { Account, EquitySnapshot, PlanRunDetail, DataHealth } from '../../types/trading'

const api = useTradingApi()
const { formatMoney, formatPercent } = useFormat()

const props = defineProps<{ accountId: number | null }>()

const accounts = ref<Account[]>([])
const account = ref<Account | null>(null)
const equity = ref<EquitySnapshot | null>(null)
const latestPlan = ref<PlanRunDetail | null>(null)
const health = ref<DataHealth | null>(null)
const loading = ref(false)
const error = ref('')

async function loadAll() {
  if (props.accountId == null) {
    // 无选中账户时,尝试加载 active 账户
    try {
      const res = await api.listAccounts(true)
      accounts.value = res.accounts
      if (accounts.value.length > 0) {
        account.value = accounts.value[0] ?? null
      }
    } catch {
      account.value = null
    }
  } else {
    try {
      account.value = await api.getAccount(props.accountId)
    } catch {
      account.value = null
    }
  }
  if (!account.value) return

  loading.value = true
  error.value = ''
  const today = new Date().toISOString().slice(0, 10)

  // 并行加载净值/最近计划/数据健康
  const tasks: Promise<void>[] = []
  tasks.push(
    api.getEquitySnapshot(account.value.id, today).then(e => { equity.value = e }).catch(() => { equity.value = null }),
  )
  tasks.push(
    api.listPlanRuns().then(res => {
      const readyPlans = res.plan_runs.filter(p =>
        ['READY', 'PARTIAL', 'PUBLISHED', 'BLOCKED'].includes(p.status)
      )
      latestPlan.value = readyPlans[0] ?? res.plan_runs[0] ?? null
    }).catch(() => { latestPlan.value = null }),
  )
  tasks.push(
    api.getDataHealth().then(h => { health.value = h }).catch(() => { health.value = null }),
  )
  await Promise.all(tasks)
  loading.value = false
}

const regimeLabel = (r: string | null): string => {
  return { ATTACK: '进攻', NEUTRAL: '中性', DEFENSE: '防守' }[r ?? ''] ?? '-'
}
const regimeColor = (r: string | null): string => {
  return { ATTACK: '#ef4444', NEUTRAL: '#f59e0b', DEFENSE: '#3b82f6' }[r ?? ''] ?? '#888'
}
const statusLabel = (s: string): string => {
  return { READY: '就绪', PARTIAL: '部分就绪', PUBLISHED: '已发布', BLOCKED: '已阻断',
           SUPERSEDED: '已替代', FAILED: '失败', CREATED: '创建中', VALIDATING: '验证中',
           GENERATING: '生成中' }[s] ?? s
}
const statusColor = (s: string): string => {
  return { READY: '#10b981', PARTIAL: '#f59e0b', PUBLISHED: '#3b82f6', BLOCKED: '#ef4444',
           SUPERSEDED: '#666', FAILED: '#ef4444' }[s] ?? '#888'
}

onMounted(loadAll)
watch(() => props.accountId, loadAll)
</script>

<template>
  <div class="dashboard">
    <p v-if="error" class="msg error">{{ error }}</p>
    <div v-if="loading" class="loading-hint">加载中...</div>

    <template v-if="account">
      <!-- 账户摘要卡片 -->
      <div class="card-row">
        <div class="metric-card">
          <span class="metric-label">账户净值</span>
          <span class="metric-value">{{ equity ? formatMoney(equity.total_equity) : formatMoney(account.cash_balance) }}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">现金</span>
          <span class="metric-value">{{ equity ? formatMoney(equity.cash) : formatMoney(account.cash_balance) }}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">持仓市值</span>
          <span class="metric-value">{{ equity ? formatMoney(equity.market_value) : '¥0.00' }}</span>
        </div>
        <div class="metric-card">
          <span class="metric-label">总仓位</span>
          <span class="metric-value" :style="{ color: (equity?.exposure ?? 0) > 0.6 ? '#ef4444' : '#eee' }">
            {{ equity ? formatPercent(equity.exposure) : '0.00%' }}
          </span>
        </div>
        <div class="metric-card">
          <span class="metric-label">最大回撤</span>
          <span class="metric-value" :style="{ color: (equity?.drawdown ?? 0) >= account.max_drawdown_limit ? '#ef4444' : '#eee' }">
            {{ equity ? formatPercent(equity.drawdown) : '0.00%' }}
          </span>
        </div>
      </div>

      <!-- 市场状态 + 最近计划 -->
      <div class="dual-row">
        <div class="info-card">
          <h4>市场状态</h4>
          <div v-if="latestPlan" class="market-info">
            <span class="regime-badge" :style="{ background: regimeColor(latestPlan.market_regime), color: 'white' }">
              {{ regimeLabel(latestPlan.market_regime) }}
            </span>
            <span class="market-score">分数: {{ latestPlan.market_score ?? '-' }}</span>
            <span v-if="latestPlan.degraded" class="degraded-tag">降级模式</span>
            <span class="market-exposure">建议仓位: {{ latestPlan.recommended_exposure ? formatPercent(latestPlan.recommended_exposure) : '-' }}</span>
          </div>
          <div v-else class="empty-hint">暂无计划数据</div>
        </div>

        <div class="info-card">
          <h4>最近计划</h4>
          <div v-if="latestPlan" class="plan-info">
            <span class="plan-status" :style="{ color: statusColor(latestPlan.status) }">
              {{ statusLabel(latestPlan.status) }}
            </span>
            <span class="plan-date">{{ latestPlan.signal_date }} → {{ latestPlan.target_trade_date }}</span>
            <div class="plan-counts">
              <span>条件买入: {{ latestPlan.items.filter(i => i.action === 'CONDITIONAL_BUY').length }}</span>
              <span>退出: {{ latestPlan.items.filter(i => i.action === 'EXIT').length }}</span>
              <span>持有: {{ latestPlan.items.filter(i => i.action === 'HOLD').length }}</span>
            </div>
          </div>
          <div v-else class="empty-hint">暂无计划</div>
        </div>
      </div>

      <!-- 数据健康摘要 -->
      <div class="info-card">
        <h4>数据健康</h4>
        <div v-if="health" class="health-info">
          <span class="health-status" :style="{ color: statusColor(health.overall_status === 'OK' ? 'READY' : health.overall_status === 'PARTIAL' ? 'PARTIAL' : 'BLOCKED') }">
            {{ health.overall_status }}
          </span>
          <span>股票池覆盖: {{ health.pool_available }}/{{ health.pool_total }}</span>
          <span v-if="health.pool_missing.length">缺失: {{ health.pool_missing.length }}</span>
          <span class="health-time">更新: {{ health.generated_at }}</span>
        </div>
        <div v-else class="empty-hint">暂无数据健康报告</div>
      </div>

      <!-- 交易统计(Phase 5) -->
      <div class="info-card phase5-placeholder">
        <h4>交易统计 <span class="phase5-tag">Phase 5</span></h4>
        <p class="empty-hint">胜率 / 平均 R / 期望值 / 规则执行率将在 Phase 5(回测与复盘)就绪后展示。</p>
      </div>
    </template>

    <div v-else-if="!loading" class="empty-hint">
      <p>暂无账户,请先在"持仓与成交"页面创建账户。</p>
    </div>
  </div>
</template>

<style scoped>
.dashboard { padding: 1rem; display: flex; flex-direction: column; gap: 1rem; }
.msg { padding: 0.5rem; border-radius: 4px; }
.msg.error { background: #451a1a; color: #fca5a5; }
.loading-hint, .empty-hint { color: #666; padding: 1rem; text-align: center; }
.card-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.75rem; }
.metric-card { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.75rem; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; }
.metric-label { font-size: 0.8rem; color: #888; }
.metric-value { font-size: 1.2rem; font-weight: bold; color: #eee; }
.dual-row { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
.info-card { padding: 0.75rem; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; }
.info-card h4 { margin: 0 0 0.5rem 0; color: #ccc; font-size: 0.95rem; }
.market-info, .plan-info, .health-info { display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem; font-size: 0.9rem; color: #ccc; }
.regime-badge { padding: 0.2rem 0.6rem; border-radius: 4px; font-weight: bold; font-size: 0.85rem; }
.degraded-tag { background: #422006; color: #fcd34d; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.75rem; }
.plan-status { font-weight: bold; font-size: 1rem; }
.plan-date { color: #888; }
.plan-counts { display: flex; gap: 1rem; width: 100%; font-size: 0.85rem; color: #aaa; }
.health-time { color: #666; font-size: 0.8rem; margin-left: auto; }
.phase5-placeholder { opacity: 0.6; }
.phase5-tag { font-size: 0.7rem; background: #333; color: #888; padding: 0.1rem 0.3rem; border-radius: 2px; }

@media (max-width: 600px) {
  .card-row { grid-template-columns: 1fr 1fr; }
  .dual-row { grid-template-columns: 1fr; }
}
</style>
