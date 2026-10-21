<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { fetchInterests, saveInterests, errorMessage } from '../api'
import { genreLabel } from '../recommendations'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: []; saved: [genres: string[]] }>()

const selectable = ref<string[]>([])
const selected = ref<string[]>([])
const loading = ref(false), loadError = ref<unknown>(null)
const saving = ref(false), saveError = ref<unknown>(null)
const card = ref<HTMLElement>()

async function load() {
  loading.value = true; loadError.value = null
  try {
    const data = await fetchInterests()
    selectable.value = data.selectable
    selected.value = data.genres.filter(genre => data.selectable.includes(genre))
  } catch (e) { loadError.value = e }
  finally { loading.value = false }
}
function onKeydown(event: KeyboardEvent) { if (event.key === 'Escape') emit('close') }
function toggle(genre: string) {
  const index = selected.value.indexOf(genre)
  if (index >= 0) selected.value.splice(index, 1)
  else if (selected.value.length < 8) selected.value.push(genre)
}
const canConfirm = computed(() => selected.value.length >= 1 && selected.value.length <= 8)
async function confirm() {
  if (!canConfirm.value || saving.value) return
  saving.value = true; saveError.value = null
  try {
    const data = await saveInterests([...selected.value])
    emit('saved', data.genres)
  } catch (e) { saveError.value = e }
  finally { saving.value = false }
}
watch(() => props.open, open => {
  if (open) {
    saveError.value = null
    void load()
    window.addEventListener('keydown', onKeydown)
    void nextTick(() => card.value?.focus())
  } else window.removeEventListener('keydown', onKeydown)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div v-if="open" class="interest-overlay" @click.self="emit('close')">
    <div ref="card" class="interest-dialog" role="dialog" aria-modal="true" aria-label="设置兴趣偏好" tabindex="-1">
      <div class="interest-head">
        <div><p class="eyebrow">你的口味 / INTERESTS</p><h2>选择感兴趣的电影类型</h2></div>
        <el-button text aria-label="关闭弹窗" @click="emit('close')">关闭</el-button>
      </div>
      <p class="muted">选择 1—8 个喜欢的类型，我们会据此生成兴趣画像作为冷启动推荐依据；之后为电影评分，推荐会更准确。</p>
      <div v-if="loading" class="state-panel" role="status" aria-live="polite"><p>正在读取可选类型…</p></div>
      <div v-else-if="loadError" class="state-panel error-panel" role="alert">
        <h3>暂时无法加载兴趣选项</h3><p>{{ errorMessage(loadError) }}</p><el-button @click="load">重试</el-button>
      </div>
      <template v-else>
        <div class="interest-chips" role="group" aria-label="可选电影类型">
          <button v-for="genre in selectable" :key="genre" type="button" class="interest-chip"
            :class="{ selected: selected.includes(genre) }" :aria-pressed="selected.includes(genre)"
            :disabled="!selected.includes(genre) && selected.length >= 8" @click="toggle(genre)">{{ genreLabel(genre) }}</button>
        </div>
        <p class="interest-count" role="status">已选 {{ selected.length }} / 8</p>
        <p v-if="!canConfirm" class="form-hint">请选择 1-8 个感兴趣的类型</p>
        <div v-if="saveError" class="form-error" role="alert"><p>{{ errorMessage(saveError) }}</p></div>
        <div class="interest-actions">
          <el-button text :disabled="saving" @click="emit('close')">取消</el-button>
          <el-button type="primary" :loading="saving" :disabled="!canConfirm" @click="confirm">确认</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.interest-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: var(--el-mask-color, rgba(0, 0, 0, 0.6));
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px
}

.interest-dialog {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 32px;
  width: min(560px, 100%);
  max-height: 85vh;
  overflow: auto;
  box-shadow: 0 24px 60px rgba(10, 30, 45, 0.35);
  outline: none
}

.interest-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 10px
}

.interest-head h2 {
  font-size: 22px
}

.interest-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px
}

.interest-chip {
  border: 1px solid var(--border);
  background: var(--el-bg-color, #fff);
  color: var(--text);
  border-radius: 999px;
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 600;
  transition: background .18s, color .18s, border-color .18s
}

.interest-chip:hover {
  border-color: var(--accent)
}

.interest-chip.selected {
  background: var(--accent-tint);
  border-color: var(--accent);
  color: var(--accent)
}

.interest-chip:disabled {
  cursor: not-allowed;
  opacity: .55
}

.interest-count {
  margin-top: 16px;
  font-size: 13px;
  font-weight: 600
}

.form-hint {
  margin-top: 6px;
  font-size: 12px
}

.form-error {
  margin-top: 14px;
  margin-bottom: 0
}

.interest-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 22px
}

@media (max-width: 640px) {
  .interest-dialog {
    padding: 24px 18px
  }
}
</style>
