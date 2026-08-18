<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import { useFormat } from '../../composables/useFormat'
import type {
  PlanRunResponse,
  PlanRunDetail,
  PlanRunSummary,
  PlanItem,
  StockPoolListItem,
  Strategy,
} from '../../types/trading'

// ---- 次日计划面板(spec §12.3)----
// 生成 → 列表 → 详情(items 排序:持仓处理在前,候选在后)→ 发布(仅 READY/PARTIAL)。
// BLOCKED 状态展示醒目但不恐慌的风险提示,不提供「强制忽略并发布」按钮。

const props = defineProps<{ accountId: number | null }>()

const api = useTradingApi()
const { formatMoney, formatPercent, formatPrice, formatQty } = useFormat()

// 列表与详情
const plans = ref<PlanRunSummary[]>([])
const selected = ref<PlanRunDetail | null>(null)

// 表单选项
const poolVersions = ref<StockPoolListItem[]>([])
const strategies = ref<Strategy[]>([])

// 状态标记
const loadingList = ref(false)
const loadingDetail = ref(false)
const loadingOptions = ref(false)
const generating = ref(false)
const publishing = ref(false)
const error = ref('')
const success = ref('')
const publishError = ref('')
let listRequestVersion = 0
let detailRequestVersion = 0
let generateRequestVersion = 0
let publishRequestVersion = 0

