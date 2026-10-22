<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import http, { ApiError } from '../api'
import StatePanel from './StatePanel.vue'
import RecommendationList from './RecommendationList.vue'
import { recommendationState } from '../recommendations'
import type { AlgorithmResult, Movie } from '../types'
const props = withDefaults(defineProps<{ endpoint: string | null; label: string; emptySubject?: string; ratingCount?: number | null; refreshKey?: number }>(), { emptySubject: '登录后体验', ratingCount: null, refreshKey: 0 })
const emit = defineEmits<{ loaded: [] }>()
const loading = ref(false), error = ref<unknown>(null), unavailable = ref(false)
const result = ref<AlgorithmResult | null>(null), movies = ref<Record<string, Movie>>({})
const metadataMissing = ref(0), previousIds = ref<string[] | null>(null)
const state = computed(() => result.value ? recommendationState(result.value, props.ratingCount) : null)
const changed = computed(() => previousIds.value && result.value ? JSON.stringify(previousIds.value) !== JSON.stringify(result.value.items.map(i => i.movie_id)) : null)
let run = 0, lastEndpoint: string | null = null
onScopeDispose(() => { run++ })
async function load() {
  const seq = ++run
  const snapshot = lastEndpoint === props.endpoint && result.value ? result.value.items.map(i => i.movie_id) : null
  lastEndpoint = props.endpoint
  error.value = null; result.value = null; unavailable.value = false; loading.value = false; previousIds.value = null; movies.value = {}; metadataMissing.value = 0
  if (!props.endpoint) return
  loading.value = true
  try {
    const response = await http.get<AlgorithmResult>(props.endpoint)
    if (seq !== run) return
    if (!Array.isArray(response.data.items)) throw new ApiError(502, 'INVALID_RECOMMENDATION_RESPONSE', '推荐响应缺少真实 items，请检查后端契约')
    result.value = response.data; previousIds.value = snapshot; emit('loaded')
    const records = await Promise.all(response.data.items.map(async item => {
      try { return (await http.get<Movie>(`/movies/${encodeURIComponent(item.movie_id)}`)).data } catch { return null }
    }))
    if (seq !== run) return
    const found: Record<string, Movie> = {}
    for (const movie of records) if (movie) found[movie.movie_id] = movie
    movies.value = found; metadataMissing.value = records.filter(m => !m).length
  } catch (e) {
    if (seq !== run) return
    if (e instanceof ApiError && e.status === 501) unavailable.value = true
    else error.value = e
  } finally { if (seq === run) loading.value = false }
}
watch(() => [props.endpoint, props.refreshKey], load, { immediate: true })
</script>
<template>
  <section class="algorithm-panel" :aria-label="label">
    <div class="section-top"><h3>{{ label }}</h3><el-button :disabled="!endpoint || loading" :loading="loading" @click="load">刷新推荐</el-button></div>
    <StatePanel :loading="loading" :error="error" @retry="load">
      <div v-if="!endpoint || unavailable" class="algorithm-empty" role="status">
        <div><span class="status-pill">{{ !endpoint ? emptySubject : '服务尚未实现' }}</span><h3>{{ !endpoint ? '选择观众，开始探索' : '此推荐服务暂未接入' }}</h3><p>{{ !endpoint ? '从真实历史用户中选择，或登录自己的注册账号。' : '服务返回 501；不会用固定片单或随机结果代替。' }}</p></div>
        <RouterLink v-if="!endpoint && emptySubject === '登录后体验'" class="text-link" to="/login">前往登录 →</RouterLink>
      </div>
      <div v-else-if="result" class="recommendation-results">
        <div class="strategy-notice"><span class="status-pill" :class="{ personalized: state?.personalized }">{{ state?.label }}</span><p>{{ state?.explanation }}</p><p v-if="result.strategy" class="mono">服务端策略：{{ typeof result.strategy === 'string' ? result.strategy : JSON.stringify(result.strategy) }}</p></div>
        <div v-if="result.algorithm==='realtime'" class="realtime-summary"><p>消费状态：<strong>{{ typeof result.consumer_status === 'object' ? JSON.stringify(result.consumer_status) : result.consumer_status || '未提供' }}</strong></p><p>最新已消费事件 <code>{{ result.last_event_id || '无' }}</code></p><p>{{ result.strategy==='static_fallback_no_events' ? '没有已消费事件：当前是静态基线，不是实时个性化。' : result.ranking_changed === true ? '短期兴趣已改变基础排名。' : result.ranking_changed === false ? '相对基础候选，当前排序未改变；不等于事件未消费。' : '是否重排以服务端证据为准。' }}</p></div>
        <p v-if="changed !== null" class="refresh-comparison" role="status">与上次刷新相比：{{ changed ? '推荐顺序或候选发生变化' : '推荐列表未变化（可能仍在等待消费或行为未改变排序）' }}</p>
        <p v-if="metadataMissing" class="metadata-warning" role="status">{{ metadataMissing }} 部电影资料暂不可用；保留真实推荐 ID，可打开详情重试。</p>
        <RecommendationList v-if="result.items.length" :items="result.items" :movies="movies" />
        <div v-else class="inline-empty"><p>本次没有可用候选。可增加有效评分，或更换算法后重试。</p></div>
        <details class="result-provenance"><summary>模型版本与本次结果凭据</summary><dl><dt>模型</dt><dd>{{ result.model_version || '未提供' }}</dd><dt>用户</dt><dd>{{ result.subject_key || '由请求身份确定' }}</dd><dt>生成时间 UTC</dt><dd>{{ result.generated_at || '未提供' }}</dd><dt>数据版本</dt><dd>{{ result.dataset_version || '未提供' }}</dd><dt>划分版本</dt><dd>{{ result.split_version || '未提供' }}</dd></dl><pre v-if="result.source_models">{{ JSON.stringify(result.source_models, null, 2) }}</pre><pre v-if="result.short_term">短期兴趣 {{ JSON.stringify(result.short_term, null, 2) }}</pre><p v-if="previousIds" class="mono">上次电影 ID：{{ previousIds.join(', ') }}</p></details>
      </div>
    </StatePanel>
    <div class="recommendation-action"><RouterLink to="/movies" class="text-link">去选电影评分 →</RouterLink><small>评分或收藏后，返回这里刷新；不同算法分数不可直接横比。</small></div>
  </section>
</template>
