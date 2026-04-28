<script setup lang="ts">
import { ref, computed, provide } from 'vue'
import { useRoute } from 'vue-router'
import type { ViewTab, StockRecord } from './types'
import { useApi } from './composables/useApi'
import DailyView from './views/DailyView.vue'
import PnlView from './views/PnlView.vue'
import RangeView from './views/RangeView.vue'
import UploadArea from './components/UploadArea.vue'
import ConfirmModal from './components/ConfirmModal.vue'

const route = useRoute()
const api = useApi()

const isAdmin = computed(() => route.meta.isAdmin === true)
provide('isAdmin', isAdmin)

const currentTab = ref<ViewTab>('daily')
const showUpload = ref(false)
const toastMsg = ref('')
const toastVisible = ref(false)
const toastColor = ref('#10b981')

function showToast(msg: string, type: 'success' | 'error' = 'success') {
  toastMsg.value = msg
  toastColor.value = type === 'success' ? '#10b981' : '#ef4444'
  toastVisible.value = true
  setTimeout(() => { toastVisible.value = false }, 2500)
}

// Upload flow
const parsedData = ref<{ date: string; records: StockRecord[] } | null>(null)
const showConfirm = ref(false)

async function handleFile(file: File) {
  try {
    parsedData.value = await api.uploadImage(file)
    showConfirm.value = true
  } catch (e: unknown) {
    showToast('识别失败: ' + (e instanceof Error ? e.message : String(e)), 'error')
  }
}

async function confirmSave(data: { date: string; records: StockRecord[] }) {
  try {
    await api.saveRecords(data)
    showConfirm.value = false
    parsedData.value = null
    showUpload.value = false
    showToast(`保存成功，共 ${data.records.length} 只股票`)
    // Switch to daily and reload
    currentTab.value = 'daily'
  } catch (e: unknown) {
    showToast('保存失败: ' + (e instanceof Error ? e.message : String(e)), 'error')
  }
}

const currentView = computed(() => {
  switch (currentTab.value) {
    case 'daily': return DailyView
    case 'pnl': return PnlView
    case 'range': return RangeView
  }
})
</script>

<template>
  <div>
    <!-- Nav -->
    <nav class="nav">
      <div class="nav-brand">
        <div class="logo">S</div>
        <h1>StockPulse</h1>
      </div>
      <div class="nav-controls">
        <div class="tabs">
          <button
            v-for="tab in ([
              { key: 'daily', label: '日报' },
              { key: 'pnl', label: '盈亏走势' },
              { key: 'range', label: '时段分析' },
            ] as const)"
            :key="tab.key"
            class="btn btn-outline"
            :class="{ active: currentTab === tab.key }"
            @click="currentTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>
        <button v-if="isAdmin" class="btn btn-primary" @click="showUpload = !showUpload">
          上传图片
        </button>
      </div>
    </nav>

    <!-- Toast -->
    <div class="toast" :class="{ show: toastVisible }" :style="{ borderColor: toastColor, color: toastColor }">
      {{ toastMsg }}
    </div>

    <div class="content">
      <!-- Upload Area -->
      <UploadArea
        v-if="showUpload && isAdmin"
        @upload="handleFile"
      />

      <!-- Main Content -->
      <component :is="currentView" />
    </div>

    <!-- Confirm Modal -->
    <ConfirmModal
      v-if="showConfirm && parsedData"
      :data="parsedData"
      @save="confirmSave"
      @cancel="showConfirm = false; parsedData = null"
    />
  </div>
</template>
