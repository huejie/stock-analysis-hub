<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import { useFormat } from '../../composables/useFormat'
import type { Account, AccountCreateRequest, AccountUpdateRequest } from '../../types/trading'

const api = useTradingApi()
const { formatMoney, formatPercent } = useFormat()
const emit = defineEmits<{ (e: 'select', accountId: number): void }>()

const accounts = ref<Account[]>([])
const selectedId = ref<number | null>(null)
const selected = ref<Account | null>(null)

const showCreate = ref(false)
const showEdit = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const success = ref('')

// 创建表单默认值(spec §12.5 默认风控参数)
const DEFAULT_FORM: AccountCreateRequest = {
  name: '',
  initial_equity: 100000,
  cash_balance: 100000,
  risk_per_trade: 0.02,
  max_single_position: 0.10,
  max_total_exposure: 1.0,
  max_sector_exposure: 0.30,
  max_positions: 10,
  max_drawdown_limit: 0.15,
  is_active: true,
}
const form = ref<AccountCreateRequest>({ ...DEFAULT_FORM })

// 编辑表单(initial_equity 不可编辑)
const editForm = ref<AccountUpdateRequest>({})

async function loadAccounts() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.listAccounts()
    accounts.value = res.accounts
    const first = accounts.value[0]
    if (first && selectedId.value == null) {
      await selectAccount(first.id)
    } else if (selectedId.value != null) {
      // 已选中时刷新详情
      await loadDetail(selectedId.value)
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function selectAccount(id: number) {
  selectedId.value = id
  emit('select', id)
  await loadDetail(id)
}

async function loadDetail(id: number) {
  error.value = ''
  try {
    selected.value = await api.getAccount(id)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
    selected.value = null
  }
}

function openCreate() {
  form.value = { ...DEFAULT_FORM, name: '' }
  showCreate.value = true
  success.value = ''
  error.value = ''
}

async function handleCreate() {
  if (!form.value.name.trim()) {
    error.value = '请输入账户名称'
    return
  }
  saving.value = true
  error.value = ''
  success.value = ''
  try {
    const created = await api.createAccount(form.value)
    success.value = `账户「${created.name}」创建成功`
    showCreate.value = false
    await loadAccounts()
    await selectAccount(created.id)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

function openEdit() {
  if (!selected.value) return
  const a = selected.value
  editForm.value = {
    cash_balance: a.cash_balance,
    risk_per_trade: a.risk_per_trade,
    max_single_position: a.max_single_position,
    max_total_exposure: a.max_total_exposure,
    max_sector_exposure: a.max_sector_exposure,
    max_positions: a.max_positions,
    max_drawdown_limit: a.max_drawdown_limit,
    is_active: a.is_active,
  }
  showEdit.value = true
  error.value = ''
  success.value = ''
}

async function handleUpdate() {
  if (!selected.value) return
  saving.value = true
  error.value = ''
  success.value = ''
  try {
    const updated = await api.updateAccount(selected.value.id, editForm.value)
    selected.value = updated
    success.value = '风控配置已更新'
    showEdit.value = false
    // 同步列表中的同名账户
    const idx = accounts.value.findIndex((x) => x.id === updated.id)
    if (idx >= 0) accounts.value[idx] = updated
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

function cancelEdit() {
  showEdit.value = false
}

function cancelCreate() {
  showCreate.value = false
}

watch(selectedId, (id) => {
  if (id != null) void loadDetail(id)
})

onMounted(loadAccounts)
</script>

<template>
  <div class="panel">
    <div class="header">
      <h3>账户管理</h3>
      <div class="header-actions">
        <button class="btn-secondary" :disabled="loading" @click="loadAccounts">
          {{ loading ? '刷新中...' : '刷新' }}
        </button>
        <button class="btn-primary" @click="openCreate">+ 新建账户</button>
      </div>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="success" class="msg success">{{ success }}</p>

    <!-- 账户选择 -->
    <div class="selector-row">
      <label class="field-label">选择账户</label>
      <select
        v-if="accounts.length"
        class="select"
        :value="selectedId ?? ''"
        @change="selectAccount(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="a in accounts" :key="a.id" :value="a.id">
          {{ a.name }} (ID:{{ a.id }})
        </option>
      </select>
      <span v-else class="text-muted">暂无账户,请先创建</span>
    </div>

    <!-- 创建表单(折叠) -->
    <div v-if="showCreate" class="form-card">
      <h4>新建账户</h4>
      <div class="form-grid">
        <div class="form-field">
          <label>账户名称 *</label>
          <input v-model="form.name" type="text" placeholder="如:主账户 / 模拟盘" />
        </div>
        <div class="form-field">
          <label>初始净值 *</label>
          <input v-model.number="form.initial_equity" type="number" min="0" step="0.01" />
        </div>
        <div class="form-field">
          <label>现金余额 *</label>
          <input v-model.number="form.cash_balance" type="number" min="0" step="0.01" />
        </div>
        <div class="form-field">
          <label>单笔风险占比 (risk_per_trade)</label>
          <input v-model.number="form.risk_per_trade" type="number" min="0" max="1" step="0.001" />
          <span class="hint">当前:{{ formatPercent(form.risk_per_trade) }}</span>
        </div>
        <div class="form-field">
          <label>单只最大仓位 (max_single_position)</label>
          <input v-model.number="form.max_single_position" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(form.max_single_position) }}</span>
        </div>
        <div class="form-field">
          <label>最大总敞口 (max_total_exposure)</label>
          <input v-model.number="form.max_total_exposure" type="number" min="0" step="0.01" />
          <span class="hint">当前:{{ formatPercent(form.max_total_exposure) }}</span>
        </div>
        <div class="form-field">
          <label>最大板块敞口 (max_sector_exposure)</label>
          <input v-model.number="form.max_sector_exposure" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(form.max_sector_exposure) }}</span>
        </div>
        <div class="form-field">
          <label>最大持仓数 (max_positions)</label>
          <input v-model.number="form.max_positions" type="number" min="0" step="1" />
        </div>
        <div class="form-field">
          <label>最大回撤限制 (max_drawdown_limit)</label>
          <input v-model.number="form.max_drawdown_limit" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(form.max_drawdown_limit) }}</span>
        </div>
        <div class="form-field checkbox-field">
          <label>
            <input v-model="form.is_active" type="checkbox" /> 启用账户
          </label>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn-primary" :disabled="saving" @click="handleCreate">
          {{ saving ? '保存中...' : '创建账户' }}
        </button>
        <button class="btn-secondary" :disabled="saving" @click="cancelCreate">取消</button>
      </div>
    </div>

    <!-- 账户详情 -->
    <div v-if="selected && !showEdit" class="detail-card">
      <div class="detail-header">
        <h4>{{ selected.name }} <span class="account-id">ID:{{ selected.id }}</span></h4>
        <span class="status-badge" :class="selected.is_active ? 'active' : 'inactive'">
          {{ selected.is_active ? '启用' : '停用' }}
        </span>
      </div>
      <div class="metrics-grid">
        <div class="metric">
          <span class="metric-label">初始净值</span>
          <span class="metric-value">{{ formatMoney(selected.initial_equity) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">现金余额</span>
          <span class="metric-value">{{ formatMoney(selected.cash_balance) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">单笔风险</span>
          <span class="metric-value">{{ formatPercent(selected.risk_per_trade) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">单只最大仓位</span>
          <span class="metric-value">{{ formatPercent(selected.max_single_position) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">最大总敞口</span>
          <span class="metric-value">{{ formatPercent(selected.max_total_exposure) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">最大板块敞口</span>
          <span class="metric-value">{{ formatPercent(selected.max_sector_exposure) }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">最大持仓数</span>
          <span class="metric-value">{{ selected.max_positions }}</span>
        </div>
        <div class="metric">
          <span class="metric-label">最大回撤限制</span>
          <span class="metric-value">{{ formatPercent(selected.max_drawdown_limit) }}</span>
        </div>
      </div>
      <div class="detail-footer">
        <button class="btn-primary" @click="openEdit">编辑风控配置</button>
      </div>
    </div>

    <!-- 编辑风控(内联) -->
    <div v-if="showEdit && selected" class="form-card">
      <h4>编辑风控配置 — {{ selected.name }}</h4>
      <p class="text-muted hint">初始净值不可编辑。</p>
      <div class="form-grid">
        <div class="form-field">
          <label>初始净值(只读)</label>
          <input :value="formatMoney(selected.initial_equity)" type="text" disabled />
        </div>
        <div class="form-field">
          <label>现金余额</label>
          <input v-model.number="editForm.cash_balance" type="number" min="0" step="0.01" />
        </div>
        <div class="form-field">
          <label>单笔风险 (risk_per_trade)</label>
          <input v-model.number="editForm.risk_per_trade" type="number" min="0" max="1" step="0.001" />
          <span class="hint">当前:{{ formatPercent(editForm.risk_per_trade) }}</span>
        </div>
        <div class="form-field">
          <label>单只最大仓位 (max_single_position)</label>
          <input v-model.number="editForm.max_single_position" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(editForm.max_single_position) }}</span>
        </div>
        <div class="form-field">
          <label>最大总敞口 (max_total_exposure)</label>
          <input v-model.number="editForm.max_total_exposure" type="number" min="0" step="0.01" />
          <span class="hint">当前:{{ formatPercent(editForm.max_total_exposure) }}</span>
        </div>
        <div class="form-field">
          <label>最大板块敞口 (max_sector_exposure)</label>
          <input v-model.number="editForm.max_sector_exposure" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(editForm.max_sector_exposure) }}</span>
        </div>
        <div class="form-field">
          <label>最大持仓数 (max_positions)</label>
          <input v-model.number="editForm.max_positions" type="number" min="0" step="1" />
        </div>
        <div class="form-field">
          <label>最大回撤限制 (max_drawdown_limit)</label>
          <input v-model.number="editForm.max_drawdown_limit" type="number" min="0" max="1" step="0.01" />
          <span class="hint">当前:{{ formatPercent(editForm.max_drawdown_limit) }}</span>
        </div>
        <div class="form-field checkbox-field">
          <label>
            <input v-model="editForm.is_active" type="checkbox" /> 启用账户
          </label>
        </div>
      </div>
      <div class="form-actions">
        <button class="btn-primary" :disabled="saving" @click="handleUpdate">
          {{ saving ? '保存中...' : '保存修改' }}
        </button>
        <button class="btn-secondary" :disabled="saving" @click="cancelEdit">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; flex-wrap: wrap; gap: 0.5rem; }
.header-actions { display: flex; gap: 0.5rem; }

.selector-row { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
.field-label { color: #888; font-size: 0.9rem; }
.select { background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.4rem 0.6rem; min-width: 200px; }

.msg { padding: 0.5rem; border-radius: 4px; margin-bottom: 0.75rem; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }
.text-muted { color: #888; }
.hint { color: #888; font-size: 0.8rem; }

.form-card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
.form-card h4 { margin: 0 0 0.75rem; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 0.75rem; }
.form-field { display: flex; flex-direction: column; gap: 0.25rem; }
.form-field label { color: #aaa; font-size: 0.85rem; }
.form-field input[type="text"],
.form-field input[type="number"] {
  background: #0f0f0f; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.4rem 0.6rem;
}
.form-field input:disabled { opacity: 0.6; }
.checkbox-field { justify-content: center; }
.checkbox-field label { display: flex; align-items: center; gap: 0.4rem; color: #eee; }
.form-actions { display: flex; gap: 0.5rem; margin-top: 0.75rem; }

.btn-primary { padding: 0.4rem 1rem; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { padding: 0.4rem 1rem; background: #333; color: #ccc; border: 1px solid #444; border-radius: 4px; cursor: pointer; }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

.detail-card { background: #1a1a1a; border: 1px solid #333; border-radius: 6px; padding: 1rem; }
.detail-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.detail-header h4 { margin: 0; }
.account-id { color: #666; font-size: 0.85rem; font-weight: normal; }
.status-badge { padding: 0.15rem 0.6rem; border-radius: 10px; font-size: 0.8rem; }
.status-badge.active { background: #14532d; color: #86efac; }
.status-badge.inactive { background: #451a1a; color: #fca5a5; }
.metrics-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 0.75rem; }
.metric { display: flex; flex-direction: column; gap: 0.15rem; }
.metric-label { color: #888; font-size: 0.8rem; }
.metric-value { color: #eee; font-weight: 500; }
.detail-footer { margin-top: 1rem; display: flex; gap: 0.5rem; }

@media (max-width: 600px) {
  .selector-row { flex-direction: column; align-items: flex-start; }
  .select { width: 100%; }
  .form-grid { grid-template-columns: 1fr; }
  .metrics-grid { grid-template-columns: 1fr 1fr; }
  .header { flex-direction: column; align-items: stretch; }
  .header-actions { justify-content: flex-end; }
}
</style>
