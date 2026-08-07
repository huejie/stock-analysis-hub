<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useTradingApi } from '../../composables/useTradingApi'
import type { StockPoolListItem, StockPoolVersion, StockPoolItem } from '../../types/trading'

const api = useTradingApi()
const versions = ref<StockPoolListItem[]>([])
const selected = ref<StockPoolVersion | null>(null)
// 上一版本(用于 diff),versions 按版本号 desc 排列,所以 idx+1 即为更旧版本
const previousVersion = ref<StockPoolVersion | null>(null)
const inputText = ref('')
const inputSource = ref<'text' | 'csv'>('text')
const loading = ref(false)
const error = ref('')
const success = ref('')

// placeholder 用 computed 避免模板内多行字符串 + 字符实体的解析问题
const placeholderText = computed(() =>
  inputSource.value === 'text'
    ? '每行一只: 000001 平安银行\n600000 浦发银行\n300750'
    : 'code,name\n000001,平安银行\n600000,浦发银行'
)

type DiffStatus = 'added' | 'removed' | 'unchanged'

interface DiffItem {
  stock_code: string
  stock_name: string | null
  sector_name: string | null
  manual_blacklist: boolean
  status: DiffStatus
}

// 计算 selected 与 previousVersion 的 diff(按 stock_code 比对)
const diffItems = computed<DiffItem[]>(() => {
  const cur = selected.value
  const prev = previousVersion.value
  if (!cur) return []
  if (!prev) {
    // 没有上一版本,全部视为 unchanged(无 diff 标签)
    return cur.items.map((it) => ({
      stock_code: it.stock_code,
      stock_name: it.stock_name,
      sector_name: it.sector_name,
      manual_blacklist: it.manual_blacklist,
      status: 'unchanged' as DiffStatus,
    }))
  }
  const prevMap = new Map<string, StockPoolItem>()
  for (const it of prev.items) prevMap.set(it.stock_code, it)
  const curCodes = new Set(cur.items.map((i) => i.stock_code))
  const result: DiffItem[] = []
  // 当前版本中的项: 在 previous 中存在 → unchanged,否则 → added
  for (const it of cur.items) {
    result.push({
      stock_code: it.stock_code,
      stock_name: it.stock_name,
      sector_name: it.sector_name,
      manual_blacklist: it.manual_blacklist,
      status: prevMap.has(it.stock_code) ? 'unchanged' : 'added',
    })
  }
  // 上一版本中存在但当前版本缺失 → removed
  for (const it of prev.items) {
    if (!curCodes.has(it.stock_code)) {
      result.push({
        stock_code: it.stock_code,
        stock_name: it.stock_name,
        sector_name: it.sector_name,
        manual_blacklist: it.manual_blacklist,
        status: 'removed',
      })
    }
  }
  return result
})

const addedCount = computed(() => diffItems.value.filter((i) => i.status === 'added').length)
const removedCount = computed(() => diffItems.value.filter((i) => i.status === 'removed').length)
const hasDiff = computed(() => !!previousVersion.value)