// 生成表单
function todayStr(): string {
  const d = new Date()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

const form = ref({
  signal_date: todayStr(),
  stock_pool_version_id: 0 as number | null,
  strategy_version_id: 0 as number | null,
  force_new_version: false,
})

// 确认发布弹窗
const showPublishConfirm = ref(false)

// ---- 颜色 / 文案映射 ----
const STATUS_META: Record<string, { color: string; label: string }> = {
  READY: { color: '#10b981', label: '就绪' },
  PARTIAL: { color: '#f59e0b', label: '部分就绪' },
  BLOCKED: { color: '#ef4444', label: '已阻断' },
  PUBLISHED: { color: '#3b82f6', label: '已发布' },
  SUPERSEDED: { color: '#888', label: '已废弃' },
  FAILED: { color: '#888', label: '失败' },
}

function statusMeta(status: string): { color: string; label: string } {
  return STATUS_META[status] ?? { color: '#888', label: status }
}

const ACTION_META: Record<string, { color: string; label: string }> = {
  CONDITIONAL_BUY: { color: '#3b82f6', label: '条件买入' },
  WATCH: { color: '#888', label: '观察' },
  HOLD: { color: '#10b981', label: '持有' },
  REDUCE: { color: '#f59e0b', label: '减仓' },
  EXIT: { color: '#ef4444', label: '退出' },
  FORBIDDEN: { color: '#7f1d1d', label: '禁止' },
}

function actionMeta(action: string): { color: string; label: string } {
  return ACTION_META[action] ?? { color: '#888', label: action }
}

const REGIME_META: Record<string, { color: string; label: string }> = {
  ATTACK: { color: '#ef4444', label: '进攻' },
  NEUTRAL: { color: '#888', label: '中性' },
  DEFENSE: { color: '#3b82f6', label: '防守' },
}

function regimeMeta(regime: string | null): { color: string; label: string } | null {
  if (!regime) return null
  return REGIME_META[regime] ?? { color: '#888', label: regime }
}

// ---- 排序:持仓处理(EXIT/REDUCE/HOLD)在前,新候选(CONDITIONAL_BUY/WATCH)在后 ----
const HOLDING_ACTIONS = ['EXIT', 'REDUCE', 'HOLD']

function sortItems(items: PlanItem[]): PlanItem[] {
  return [...items].sort((a, b) => {
    const aHolding = HOLDING_ACTIONS.includes(a.action) ? 0 : 1
    const bHolding = HOLDING_ACTIONS.includes(b.action) ? 0 : 1
    if (aHolding !== bHolding) return aHolding - bHolding
    return (a.rank_no ?? 999) - (b.rank_no ?? 999)
  })
}

const sortedItems = computed<PlanItem[]>(() =>
  selected.value ? sortItems(selected.value.items) : [],
)

const selectedSummary = computed<PlanRunSummary | null>(() => {
  if (!selected.value || props.accountId == null) return null
  return plans.value.find(
    plan => plan.id === selected.value?.id && plan.account_id === props.accountId,
  ) ?? null
})

// ---- 发布按钮可用性(spec §12.3)----
const publishState = computed<{ kind: 'publish' | 'published' | 'disabled'; tip: string }>(() => {
  if (!selected.value || !selectedSummary.value) {
    return { kind: 'disabled', tip: '请先选择当前账户计划' }
  }
  const status = selected.value.status
  if (status === 'PUBLISHED') return { kind: 'published', tip: '该计划已发布' }
  if (status === 'READY' || status === 'PARTIAL') {
    return { kind: 'publish', tip: '发布到执行通道' }
  }
  if (status === 'BLOCKED') return { kind: 'disabled', tip: '数据门禁阻断,无法发布' }
  return { kind: 'disabled', tip: `当前状态 ${status} 不可发布` }
})

const canGenerate = computed(() => {
  if (props.accountId == null) return false
  if (!form.value.signal_date) return false
  if (!form.value.stock_pool_version_id) return false
  if (!form.value.strategy_version_id) return false
  return !generating.value
})

// ---- 加载 ----
async function loadOptions() {
  loadingOptions.value = true
  try {
    const [poolRes, stratRes] = await Promise.all([
      api.listStockPools(),
      api.listStrategies(),
    ])
    poolVersions.value = poolRes.versions
    strategies.value = stratRes.strategies.filter((s) => s.status === 'ACTIVE')
    // 默认选中最新池版本与最新激活策略
    if (!form.value.stock_pool_version_id && poolVersions.value[0]) {
      form.value.stock_pool_version_id = poolVersions.value[0].id
    }
    if (!form.value.strategy_version_id && strategies.value[0]) {
      form.value.strategy_version_id = strategies.value[0].id
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loadingOptions.value = false
  }
}

async function loadList() {
  const accountId = props.accountId
  const requestVersion = ++listRequestVersion
  loadingList.value = true
  error.value = ''
  if (accountId == null) {
    plans.value = []
    selected.value = null
    loadingList.value = false
    return
  }
  try {
    const res = await api.listPlanRuns(undefined, undefined, accountId)
    if (requestVersion !== listRequestVersion || props.accountId !== accountId) return
    plans.value = res.plan_runs.filter(plan => plan.account_id === accountId)
    if (!selectedSummary.value) selected.value = null
    // 若当前没有选中,自动选第一条
    if (!selected.value && plans.value[0]) {
      await selectPlan(plans.value[0].id, accountId)
    }
  } catch (e: unknown) {
    if (requestVersion === listRequestVersion && props.accountId === accountId) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (requestVersion === listRequestVersion && props.accountId === accountId) {
      loadingList.value = false
    }
  }
}

async function selectPlan(id: number, accountId = props.accountId) {
  const summary = plans.value.find(
    plan => plan.id === id && plan.account_id === accountId,
  )
  if (accountId == null || !summary) {
    selected.value = null
    return
  }
  if (publishing.value && selected.value?.id !== id) {
    publishRequestVersion += 1
    publishing.value = false
    showPublishConfirm.value = false
  }
  const requestVersion = ++detailRequestVersion
  loadingDetail.value = true
  publishError.value = ''
  try {
    const detail = await api.getPlanRun(id)
    if (
      requestVersion === detailRequestVersion
      && props.accountId === accountId
      && plans.value.some(
        plan => plan.id === id && plan.account_id === accountId,
      )
    ) {
      selected.value = detail
    }
  } catch (e: unknown) {
    if (requestVersion === detailRequestVersion && props.accountId === accountId) {
      error.value = e instanceof Error ? e.message : String(e)
      selected.value = null
    }
  } finally {
    if (requestVersion === detailRequestVersion && props.accountId === accountId) {
      loadingDetail.value = false
    }
  }
}

async function handleGenerate() {
  const accountId = props.accountId
  if (accountId == null) {
    error.value = '请先选择账户'
    return
  }
  if (!form.value.stock_pool_version_id || !form.value.strategy_version_id) {
    error.value = '请选择股票池版本与策略版本'
    return
  }
  const requestVersion = ++generateRequestVersion
  generating.value = true
  error.value = ''
  success.value = ''
  try {
    const result: PlanRunResponse = await api.createPlanRun({
      account_id: accountId,
      signal_date: form.value.signal_date,
      stock_pool_version_id: form.value.stock_pool_version_id,
      strategy_version_id: form.value.strategy_version_id,
      force_new_version: form.value.force_new_version,
    })
    if (
      requestVersion !== generateRequestVersion
      || props.accountId !== accountId
    ) return
    success.value = result.reused
      ? `复用已有计划 #${result.id}(状态 ${result.status})`
      : `计划已生成 #${result.id}(状态 ${result.status})`
    form.value.force_new_version = false
    await loadList()
    if (
      requestVersion !== generateRequestVersion
      || props.accountId !== accountId
    ) return
    await selectPlan(result.id, accountId)
  } catch (e: unknown) {
    if (
      requestVersion === generateRequestVersion
      && props.accountId === accountId
    ) {
      error.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (
      requestVersion === generateRequestVersion
      && props.accountId === accountId
    ) {
      generating.value = false
    }
  }
}

function openPublishConfirm() {
  publishError.value = ''
  if (!selectedSummary.value) {
    publishError.value = '只能发布当前账户计划'
    return
  }
  showPublishConfirm.value = true
}

async function handlePublish() {
  const accountId = props.accountId
  const summary = selectedSummary.value
  if (!selected.value || !summary || accountId == null) {
    publishError.value = '只能发布当前账户计划'
    showPublishConfirm.value = false
    return
  }
  const planId = selected.value.id
  const requestVersion = ++publishRequestVersion
  publishing.value = true
  publishError.value = ''
  try {
    const res = await api.publishPlanRun(planId)
    if (
      requestVersion !== publishRequestVersion
      || props.accountId !== accountId
      || selected.value?.id !== planId
      || selectedSummary.value?.id !== planId
    ) return
    success.value = `计划 #${res.id} 已发布(${res.published_at})`
    showPublishConfirm.value = false
    await selectPlan(planId, accountId)
    if (
      requestVersion !== publishRequestVersion
      || props.accountId !== accountId
      || selected.value?.id !== planId
    ) return
    await loadList()
  } catch (e: unknown) {
    if (
      requestVersion === publishRequestVersion
      && props.accountId === accountId
      && selected.value?.id === planId
    ) {
      publishError.value = e instanceof Error ? e.message : String(e)
    }
  } finally {
    if (
      requestVersion === publishRequestVersion
      && props.accountId === accountId
      && selected.value?.id === planId
    ) {
      publishing.value = false
    }
  }
}

function cancelPublish() {
  if (!publishing.value) showPublishConfirm.value = false
}

// accountId 切换:清空已选,重新加载列表
watch(
  () => props.accountId,
  (id) => {
    listRequestVersion += 1
    detailRequestVersion += 1
    generateRequestVersion += 1
    publishRequestVersion += 1
    selected.value = null
    plans.value = []
    loadingList.value = false
    loadingDetail.value = false
    showPublishConfirm.value = false
    publishing.value = false
    generating.value = false
    publishError.value = ''
    success.value = ''
    error.value = ''
    if (id != null) {
      void loadList()
    }
  },
)

onMounted(async () => {
  await loadOptions()
  if (props.accountId != null) {
    await loadList()
  }
})
</script>

<template>
  <div class="panel">
    <div v-if="accountId == null" class="empty-prompt">
      <p>请先选择账户</p>
      <p class="hint">在「账户管理」中选择或创建账户后,即可生成次日交易计划。</p>
    </div>

    <template v-else>
      <div class="header">
        <h3>次日计划</h3>
        <button class="btn-secondary" :disabled="loadingList" @click="loadList">
          {{ loadingList ? '刷新中...' : '刷新列表' }}
        </button>
      </div>

      <p v-if="error" class="msg error">{{ error }}</p>
      <p v-if="success" class="msg success">{{ success }}</p>

      <!-- 生成表单 -->
      <section class="section form-card">
        <h4>生成计划</h4>
        <div class="gen-form">
          <div class="form-field">
            <label>信号日期 *</label>
            <input v-model="form.signal_date" type="date" />
          </div>
          <div class="form-field">
            <label>股票池版本 *</label>
            <select v-model.number="form.stock_pool_version_id">
              <option :value="0" disabled>请选择...</option>
              <option v-for="p in poolVersions" :key="p.id" :value="p.id">
                v{{ p.version_no }} · {{ p.items_count }} 只 · {{ p.created_at.slice(0, 10) }}
              </option>
            </select>
          </div>
          <div class="form-field">
            <label>策略版本 *</label>
            <select v-model.number="form.strategy_version_id">
              <option :value="0" disabled>请选择...</option>
              <option v-for="s in strategies" :key="s.id" :value="s.id">
                {{ s.name }} · v{{ s.version_no }} ({{ s.strategy_code }})
              </option>
            </select>
          </div>
          <div class="form-field checkbox-field">
            <label>
              <input v-model="form.force_new_version" type="checkbox" /> 强制生成新版本
            </label>
            <span class="hint">忽略复用,生成全新计划并废弃旧版。</span>
          </div>
        </div>
        <div class="form-actions">
          <button class="btn-primary" :disabled="!canGenerate" @click="handleGenerate">
            {{ generating ? '生成中...' : '生成计划' }}
          </button>
          <span v-if="loadingOptions" class="hint">选项加载中...</span>
          <span
            v-else-if="!poolVersions.length || !strategies.length"
            class="hint warn"
          >
            缺少{{ !poolVersions.length ? '股票池' : '' }}{{ !poolVersions.length && !strategies.length ? '/' : '' }}{{ !strategies.length ? '激活策略' : '' }},请先在对应面板创建。
          </span>
        </div>
      </section>

      <!-- 列表 -->
      <section class="section">
        <h4>计划列表</h4>
        <p v-if="loadingList && !plans.length" class="text-muted">加载中...</p>
        <p v-else-if="!plans.length" class="text-muted">暂无计划,先生成一份试试。</p>
        <div v-else class="plan-list">
          <button
            v-for="p in plans"
            :key="p.id"
            class="plan-item"
            :class="{ active: selected?.id === p.id }"
            @click="selectPlan(p.id)"
          >
            <span class="pi-status" :style="{ color: statusMeta(p.status).color }">
              ● {{ statusMeta(p.status).label }}
            </span>
            <span class="pi-date">{{ p.signal_date }} → {{ p.target_trade_date }}</span>
            <span class="pi-id">#{{ p.id }}</span>
          </button>
        </div>
      </section>

      <!-- 详情 -->
      <section v-if="loadingDetail && !selected" class="section">
        <p class="text-muted">加载详情...</p>
      </section>

      <section v-else-if="selected" class="section">
        <!-- BLOCKED 醒目但冷静的提示(无 bypass 按钮)-->
        <div v-if="selected.status === 'BLOCKED'" class="block-banner">
          <div class="block-title">⚠ 计划已被数据门禁阻断</div>
          <div class="block-body">
            <p>当前数据不满足生成可执行计划的条件,无法发布。<strong>请先修复数据问题</strong>(数据健康检查、补齐行情或基准),再重新生成计划。</p>
            <ul v-if="selected.warnings.length">
              <li v-for="(w, i) in selected.warnings" :key="i">{{ w }}</li>
            </ul>
          </div>
        </div>

        <!-- 头部信息 -->
        <div class="detail-header">
          <div class="dh-left">
            <span
              class="status-badge"
              :style="{
                background: statusMeta(selected.status).color + '22',
                color: statusMeta(selected.status).color,
                borderColor: statusMeta(selected.status).color,
              }"
            >
              {{ statusMeta(selected.status).label }} ({{ selected.status }})
            </span>
            <span class="dh-dates">
              {{ selected.signal_date }} → <strong>{{ selected.target_trade_date }}</strong>
            </span>
            <span class="dh-id">计划 #{{ selected.id }}</span>
          </div>
          <div class="dh-right">
            <!-- 发布按钮(状态驱动)-->
            <button
              v-if="publishState.kind === 'published'"
              class="btn-secondary"
              disabled
            >
              已发布
            </button>
            <button
              v-else-if="publishState.kind === 'publish'"
              class="btn-publish"
              :disabled="publishing"
              @click="openPublishConfirm"
            >
              {{ publishing ? '发布中...' : '发布计划' }}
            </button>
            <button
              v-else
              class="btn-secondary"
              disabled
              :title="publishState.tip"
            >
              发布
            </button>
          </div>
        </div>

        <!-- 指标网格 -->
        <div class="metrics-grid">
          <div class="metric">
            <span class="metric-label">市场状态</span>
            <span
              v-if="regimeMeta(selected.market_regime)"
              class="metric-value"
              :style="{ color: regimeMeta(selected.market_regime)!.color }"
            >
              {{ regimeMeta(selected.market_regime)!.label }}
              <span class="metric-sub">({{ selected.market_regime }})</span>
            </span>
            <span v-else class="metric-value text-muted">-</span>
          </div>
          <div class="metric">
            <span class="metric-label">状态分数</span>
            <span class="metric-value">{{ selected.market_score ?? '-' }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">建议总仓位</span>
            <span class="metric-value">{{ formatPercent(selected.recommended_exposure) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">数据降级</span>
            <span
              class="metric-value"
              :class="selected.degraded ? 'warn-text' : ''"
            >
              {{ selected.degraded ? '是' : '否' }}
            </span>
          </div>
          <div class="metric">
            <span class="metric-label">生成时间</span>
            <span class="metric-value mono small">{{ selected.created_at.slice(0, 16) }}</span>
          </div>
          <div class="metric">
            <span class="metric-label">发布时间</span>
            <span class="metric-value mono small">
              {{ selected.published_at ? selected.published_at.slice(0, 16) : '-' }}
            </span>
          </div>
        </div>

        <!-- 警告 -->
        <div v-if="selected.warnings.length && selected.status !== 'BLOCKED'" class="warn-box">
          <div class="warn-title">⚠ 非阻断告警({{ selected.warnings.length }})</div>
          <ul>
            <li v-for="(w, i) in selected.warnings" :key="i">{{ w }}</li>
          </ul>
        </div>

        <!-- 明细表(持仓处理在前)-->
        <div class="items">
          <div class="items-header">
            <h4>计划明细 <span class="text-muted small">({{ sortedItems.length }} 项,持仓处理在前)</span></h4>
          </div>
          <p v-if="!sortedItems.length" class="text-muted">该计划无明细项。</p>
          <div v-else class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>操作</th>
                  <th>股票</th>
                  <th>评分</th>
                  <th>排名</th>
                  <th>触发价</th>
                  <th>禁追价</th>
                  <th>止损</th>
                  <th>2R目标</th>
                  <th>建议数量</th>
                  <th>仓位占比</th>
                  <th>损失金额</th>
                  <th>账户占比</th>
                  <th>规则命中</th>
                  <th>失效条件</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in sortedItems" :key="item.id">
                  <td>
                    <span
                      class="action-badge"
                      :style="{
                        background: actionMeta(item.action).color + '22',
                        color: actionMeta(item.action).color,
                        borderColor: actionMeta(item.action).color,
                      }"
                    >
                      {{ actionMeta(item.action).label }}
                    </span>
                  </td>
                  <td>
                    <div class="stock-cell">
                      <span class="mono">{{ item.stock_code }}</span>
                      <span class="stock-name">{{ item.stock_name || '-' }}</span>
                    </div>
                  </td>
                  <td>{{ item.score ?? '-' }}</td>
                  <td>{{ item.rank_no ?? '-' }}</td>
                  <td>{{ formatPrice(item.trigger_price) }}</td>
                  <td>{{ formatPrice(item.do_not_chase_price) }}</td>
                  <td>{{ formatPrice(item.stop_price) }}</td>
                  <td>{{ formatPrice(item.target_2r_price) }}</td>
                  <td>{{ formatQty(item.suggested_quantity) }}</td>
                  <td>{{ formatPercent(item.suggested_position_pct) }}</td>
                  <td>{{ formatMoney(item.risk_amount) }}</td>
                  <td>{{ formatPercent(item.risk_pct) }}</td>
                  <td>
                    <div class="chips">
                      <span v-for="r in item.rule_hits" :key="r" class="chip hit">{{ r }}</span>
                      <span v-if="!item.rule_hits.length" class="text-muted">-</span>
                    </div>
                  </td>
                  <td class="invalid-cell">
                    <span v-if="item.invalidation_reason" class="invalid-text">
                      {{ item.invalidation_reason }}
                    </span>
                    <span v-else class="text-muted">-</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- 发布确认弹窗 -->
      <div v-if="showPublishConfirm" class="modal-backdrop" @click.self="cancelPublish">
        <div class="modal">
          <h4>确认发布计划</h4>
          <p>即将发布计划 <strong>#{{ selected?.id }}</strong>(状态 {{ selected?.status }})。</p>
          <p class="hint">
            发布后计划将进入执行通道,不可再修改。请确认明细与告警无误。
          </p>
          <ul v-if="selected?.warnings.length" class="modal-warns">
            <li v-for="(w, i) in selected!.warnings" :key="i">{{ w }}</li>
          </ul>
          <p v-if="publishError" class="msg error">{{ publishError }}</p>
          <div class="modal-actions">
            <button class="btn-publish" :disabled="publishing" @click="handlePublish">
              {{ publishing ? '发布中...' : '确认发布' }}
            </button>
            <button class="btn-secondary" :disabled="publishing" @click="cancelPublish">取消</button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }

.empty-prompt { padding: 2rem; text-align: center; color: #888; border: 1px dashed #333; border-radius: 6px; }
.empty-prompt .hint { font-size: 0.85rem; color: #555; margin-top: 0.5rem; }

.msg { padding: 0.5rem; border-radius: 4px; margin-bottom: 0.75rem; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }

.section { margin-bottom: 1.5rem; }
.section h4 { margin: 0 0 0.5rem; color: #ccc; }
.text-muted { color: #888; }
.small { font-size: 0.8rem; font-weight: normal; }
.mono { font-family: monospace; }
.hint { color: #666; font-size: 0.8rem; margin-left: 0.5rem; }
.hint.warn { color: #f59e0b; }

/* 表单卡片 */
.form-card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 1rem; }
.gen-form { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; }
.form-field { display: flex; flex-direction: column; gap: 0.25rem; }
.form-field label { color: #aaa; font-size: 0.85rem; }
.form-field input,
.form-field select {
  background: #0f0f0f; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.4rem 0.6rem;
}
.checkbox-field { justify-content: center; }
.checkbox-field label { display: flex; align-items: center; gap: 0.4rem; color: #eee; }
.form-actions { display: flex; align-items: center; gap: 0.5rem; margin-top: 0.75rem; }

/* 按钮 */
.btn-primary { padding: 0.45rem 1.2rem; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { padding: 0.4rem 1rem; background: #333; color: #ccc; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-publish { padding: 0.45rem 1.2rem; background: #10b981; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-publish:disabled { opacity: 0.5; cursor: not-allowed; }

/* 计划列表 */
.plan-list { display: flex; flex-direction: column; gap: 0.25rem; }
.plan-item {
  display: flex; align-items: center; gap: 0.75rem;
  text-align: left; padding: 0.5rem 0.75rem;
  background: #1a1a1a; color: #ccc; border: 1px solid #333; border-radius: 4px; cursor: pointer;
}
.plan-item:hover { border-color: #444; }
.plan-item.active { border-color: #3b82f6; background: #1e3a5f; }
.pi-status { font-weight: 500; min-width: 110px; }
.pi-date { color: #eee; font-family: monospace; font-size: 0.9rem; }
.pi-id { color: #666; margin-left: auto; font-family: monospace; }
.pi-degraded { color: #f59e0b; font-size: 0.75rem; padding: 0.05rem 0.4rem; border: 1px solid #f59e0b; border-radius: 3px; }

/* 详情头部 */
.detail-header {
  display: flex; justify-content: space-between; align-items: center; gap: 1rem;
  padding: 0.75rem; background: #1a1a1a; border: 1px solid #333; border-radius: 6px; margin-bottom: 0.75rem;
  flex-wrap: wrap;
}
.dh-left { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.dh-right { display: flex; gap: 0.5rem; }
.status-badge { padding: 0.2rem 0.7rem; border-radius: 12px; font-size: 0.85rem; font-weight: 500; border: 1px solid; }
.dh-dates { color: #ccc; font-family: monospace; font-size: 0.95rem; }
.dh-id { color: #666; font-family: monospace; font-size: 0.85rem; }

/* BLOCKED banner */
.block-banner {
  border-left: 4px solid #ef4444; background: #2a1414;
  border-radius: 4px; padding: 0.75rem 1rem; margin-bottom: 0.75rem;
}
.block-title { color: #fca5a5; font-weight: 600; font-size: 1rem; margin-bottom: 0.4rem; }
.block-body { color: #d4d4d4; font-size: 0.9rem; line-height: 1.5; }
.block-body p { margin: 0 0 0.4rem; }
.block-body ul { margin: 0.4rem 0 0; padding-left: 1.2rem; }
.block-body li { color: #fcd34d; font-size: 0.85rem; margin-bottom: 0.15rem; }

/* 指标 */
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 0.6rem; margin-bottom: 0.75rem; }
.metric { display: flex; flex-direction: column; gap: 0.15rem; padding: 0.5rem 0.7rem; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 4px; }
.metric-label { color: #888; font-size: 0.78rem; }
.metric-value { color: #eee; font-weight: 500; }
.metric-sub { color: #888; font-size: 0.78rem; font-weight: normal; }
.warn-text { color: #f59e0b; }

/* 警告盒 */
.warn-box { border-left: 4px solid #f59e0b; background: #422006; border-radius: 4px; padding: 0.6rem 0.9rem; margin-bottom: 0.75rem; }
.warn-title { color: #fcd34d; font-weight: 500; margin-bottom: 0.3rem; }
.warn-box ul { margin: 0; padding-left: 1.2rem; }
.warn-box li { color: #fde68a; font-size: 0.85rem; margin-bottom: 0.15rem; }

/* 明细表 */
.items-header { margin-bottom: 0.4rem; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #2a2a2a; white-space: nowrap; vertical-align: top; }
th { color: #888; font-weight: 500; font-size: 0.82rem; }

.action-badge { display: inline-block; padding: 0.1rem 0.55rem; border-radius: 10px; font-size: 0.8rem; font-weight: 500; border: 1px solid; }
.stock-cell { display: flex; flex-direction: column; }
.stock-name { color: #aaa; font-size: 0.82rem; }

.chips { display: flex; flex-wrap: wrap; gap: 0.2rem; max-width: 180px; }
.chip { padding: 0.05rem 0.4rem; border-radius: 3px; font-size: 0.72rem; font-family: monospace; }
.chip.hit { background: #14532d; color: #86efac; }

.invalid-cell { max-width: 220px; }
.invalid-text { color: #fcd34d; font-size: 0.82rem; white-space: normal; }

/* 发布确认弹窗 */
.modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 1.25rem; max-width: 460px; width: 92%; }
.modal h4 { margin: 0 0 0.6rem; color: #eee; }
.modal p { margin: 0 0 0.4rem; color: #ccc; font-size: 0.9rem; }
.modal .hint { color: #888; margin: 0 0 0.6rem; display: block; }
.modal-warns { margin: 0.4rem 0 0.8rem; padding-left: 1.2rem; }
.modal-warns li { color: #fde68a; font-size: 0.82rem; margin-bottom: 0.15rem; }
.modal-actions { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.75rem; }

@media (max-width: 600px) {
  .gen-form { grid-template-columns: 1fr; }
  .metrics-grid { grid-template-columns: 1fr 1fr; }
  .detail-header { flex-direction: column; align-items: stretch; }
  .dh-left { flex-direction: column; align-items: flex-start; gap: 0.3rem; }
  .dh-right { justify-content: flex-end; }
  .pi-id { margin-left: 0; }
  th, td { padding: 0.3rem 0.4rem; font-size: 0.82rem; }
}
</style>
