<script setup lang="ts">
import { ref, computed } from 'vue'
import type { AIAnalysisResult } from '../types'
import { useApi } from '../composables/useApi'

const api = useApi()

type AITab = 'review' | 'picker' | 'diagnosis'
const activeTab = ref<AITab>('review')

// ---- 通用导入状态 ----
const showImport = ref(false)
const importType = ref('daily_review')
const importDate = ref(new Date().toISOString().slice(0, 10))
const importContent = ref('')
const importing = ref(false)
const importError = ref('')

function openImport(type: string) {
  importType.value = type
  importDate.value = new Date().toISOString().slice(0, 10)
  importContent.value = ''
  importError.value = ''
  showImport.value = true
}

async function doImport() {
  if (!importContent.value.trim()) return
  importing.value = true
  importError.value = ''
  try {
    const resp = await fetch('/api/ai/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        analysis_type: importType.value,
        date: importDate.value,
        content: importContent.value,
        model: 'Hermes',
      }),
    })
    if (!resp.ok) throw new Error(await resp.text())
    showImport.value = false
    // 刷新当前 tab
    if (importType.value === 'daily_review') await loadReview()
    else if (importType.value === 'stock_pick') await loadStockPick()
    else if (importType.value === 'signal_diagnosis') await loadDiagnosis()
  } catch (e: unknown) {
    importError.value = e instanceof Error ? e.message : String(e)
  } finally {
    importing.value = false
  }
}

// ---- 每日复盘 ----
const reviewDate = ref(new Date().toISOString().slice(0, 10))
const review = ref<AIAnalysisResult | null>(null)
const reviewLoading = ref(false)

async function loadReview() {
  reviewLoading.value = true
  try {
    review.value = await (await fetch(`/api/ai/daily-review?date=${reviewDate.value}`)).json()
  } finally {
    reviewLoading.value = false
  }
}

// ---- 智能选股 ----
const pickResult = ref<AIAnalysisResult | null>(null)
const pickLoading = ref(false)

async function loadStockPick() {
  pickLoading.value = true
  try {
    pickResult.value = await (await fetch('/api/ai/stock-pick')).json()
  } finally {
    pickLoading.value = false
  }
}

// ---- 信号诊断 ----
const diagnosis = ref<AIAnalysisResult | null>(null)
const diagLoading = ref(false)

async function loadDiagnosis() {
  diagLoading.value = true
  try {
    diagnosis.value = await (await fetch('/api/ai/signal-diagnosis')).json()
  } finally {
    diagLoading.value = false
  }
}

