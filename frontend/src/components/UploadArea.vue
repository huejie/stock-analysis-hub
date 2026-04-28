<script setup lang="ts">
const emit = defineEmits<{ upload: [file: File] }>()

function onClick() {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*'
  input.onchange = () => { if (input.files?.[0]) emit('upload', input.files[0]) }
  input.click()
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer?.files?.[0]) emit('upload', e.dataTransfer.files[0])
}
</script>

<template>
  <div
    class="upload-area"
    style="display:block"
    @click="onClick"
    @dragover.prevent
    @drop="onDrop"
  >
    <div class="upload-icon">+</div>
    <p class="upload-title">点击或拖拽图片到此处上传</p>
    <p class="upload-hint">支持 PNG、JPG 格式的股票热榜截图</p>
  </div>
</template>
