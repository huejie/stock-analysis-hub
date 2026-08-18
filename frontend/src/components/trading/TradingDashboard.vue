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
const accountError = ref('')
const equityError = ref('')
const planError = ref('')
const healthError = ref('')
let loadVersion = 0
let planRequestVersion = 0

function isCurrentLoad(version: number, requestedAccountId: number | null): boolean {
  return version === loadVersion && props.accountId === requestedAccountId
}

function isCurrentPlanRequest(
  version: number,
  requestedAccountId: number | null,
): boolean {
  return version === planRequestVersion && props.accountId === requestedAccountId
}

async function loadLatestPlan(
  accountId: number | null,
  planVersion: number,
  requestedAccountId: number | null,
) {
  if (accountId == null) return
  try {
    const res = await api.listPlanRuns(undefined, undefined, accountId)
    if (!isCurrentPlanRequest(planVersion, requestedAccountId)) return
    const accountPlans = res.plan_runs.filter(p => p.account_id === accountId)
    const readyPlans = accountPlans.filter(p =>
      ['READY', 'PARTIAL', 'PUBLISHED', 'BLOCKED'].includes(p.status)
    )
    const summary = readyPlans[0] ?? accountPlans[0]
    if (!summary) return

    try {
      const detail = await api.getPlanRun(summary.id)
      if (isCurrentPlanRequest(planVersion, requestedAccountId)) {
        latestPlan.value = detail
      }
    } catch {
      if (isCurrentPlanRequest(planVersion, requestedAccountId)) {
        planError.value = '计划详情加载失败'
      }
    }
  } catch {
    if (isCurrentPlanRequest(planVersion, requestedAccountId)) {
      planError.value = '计划列表加载失败'
    }
  }
}

function retryLatestPlan() {
  const requestedAccountId = props.accountId
  const accountId = requestedAccountId ?? account.value?.id ?? null
  const planVersion = ++planRequestVersion
  latestPlan.value = null
  planError.value = ''
  void loadLatestPlan(accountId, planVersion, requestedAccountId)
}

async function fetchAccount(requestedAccountId: number | null): Promise<Account | null> {
  if (requestedAccountId == null) {
    // 无选中账户时,尝试加载 active 账户
    const res = await api.listAccounts(true)
    accounts.value = res.accounts
    return accounts.value[0] ?? null
  }
  return api.getAccount(requestedAccountId)
}

async function loadAll() {
  const version = ++loadVersion
  const planVersion = ++planRequestVersion
  const requestedAccountId = props.accountId
  loading.value = true
  account.value = null
  equity.value = null
  latestPlan.value = null
  health.value = null
  accountError.value = ''
  equityError.value = ''
  planError.value = ''
  healthError.value = ''
  const today = new Date().toISOString().slice(0, 10)

  let loadedAccount: Account | null = null
  try {
    loadedAccount = await fetchAccount(requestedAccountId)
    if (!isCurrentLoad(version, requestedAccountId)) return
    account.value = loadedAccount
  } catch {
    if (!isCurrentLoad(version, requestedAccountId)) return
    accountError.value = '账户加载失败'
  }

  const planAccountId = requestedAccountId ?? loadedAccount?.id ?? null
  const planTask = loadLatestPlan(
    planAccountId,
    planVersion,
    requestedAccountId,
  )
  const healthTask = api.getDataHealth()
    .then(h => {
      if (isCurrentLoad(version, requestedAccountId)) health.value = h
    })
    .catch(() => {
      if (isCurrentLoad(version, requestedAccountId)) {
        health.value = null
        healthError.value = '数据健康加载失败'
      }
    })

  const equityTask = loadedAccount
    ? api.getEquitySnapshot(loadedAccount.id, today)
      .then(e => {
        if (isCurrentLoad(version, requestedAccountId)) equity.value = e
      })
      .catch(() => {
        if (isCurrentLoad(version, requestedAccountId)) {
          equity.value = null
          equityError.value = '净值加载失败'
        }
      })
    : Promise.resolve()
  await Promise.all([planTask, healthTask, equityTask])
  if (isCurrentLoad(version, requestedAccountId)) loading.value = false
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

onMounted(() => { void loadAll() })
watch(() => props.accountId, () => { void loadAll() })
</script>

<template>
  <div class="dashboard">
    <p v-if="accountError" class="msg error">{{ accountError }}</p>
    <p v-if="equityError" class="msg error">{{ equityError }}</p>
    <p v-if="planError" class="msg error">{{ planError }} <button type="button" @click="retryLatestPlan">重试</button></p>
    <p v-if="healthError" class="msg error">{{ healthError }}</p>
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
    </template>

    <div v-else-if="!loading" class="empty-hint">
      <p>暂无账户,请先在"持仓与成交"页面创建账户。</p>
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
          <div v-if="latestPlan?.items" class="plan-info">
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

    <template v-if="account">
      <!-- 交易统计(Phase 5) -->
      <div class="info-card phase5-placeholder">
        <h4>交易统计 <span class="phase5-tag">Phase 5</span></h4>
        <p class="empty-hint">胜率 / 平均 R / 期望值 / 规则执行率将在 Phase 5(回测与复盘)就绪后展示。</p>
      </div>
    </template>
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
