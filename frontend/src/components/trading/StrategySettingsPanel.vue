<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import { useFormat } from '../../composables/useFormat'
import type { Strategy, StrategyActivateResponse } from '../../types/trading'

const api = useTradingApi()
const { formatPercent } = useFormat()

// 仅保留计划引擎实际读取的策略参数；账户级限制在账户管理中维护。
interface ParamMeta {
  key: string
  label: string
  default: number
  min: number
  max: number
  format: 'percent' | 'int'
  highRisk: boolean
  risk: string
}
const EDITABLE_STRATEGY_PARAMS: ParamMeta[] = [
  { key: 'min_score', label: '最低候选评分', default: 70, min: 0, max: 100, format: 'int', highRisk: false, risk: '降低评分门槛纳入更多低质量候选' },
  { key: 'risk_per_trade', label: '策略单笔风险', default: 0.005, min: 0.001, max: 0.05, format: 'percent', highRisk: true, risk: '提高单笔风险会放大每笔亏损' },
  { key: 'max_risk', label: '策略单笔风险上限', default: 0.005, min: 0.001, max: 0.05, format: 'percent', highRisk: true, risk: '提高风险上限会放宽单笔风险约束' },
]

const FIXED_RULES = [
  '固定规则：最小止损距离 3%',
  '固定规则：最大止损距离 10%',
]

const strategies = ref<Strategy[]>([])
const selected = ref<Strategy | null>(null)
const loading = ref(false)
const error = ref('')
const success = ref('')

// 创建草稿表单
const showCreateForm = ref(false)
const formCode = ref('default')
const formName = ref('')
const formParams = ref<Record<string, number>>({})
const showHighRiskConfirm = ref(false)
const highRiskChanges = ref<string[]>([])

// 激活
const activateResult = ref<StrategyActivateResponse | null>(null)

function initFormParams() {
  const p: Record<string, number> = {}
  for (const m of EDITABLE_STRATEGY_PARAMS) p[m.key] = m.default
  return p
}

function serializeEditableParams(): Record<string, number> {
  const params: Record<string, number> = {}
  for (const m of EDITABLE_STRATEGY_PARAMS) {
    params[m.key] = formParams.value[m.key] ?? m.default
  }
  return params
}

function fmtVal(v: number | undefined, meta: ParamMeta): string {
  if (v == null) return '-'
  return meta.format === 'percent' ? formatPercent(v) : String(v)
}