async function loadVersions() {
  try {
    const res = await api.listStockPools()
    versions.value = res.versions
    const first = versions.value[0]
    if (first && !selected.value) {
      await selectVersion(first.id)
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function selectVersion(id: number) {
  try {
    selected.value = await api.getStockPool(id)
    // 找到当前版本在列表中的索引,加载下一项(更旧的版本)以做 diff
    previousVersion.value = null
    const idx = versions.value.findIndex((v) => v.id === id)
    const older = idx >= 0 ? versions.value[idx + 1] : undefined
    if (older) {
      try {
        previousVersion.value = await api.getStockPool(older.id)
      } catch {
        // 上一版本加载失败不影响主版本展示
        previousVersion.value = null
      }
    }
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}

async function handleImport() {
  if (!inputText.value.trim()) {
    error.value = '请输入股票代码'
    return
  }
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const result = await api.importStockPool({
      pool_name: 'default',
      source: inputSource.value,
      text_body: inputText.value,
    })
    success.value = result.reused
      ? `股票池未变化(版本 ${result.version_no})`
      : `导入成功:版本 ${result.version_no},共 ${result.items_count} 只`
    inputText.value = ''
    await loadVersions()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

onMounted(loadVersions)
</script>

<template>
  <div class="panel">
    <h3>股票池管理</h3>

    <!-- 导入区 -->
    <div class="import-section">
      <div class="source-tabs">
        <button :class="{ active: inputSource === 'text' }" @click="inputSource = 'text'">文本</button>
        <button :class="{ active: inputSource === 'csv' }" @click="inputSource = 'csv'">CSV</button>
      </div>
      <textarea
        v-model="inputText"
        :placeholder="placeholderText"
        rows="6"
      />
      <button class="btn-primary" :disabled="loading" @click="handleImport">
        {{ loading ? '导入中...' : '导入股票池' }}
      </button>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="success" class="msg success">{{ success }}</p>

    <!-- 版本列表 -->
    <div class="versions" v-if="versions.length">
      <h4>历史版本</h4>
      <div class="version-list">
        <button
          v-for="v in versions"
          :key="v.id"
          class="version-item"
          :class="{ active: selected?.id === v.id }"
          @click="selectVersion(v.id)"
        >
          v{{ v.version_no }} · {{ v.items_count }} 只 · {{ v.created_at.slice(0, 16) }}
        </button>
      </div>
    </div>

    <!-- 版本明细 -->
    <div class="items" v-if="selected">
      <h4>版本 v{{ selected.version_no }} 明细</h4>

      <!-- 版本 diff -->
      <div class="diff-section" v-if="hasDiff">
        <div class="diff-summary">
          <span class="diff-label">对比 v{{ previousVersion?.version_no }}:</span>
          <span class="chip chip-added">新增 {{ addedCount }}</span>
          <span class="chip chip-removed">删除 {{ removedCount }}</span>
          <span class="chip chip-unchanged" v-if="diffItems.length > addedCount + removedCount">
            不变 {{ diffItems.length - addedCount - removedCount }}
          </span>
        </div>
        <div class="diff-chips" v-if="addedCount || removedCount">
          <template v-for="item in diffItems" :key="item.stock_code + '-' + item.status">
            <span v-if="item.status === 'added'" class="chip chip-added">
              + {{ item.stock_code }} {{ item.stock_name || '' }}
            </span>
            <span v-else-if="item.status === 'removed'" class="chip chip-removed">
              − {{ item.stock_code }} {{ item.stock_name || '' }}
            </span>
          </template>
        </div>
      </div>

      <table>
        <thead>
          <tr><th>代码</th><th>名称</th><th>板块</th><th>黑名单</th><th v-if="hasDiff">变化</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in diffItems" :key="item.stock_code">
            <td>{{ item.stock_code }}</td>
            <td>{{ item.stock_name || '-' }}</td>
            <td>{{ item.sector_name || '-' }}</td>
            <td>{{ item.manual_blacklist ? '是' : '-' }}</td>
            <td v-if="hasDiff">
              <span v-if="item.status === 'added'" class="tag tag-added">新增</span>
              <span v-else-if="item.status === 'removed'" class="tag tag-removed">删除</span>
              <span v-else class="tag tag-unchanged">不变</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.panel { padding: 1rem; }
.import-section { display: flex; flex-direction: column; gap: 0.5rem; margin-bottom: 1rem; }
.source-tabs { display: flex; gap: 0.5rem; }
.source-tabs button { padding: 0.25rem 0.75rem; border: 1px solid #444; background: transparent; color: #ccc; border-radius: 4px; cursor: pointer; }
.source-tabs button.active { background: #3b82f6; color: white; border-color: #3b82f6; }
textarea { width: 100%; background: #1a1a1a; color: #eee; border: 1px solid #333; border-radius: 4px; padding: 0.5rem; font-family: monospace; resize: vertical; }
.btn-primary { padding: 0.5rem 1rem; background: #3b82f6; color: white; border: none; border-radius: 4px; cursor: pointer; align-self: flex-start; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.msg { padding: 0.5rem; border-radius: 4px; }
.msg.error { background: #451a1a; color: #fca5a5; }
.msg.success { background: #14532d; color: #86efac; }
.version-list { display: flex; flex-direction: column; gap: 0.25rem; }
.version-item { text-align: left; padding: 0.5rem; background: #1a1a1a; color: #ccc; border: 1px solid #333; border-radius: 4px; cursor: pointer; }
.version-item.active { border-color: #3b82f6; background: #1e3a5f; }
table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; }
th, td { padding: 0.4rem 0.6rem; text-align: left; border-bottom: 1px solid #2a2a2a; }
th { color: #888; font-weight: 500; }

/* 版本 diff */
.diff-section { background: #141414; border: 1px solid #2a2a2a; border-radius: 4px; padding: 0.6rem; margin-top: 0.5rem; }
.diff-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.diff-label { color: #aaa; font-size: 0.85rem; }
.chip { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-weight: 500; white-space: nowrap; }
.chip-added { background: #14532d; color: #86efac; }
.chip-removed { background: #451a1a; color: #fca5a5; }
.chip-unchanged { background: #2a2a2a; color: #999; }
.diff-chips { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }

.tag { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 3px; font-size: 0.78rem; font-weight: 500; }
.tag-added { background: #14532d; color: #86efac; }
.tag-removed { background: #451a1a; color: #fca5a5; }
.tag-unchanged { color: #666; }

@media (max-width: 600px) {
  .diff-summary { flex-direction: column; align-items: flex-start; gap: 0.3rem; }
  th, td { padding: 0.3rem 0.4rem; font-size: 0.85rem; }
}
</style>