// ---- 工具 ----
function renderMarkdown(text: string): string {
  return text
    .replace(/^### (.+)$/gm, '<h4 class="ai-h4">$1</h4>')
    .replace(/^## (.+)$/gm, '<h3 class="ai-h3">$1</h3>')
    .replace(/^# (.+)$/gm, '<h2 class="ai-h2">$1</h2>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul class="ai-ul">${m}</ul>`)
    .replace(/\n{2,}/g, '</p><p>')
    .replace(/^(?!<[hulo])/gm, '<p>')
    .replace(/(?<![>])$/gm, '</p>')
    .replace(/<p><\/p>/g, '')
}

// 初始加载
loadReview()

// 导出命令提示
const exportCmd = computed(() => {
  if (activeTab.value === 'review') return `python export_ai_data.py ${reviewDate.value}`
  if (activeTab.value === 'picker') return `python export_ai_data.py --type stock-pick`
  return `python export_ai_data.py --type diagnosis`
})
</script>

<template>
  <div class="ai-view">
    <!-- 子 tab -->
    <div class="ai-tabs">
      <button
        v-for="t in ([
          { key: 'review', label: '📋 每日复盘', desc: '每日分析和交易建议' },
          { key: 'picker', label: '🎯 智能选股', desc: '筛选推荐股票' },
          { key: 'diagnosis', label: '🩺 信号诊断', desc: '信号有效性评估' },
        ] as const)"
        :key="t.key"
        class="ai-tab-btn"
        :class="{ active: activeTab === t.key }"
        @click="activeTab = t.key"
      >
        <span class="ai-tab-label">{{ t.label }}</span>
        <span class="ai-tab-desc">{{ t.desc }}</span>
      </button>
    </div>

    <!-- 操作栏：导出数据 + 导入结果 -->
    <div class="ai-action-bar">
      <div class="ai-action-steps">
        <span class="step-num">1</span>
        <span>服务器运行:</span>
        <code class="step-cmd">{{ exportCmd }}</code>
      </div>
      <div class="ai-action-steps">
        <span class="step-num">2</span>
        <span>让 Hermes 读取导出文件 + prompts 模板，生成分析</span>
      </div>
      <div class="ai-action-steps">
        <span class="step-num">3</span>
        <button class="btn btn-primary btn-sm" @click="openImport(activeTab === 'picker' ? 'stock_pick' : activeTab === 'diagnosis' ? 'signal_diagnosis' : 'daily_review')">
          📋 导入分析结果
        </button>
      </div>
    </div>

    <!-- 每日复盘 -->
    <div v-if="activeTab === 'review'" class="ai-panel">
      <div class="ai-toolbar">
        <input type="date" v-model="reviewDate" class="ai-input" />
        <button class="btn btn-outline" @click="loadReview()">刷新</button>
      </div>

      <div v-if="review?.content" class="ai-report">
        <div class="ai-meta">
          {{ review.model || 'Hermes' }} | {{ review.generated_at?.slice(0, 19) || '' }}
        </div>
        <div class="ai-content" v-html="renderMarkdown(review.content)"></div>
      </div>
      <div v-else class="ai-empty">
        <p>{{ review?.message || '暂无分析结果' }}</p>
        <p class="empty-hint">请按上方步骤导出数据 → Hermes 分析 → 导入结果</p>
      </div>
    </div>

    <!-- 智能选股 -->
    <div v-if="activeTab === 'picker'" class="ai-panel">
      <div class="ai-toolbar">
        <button class="btn btn-outline" @click="loadStockPick()">刷新</button>
      </div>

      <div v-if="pickResult?.content" class="ai-report">
        <div class="ai-meta">
          {{ pickResult.model || 'Hermes' }} | {{ pickResult.generated_at?.slice(0, 19) || '' }}
        </div>
        <div class="ai-content" v-html="renderMarkdown(pickResult.content)"></div>
      </div>
      <div v-else class="ai-empty">
        <p>{{ pickResult?.message || '暂无选股结果' }}</p>
      </div>
    </div>

    <!-- 信号诊断 -->
    <div v-if="activeTab === 'diagnosis'" class="ai-panel">
      <div class="ai-toolbar">
        <button class="btn btn-outline" @click="loadDiagnosis()">刷新</button>
      </div>

      <div v-if="diagnosis?.content" class="ai-report">
        <div class="ai-meta">
          {{ diagnosis.model || 'Hermes' }} | {{ diagnosis.generated_at?.slice(0, 19) || '' }}
        </div>
        <div class="ai-content" v-html="renderMarkdown(diagnosis.content)"></div>
      </div>
      <div v-else class="ai-empty">
        <p>{{ diagnosis?.message || '暂无诊断结果' }}</p>
      </div>
    </div>

    <!-- 导入弹窗 -->
    <div v-if="showImport" class="modal-overlay" @click.self="showImport = false">
      <div class="modal-box">
        <h3>📋 导入 Hermes 分析结果</h3>
        <div class="modal-field">
          <label>分析类型</label>
          <select v-model="importType" class="ai-input">
            <option value="daily_review">每日复盘</option>
            <option value="stock_pick">智能选股</option>
            <option value="signal_diagnosis">信号诊断</option>
          </select>
        </div>
        <div class="modal-field">
          <label>日期</label>
          <input type="date" v-model="importDate" class="ai-input" />
        </div>
        <div class="modal-field">
          <label>分析内容（粘贴 Hermes 输出）</label>
          <textarea
            v-model="importContent"
            class="ai-textarea"
            rows="15"
            placeholder="将 Hermes 生成的分析内容粘贴到此处..."
          ></textarea>
        </div>
        <div v-if="importError" class="ai-error">{{ importError }}</div>
        <div class="modal-actions">
          <button class="btn btn-outline" @click="showImport = false">取消</button>
          <button class="btn btn-primary" :disabled="importing || !importContent.trim()" @click="doImport">
            {{ importing ? '导入中...' : '确认导入' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 20px;
}

.ai-tabs {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.ai-tab-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 14px 12px;
  background: rgba(30, 41, 59, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.15);
  border-radius: 12px;
  color: var(--text-muted, #94a3b8);
  cursor: pointer;
  transition: all 0.2s;
}

.ai-tab-btn:hover {
  border-color: rgba(59, 130, 246, 0.4);
  color: #e2e8f0;
}

.ai-tab-btn.active {
  background: rgba(59, 130, 246, 0.15);
  border-color: #3b82f6;
  color: #f1f5f9;
}

.ai-tab-label { font-size: 16px; font-weight: 600; }
.ai-tab-desc { font-size: 12px; opacity: 0.7; }

/* 操作栏 */
.ai-action-bar {
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ai-action-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #cbd5e1;
}

.step-num {
  width: 22px;
  height: 22px;
  background: #3b82f6;
  color: #fff;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

.step-cmd {
  background: rgba(30, 41, 59, 0.8);
  padding: 3px 10px;
  border-radius: 6px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: #93c5fd;
  white-space: nowrap;
}

.btn-sm { padding: 5px 14px; font-size: 13px; }

/* Toolbar */
.ai-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.ai-input {
  padding: 8px 12px;
  background: rgba(30, 41, 59, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 8px;
  color: #e2e8f0;
  font-size: 14px;
}

.ai-panel { min-height: 200px; }

/* Report */
.ai-report {
  background: rgba(30, 41, 59, 0.5);
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: 12px;
  padding: 24px;
}

.ai-meta {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
}

.ai-content {
  color: #cbd5e1;
  font-size: 14px;
  line-height: 1.8;
}
.ai-content :deep(.ai-h2) { font-size: 18px; font-weight: 700; color: #f1f5f9; margin: 24px 0 12px; padding-bottom: 8px; border-bottom: 1px solid rgba(148,163,184,0.15); }
.ai-content :deep(.ai-h3) { font-size: 16px; font-weight: 600; color: #e2e8f0; margin: 20px 0 8px; }
.ai-content :deep(.ai-h4) { font-size: 14px; font-weight: 600; color: #cbd5e1; margin: 16px 0 6px; }
.ai-content :deep(.ai-ul) { list-style: none; padding-left: 0; margin: 8px 0; }
.ai-content :deep(.ai-ul li) { padding: 4px 0 4px 20px; position: relative; }
.ai-content :deep(.ai-ul li)::before { content: '•'; position: absolute; left: 4px; color: #3b82f6; }
.ai-content :deep(p) { margin: 6px 0; }
.ai-content :deep(strong) { color: #f1f5f9; font-weight: 600; }

/* Empty */
.ai-empty {
  text-align: center;
  padding: 50px 0;
  color: #64748b;
  font-size: 15px;
}
.empty-hint { font-size: 13px; margin-top: 8px; opacity: 0.7; }

/* Error */
.ai-error {
  padding: 12px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 13px;
  margin-bottom: 12px;
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-box {
  background: #1e293b;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 16px;
  padding: 24px;
  width: 90%;
  max-width: 700px;
  max-height: 85vh;
  overflow-y: auto;
}

.modal-box h3 {
  color: #f1f5f9;
  margin: 0 0 18px;
  font-size: 18px;
}

.modal-field {
  margin-bottom: 14px;
}

.modal-field label {
  display: block;
  color: #94a3b8;
  font-size: 13px;
  margin-bottom: 6px;
}

.modal-field select,
.modal-field input {
  width: 100%;
}

.ai-textarea {
  width: 100%;
  padding: 12px;
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 8px;
  color: #e2e8f0;
  font-size: 14px;
  font-family: 'JetBrains Mono', monospace;
  line-height: 1.6;
  resize: vertical;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}

@media (max-width: 600px) {
  .ai-tabs { flex-direction: column; }
  .step-cmd { font-size: 11px; }
}
</style>