async function loadStrategies() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.listStrategies()
    strategies.value = res.strategies
    if (strategies.value.length > 0 && !selected.value) {
      selected.value = strategies.value[0] ?? null
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function selectStrategy(s: Strategy) {
  selected.value = s
  activateResult.value = null
}

function getParam(key: string, params: Record<string, unknown> | undefined): number | undefined {
  if (!params) return undefined
  const v = params[key]
  return typeof v === 'number' ? v : undefined
}

function getEditableParamValue(meta: ParamMeta, params: Record<string, unknown> | undefined): number {
  const value = getParam(meta.key, params)
  if (value != null) return value
  if (meta.key === 'max_risk') return getParam('risk_per_trade', params) ?? meta.default
  return meta.default
}

function openCreateForm() {
  // 用当前 ACTIVE 策略的参数初始化(如有),否则用默认
  const active = strategies.value.find(s => s.status === 'ACTIVE')
  formParams.value = initFormParams()
  if (active) {
    for (const m of EDITABLE_STRATEGY_PARAMS) {
      formParams.value[m.key] = getEditableParamValue(m, active.params_json)
    }
    formCode.value = active.strategy_code
  }
  formName.value = ''
  showCreateForm.value = true
  success.value = ''
  error.value = ''
}

function checkHighRiskBeforeCreate(): boolean {
  const active = strategies.value.find(s => s.status === 'ACTIVE')
  const changes: string[] = []
  for (const m of EDITABLE_STRATEGY_PARAMS) {
    if (!m.highRisk) continue
    const oldVal = active ? getEditableParamValue(m, active.params_json) : m.default
    const newVal = formParams.value[m.key] ?? m.default
    if (newVal > oldVal) {
      changes.push(`${m.label}: ${fmtVal(oldVal, m)} → ${fmtVal(newVal, m)}`)
    }
  }
  if (changes.length > 0) {
    highRiskChanges.value = changes
    showHighRiskConfirm.value = true
    return false
  }
  return true
}

async function handleCreate() {
  if (!checkHighRiskBeforeCreate()) return
  await doCreate()
}

async function doCreate() {
  loading.value = true
  error.value = ''
  showHighRiskConfirm.value = false
  try {
    const created = await api.createStrategy({
      strategy_code: formCode.value,
      name: formName.value || `v${Date.now()}`,
      params_json: serializeEditableParams(),
    })
    success.value = `草稿已创建: ${created.name} (v${created.version_no})`
    showCreateForm.value = false
    await loadStrategies()
    selected.value = created
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function handleActivate(id: number) {
  loading.value = true
  error.value = ''
  activateResult.value = null
  try {
    activateResult.value = await api.activateStrategy(id)
    success.value = `策略 v${selected.value?.version_no} 已激活`
    await loadStrategies()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

function statusBadgeClass(status: string): string {
  return { ACTIVE: 'status-active', DRAFT: 'status-draft', RETIRED: 'status-retired' }[status] || 'status-draft'
}

onMounted(loadStrategies)
</script>

<template>
  <div class="panel">
    <h3>策略设置</h3>
    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="success" class="msg success">{{ success }}</p>

    <!-- 策略版本列表 -->
    <div class="section">
      <div class="section-header">
        <h4>策略版本</h4>
        <button class="btn-primary" @click="openCreateForm">+ 新建草稿</button>
      </div>
      <div v-if="loading && !strategies.length" class="loading-hint">加载中...</div>
      <div v-else-if="!strategies.length" class="empty-hint">暂无策略版本,点击"新建草稿"创建第一个。</div>
      <div v-else class="version-list">
        <button
          v-for="s in strategies" :key="s.id"
          class="version-item" :class="{ active: selected?.id === s.id }"
          @click="selectStrategy(s)"
        >
          <span class="version-badge" :class="statusBadgeClass(s.status)">{{ s.status }}</span>
          <span>{{ s.strategy_code }} · v{{ s.version_no }} · {{ s.name }}</span>
          <span class="version-date">{{ s.created_at.slice(0, 10) }}</span>
        </button>
      </div>
    </div>

    <!-- 选中策略参数详情 -->
    <div v-if="selected" class="section">
      <h4>参数详情 — {{ selected.strategy_code }} v{{ selected.version_no }}</h4>
      <table class="param-table">
        <thead>
          <tr>
            <th>参数</th><th>当前值</th><th>默认值</th><th>合法范围</th><th>风险解释</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in EDITABLE_STRATEGY_PARAMS" :key="m.key" :class="{ 'high-risk-row': m.highRisk }">
            <td>{{ m.label }}<span v-if="m.highRisk" class="risk-tag">高风险</span></td>
            <td>{{ fmtVal(getEditableParamValue(m, selected.params_json), m) }}</td>
            <td>{{ fmtVal(m.default, m) }}</td>
            <td>{{ fmtVal(m.min, m) }} ~ {{ fmtVal(m.max, m) }}</td>
            <td class="risk-cell">{{ m.risk }}</td>
          </tr>
        </tbody>
      </table>
      <div class="rules-guidance">
        <p v-for="rule in FIXED_RULES" :key="rule">{{ rule }}</p>
        <p>账户风险参数请在账户管理中修改</p>
      </div>
      <!-- 激活按钮(仅 DRAFT) -->
      <div v-if="selected.status === 'DRAFT'" class="activate-section">
        <button class="btn-activate" :disabled="loading" @click="handleActivate(selected.id)">
          {{ loading ? '激活中...' : '激活此版本' }}
        </button>
        <p class="activate-hint">激活后同 code 旧版本自动 RETIRED,历史计划不受影响。</p>
      </div>
      <div v-else-if="selected.status === 'ACTIVE'" class="status-info active">当前激活版本</div>
      <div v-else-if="selected.status === 'RETIRED'" class="status-info retired">已退役</div>

      <!-- 激活结果(警告) -->
      <div v-if="activateResult" class="activate-result">
        <div v-if="activateResult.warnings.length" class="msg warning">
          <strong>激活警告:</strong>
          <ul>
            <li v-for="(w, i) in activateResult.warnings" :key="i">{{ w }}</li>
          </ul>
        </div>
        <div v-else class="msg success">激活成功,无警告。</div>
        <p v-if="activateResult.retired_previous_id" class="retired-info">
          旧版本 #{{ activateResult.retired_previous_id }} 已自动退役。
        </p>
      </div>
    </div>

    <!-- 新建草稿表单 -->
    <div v-if="showCreateForm" class="modal-overlay" @click.self="showCreateForm = false">
      <div class="modal">
        <h4>新建策略草稿</h4>
        <div class="form-row">
          <label>策略代码</label>
          <input v-model="formCode" type="text" placeholder="default">
        </div>
        <div class="form-row">
          <label>版本名称</label>
          <input v-model="formName" type="text" placeholder="v2">
        </div>
        <div class="form-params">
          <div v-for="m in EDITABLE_STRATEGY_PARAMS" :key="m.key" class="param-edit">
            <label>{{ m.label }}<span v-if="m.highRisk" class="risk-tag">高风险</span></label>
            <input
              v-model.number="formParams[m.key]"
              :name="m.key"
              type="number" :step="m.format === 'percent' ? '0.001' : '1'"
              :min="m.min" :max="m.max"
            >
            <span class="param-range">范围 {{ fmtVal(m.min, m) }} ~ {{ fmtVal(m.max, m) }}</span>
          </div>
        </div>
        <div class="rules-guidance">
          <p v-for="rule in FIXED_RULES" :key="rule">{{ rule }}</p>
          <p>账户风险参数请在账户管理中修改</p>
        </div>
        <div class="modal-actions">
          <button class="btn-cancel" @click="showCreateForm = false">取消</button>
          <button class="btn-primary" :disabled="loading" @click="handleCreate">
            {{ loading ? '创建中...' : '创建草稿' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 高风险参数确认 -->
    <div v-if="showHighRiskConfirm" class="modal-overlay" @click.self="showHighRiskConfirm = false">
      <div class="modal confirm-modal">
        <h4>⚠️ 高风险参数变化确认</h4>
        <p class="confirm-text">以下高风险参数相比当前 ACTIVE 版本有所提高:</p>
        <ul class="risk-change-list">
          <li v-for="(c, i) in highRiskChanges" :key="i">{{ c }}</li>
        </ul>
        <p class="confirm-warning">提高这些参数会放大风险,请确认你理解后果。</p>
        <div class="modal-actions">
          <button class="btn-cancel" @click="showHighRiskConfirm = false">取消</button>
          <button class="btn-danger" :disabled="loading" @click="doCreate">
            {{ loading ? '创建中...' : '确认并创建' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.msg { padding: 0.5rem; border-radius: 4px; margin-bottom: 0.75rem; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }
.msg.warning { background: #422006; color: #fcd34d; }
.section { margin-bottom: 1.5rem; }
.section-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem; }
h4 { margin: 0 0 0.5rem 0; color: #ccc; }
.loading-hint, .empty-hint { color: #666; padding: 1rem; text-align: center; }
.version-list { display: flex; flex-direction: column; gap: 0.25rem; }
.version-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem; background: #1a1a1a; color: #ccc; border: 1px solid #333; border-radius: 4px; cursor: pointer; text-align: left; }
.version-item.active { border-color: #3b82f6; background: #1e3a5f; }
.version-badge { padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.75rem; font-weight: bold; }
.status-active { background: #14532d; color: #86efac; }
.status-draft { background: #333; color: #aaa; }
.status-retired { background: #1a1a1a; color: #666; }
.version-date { margin-left: auto; color: #666; font-size: 0.85rem; }
.param-table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
.param-table th, .param-table td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
.param-table th { color: #888; font-weight: 500; font-size: 0.85rem; }
.high-risk-row { background: rgba(239, 68, 68, 0.05); }
.risk-tag { display: inline-block; margin-left: 0.3rem; padding: 0 0.3rem; background: #7f1d1d; color: #fca5a5; font-size: 0.7rem; border-radius: 2px; }
.risk-cell { color: #999; font-size: 0.85rem; word-break: break-all; }
.activate-section { margin-top: 0.75rem; }
.btn-activate { padding: 0.5rem 1rem; background: #10b981; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-activate:disabled { opacity: 0.5; cursor: not-allowed; }
.activate-hint { font-size: 0.8rem; color: #888; margin-top: 0.25rem; }
.status-info { margin-top: 0.75rem; padding: 0.5rem; border-radius: 4px; font-size: 0.9rem; }
.status-info.active { background: #14532d; color: #86efac; }
.status-info.retired { background: #2a2a2a; color: #888; }
.activate-result { margin-top: 0.75rem; }
.retired-info { font-size: 0.8rem; color: #888; }
.btn-primary { padding: 0.4rem 0.8rem; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-cancel { padding: 0.4rem 0.8rem; background: #333; color: #ccc; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
.btn-danger { padding: 0.4rem 0.8rem; background: #ef4444; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
.modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 1.5rem; max-width: 500px; width: 90%; max-height: 80vh; overflow-y: auto; }
.confirm-modal { max-width: 450px; }
.confirm-text { color: #ccc; }
.risk-change-list { color: #fca5a5; padding-left: 1.5rem; }
.confirm-warning { color: #fcd34d; font-size: 0.85rem; }
.form-row { margin-bottom: 0.5rem; }
.form-row label { display: block; color: #888; font-size: 0.85rem; margin-bottom: 0.2rem; }
.form-row input { width: 100%; background: #111; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.4rem; }
.form-params { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; margin: 0.75rem 0; }
.param-edit { display: flex; flex-direction: column; gap: 0.15rem; }
.param-edit label { font-size: 0.8rem; color: #aaa; }
.param-edit input { background: #111; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.3rem; }
.param-range { font-size: 0.7rem; color: #666; }
.rules-guidance { margin: 0.75rem 0; padding: 0.5rem 0.75rem; border-left: 2px solid #475569; color: #94a3b8; font-size: 0.85rem; }
.rules-guidance p { margin: 0.2rem 0; }
.modal-actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 0.75rem; }

@media (max-width: 600px) {
  .form-params { grid-template-columns: 1fr; }
  .version-item { flex-wrap: wrap; }
}
</style>
