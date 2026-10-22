<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Star, Back } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import http, { errorMessage } from '../api'
import { useAuth } from '../stores/auth'
import { useRemote } from '../composables/useRemote'
import type { Movie, Page } from '../types'
import MoviePoster from '../components/MoviePoster.vue'
import StatePanel from '../components/StatePanel.vue'
import { eventReceipt, type EventReceipt } from '../events'
const route = useRoute(), router = useRouter(), auth = useAuth()
const { data: movie, error, loading, load } = useRemote<Movie>()
const score = ref<number | undefined>(), savedScore = ref<string | null>(null), favorited = ref(false)
const activityLoading = ref(false), activityError = ref<unknown>(null), saving = ref(false)
const lastReceipt = ref<EventReceipt | null>(null), operationSaved = ref(false)
let activityRun = 0
const id = computed(() => String(route.params.id))
function fetchMovie() { return load(async () => (await http.get<Movie>(`/movies/${encodeURIComponent(id.value)}`)).data) }
async function retryMovie() { await fetchMovie(); await fetchActivity() }
function people(value: unknown[] | null | undefined) { if (!value?.length) return '暂未收录'; return value.map(p => typeof p === 'string' ? p : (p && typeof p === 'object' && 'name' in p ? String(p.name) : '')).filter(Boolean).join(' / ') || '暂未收录' }
// There is no single-movie favorite endpoint: inspect the real paged list, do not invent one.
async function fetchActivity() {
  const seq = ++activityRun, currentId = id.value
  savedScore.value = null; score.value = undefined; favorited.value = false; activityError.value = null; activityLoading.value = false
  if (!auth.account || !movie.value) return
  activityLoading.value = true
  try {
    const rating = (await http.get<{rating:string|null}>(`/me/ratings/${currentId}`)).data.rating
    let found = false, page = 1
    while (seq === activityRun) {
      const list = (await http.get<Page<Movie>>('/me/favorites', { params: { page, per_page: 100 } })).data
      found = list.items.some(item => item.movie_id === currentId)
      if (found || page * 100 >= list.total) break
      page++
    }
    if (seq === activityRun) { savedScore.value = rating; score.value = rating ? Number(rating) : undefined; favorited.value = found }
  } catch (e) { if (seq === activityRun) activityError.value = e }
  finally { if (seq === activityRun) activityLoading.value = false }
}
async function rate() {
  if (score.value === undefined) return
  saving.value = true; activityError.value = null
  try {
    const response = await http.put<{rating:string}>(`/me/ratings/${id.value}`, { rating: score.value })
    savedScore.value = response.data.rating; lastReceipt.value = eventReceipt(response.data, response.headers); operationSaved.value = true
    ElMessage.success('评分已保存'); await fetchActivity()
  }
  catch (e) { activityError.value = e; ElMessage.error(errorMessage(e)) }
  finally { saving.value = false }
}
async function toggleFavorite() {
  saving.value = true; activityError.value = null
  try {
    const adding = !favorited.value
    const response = adding ? await http.put(`/me/favorites/${id.value}`, {}) : await http.delete(`/me/favorites/${id.value}`)
    lastReceipt.value = eventReceipt(response.data, response.headers); operationSaved.value = true
    ElMessage.success(adding ? '已加入收藏' : '已取消收藏'); await fetchActivity()
  }
  catch (e) { activityError.value = e; ElMessage.error(errorMessage(e)) }
  finally { saving.value = false }
}
watch(id, async () => { operationSaved.value = false; lastReceipt.value = null; await fetchMovie(); await fetchActivity() }, { immediate: true })
watch(() => auth.account?.account_id, () => { operationSaved.value = false; lastReceipt.value = null; void fetchActivity() })
</script>
<template>
  <div class="page detail-page"><RouterLink to="/movies" class="back-link"><el-icon aria-hidden="true"><Back /></el-icon> 返回电影库</RouterLink>
    <StatePanel :loading="loading" :error="error" @retry="retryMovie"><div v-if="movie" class="detail-layout"><aside><MoviePoster :path="movie.poster_path" :title="movie.movielens_title" /><p class="archive-id">MOVIELENS / #{{ movie.movie_id }}</p></aside><div class="detail-content"><p class="eyebrow">FILM DOSSIER</p><h1>{{ movie.title_zh || movie.movielens_title }}</h1><p v-if="movie.title_zh" class="original-title">{{ movie.movielens_title }}</p><div class="genre-tags"><el-tag v-for="g in movie.genres" :key="g" effect="plain">{{ g }}</el-tag></div><dl class="film-facts"><div><dt>上映年份</dt><dd>{{ movie.release_year || '暂未收录' }}</dd></div><div><dt>上映日期</dt><dd>{{ movie.release_date || '暂未收录' }}</dd></div><div><dt>片长</dt><dd>{{ movie.runtime_minutes ? `${movie.runtime_minutes} 分钟` : '暂未收录' }}</dd></div><div><dt>原始语言</dt><dd>{{ movie.original_language || '暂未收录' }}</dd></div><div class="wide"><dt>导演</dt><dd>{{ people(movie.directors) }}</dd></div><div class="wide"><dt>主要演员</dt><dd>{{ people(movie.cast_top10) }}</dd></div></dl><section class="synopsis"><h2>关于这部电影</h2><p>{{ movie.overview || '剧情简介暂未收录。你仍然可以为这部电影评分或加入收藏。' }}</p></section>
      <section class="rating-panel" aria-label="我的电影记录"><div class="section-top"><h2>留下你的观影印记</h2><span class="status-pill">{{ auth.account ? auth.account.subject_key : '尚未登录' }}</span></div>
        <template v-if="auth.account"><StatePanel :loading="activityLoading" :error="activityError" @retry="fetchActivity"><form @submit.prevent="rate"><label for="rating-input">我的评分 <strong>{{ savedScore || '尚未评分' }}</strong></label><div class="rating-controls"><el-input-number id="rating-input" v-model="score" :min="0.5" :max="5" :step="0.5" :precision="1" step-strictly :disabled="saving" placeholder="请选择" /><el-button type="primary" native-type="submit" :disabled="score === undefined || saving" :loading="saving">{{ savedScore ? '修改评分' : '保存评分' }}</el-button><el-button :icon="Star" :disabled="saving" :aria-pressed="favorited" @click="toggleFavorite">{{ favorited ? '取消收藏' : '收藏电影' }}</el-button></div><p class="form-hint">0.5—5.0 分，半星步长。评分只属于当前注册账号。</p></form></StatePanel></template>
        <div v-else class="inline-empty"><p>登录后记录评分，收藏想看的电影。</p><el-button type="primary" @click="router.push({path:'/login',query:{redirect:route.fullPath}})">登录后记录</el-button></div>
        <div v-if="auth.account" class="activity-next"><div v-if="operationSaved" class="event-receipt" :class="{'receipt-failed':lastReceipt?.status==='failed'}" role="status"><strong>{{ lastReceipt ? `事件状态 · ${lastReceipt.status}` : '业务操作已保存 · 事件状态未提供' }}</strong><p>{{ lastReceipt?.message || '服务器未提供 Kafka 回执，不能确认是否发送或消费。' }}</p><code v-if="lastReceipt?.id">事件 UUID {{ lastReceipt.id }}</code><small v-if="lastReceipt?.errorCode">{{ lastReceipt.errorCode }}</small></div><p>保存评分或收藏后，返回实验台刷新，检查真实推荐与消费状态。</p><div><RouterLink class="text-link" to="/experiments?source=app&algorithm=cold_start">刷新我的推荐 →</RouterLink><RouterLink class="text-link" to="/experiments?source=app&algorithm=realtime">追踪 Kafka 实时变化 →</RouterLink></div></div>
      </section>
    </div></div></StatePanel>
  </div>
</template>
