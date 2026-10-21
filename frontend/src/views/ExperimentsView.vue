<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../stores/auth'
import http, { ApiError } from '../api'
import { useRemote } from '../composables/useRemote'
import { CORE_ALGORITHMS, ALGORITHM_LABELS, ALGORITHM_EXPLANATIONS, recommendationEndpoint, parseHistoryUser } from '../recommendations'
import type { Algorithm, HistoryUser, Movie, Page, EvaluationResult, EventTraceResult } from '../types'
import StatePanel from '../components/StatePanel.vue'
import PageNav from '../components/PageNav.vue'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
import EvaluationTable from '../components/EvaluationTable.vue'
import EventTrace from '../components/EventTrace.vue'
import InterestDialog from '../components/InterestDialog.vue'
const route = useRoute(), router = useRouter(), auth = useAuth()
const source = ref('ml'), algorithm = ref('content'), selected = ref(''), mode = ref('single')
const userInput = ref(''), userInputError = ref(''), userPage = ref(1), ratingPage = ref(1), refreshKey = ref(0)
const users = useRemote<Page<HistoryUser>>(), ratings = useRemote<Page<Movie>>()
const available = ref<Algorithm[]>([]), registryError = ref<unknown>(null)
const evaluation = useRemote<EvaluationResult | null>(), evaluationMissing = ref(false)
const events = useRemote<EventTraceResult>()
const subject = computed(() => source.value === 'ml' ? (selected.value ? `ml:${selected.value}` : '') : auth.account?.subject_key || '')
const ratingCount = computed(() => subject.value && ratings.data.value ? ratings.data.value.total : null)
const activeAlgorithm = computed(() => mode.value === 'rrf' || mode.value === 'realtime' ? mode.value : algorithm.value)
const endpoint = computed(() => recommendationEndpoint(source.value, selected.value, !!auth.account, activeAlgorithm.value))
const contentEndpoint = computed(() => recommendationEndpoint(source.value, selected.value, !!auth.account, 'content'))
const itemcfEndpoint = computed(() => recommendationEndpoint(source.value, selected.value, !!auth.account, 'itemcf'))
const algorithmGroups = computed(() => [
  { label: '核心算法', items: available.value.filter(a => ['content', 'itemcf', 'als'].includes(a.algorithm)) },
  { label: '热门与冷启动', items: available.value.filter(a => ['popular', 'cold_start'].includes(a.algorithm)) },
])
function updateUrl() {
  void router.replace({ path: '/experiments', query: { source: source.value, algorithm: activeAlgorithm.value, ...(mode.value === 'compare' ? { view: 'compare' } : {}), ...(source.value === 'ml' && selected.value ? { user: selected.value } : {}) } })
}
function chooseId() {
  const parsed = parseHistoryUser(userInput.value)
  userInputError.value = parsed.error
  if (parsed.error) return
  selected.value = parsed.id; updateUrl()
}
async function fetchUsers(page = userPage.value) {
  userPage.value = page
  await users.load(async () => (await http.get<Page<HistoryUser>>('/demo/history-users', { params: { page, per_page: 20 } })).data)
  // Start with a returned real user, never an invented fixed ID. All pages remain reachable.
  if (source.value === 'ml' && route.query.user === undefined && !selected.value && users.data.value?.items[0]) { selected.value = users.data.value.items[0].ml_user_id; userInput.value = selected.value; updateUrl() }
}
function fetchRatings() {
  if (!subject.value) return
  const path = source.value === 'ml' ? `/demo/history-users/${selected.value}/ratings` : '/me/ratings'
  return ratings.load(async () => (await http.get<Page<Movie>>(path, { params: { page: ratingPage.value, per_page: 10 } })).data)
}
async function fetchRegistry() {
  try { available.value = (await http.get<{ items: Algorithm[] }>('/experiments/algorithms')).data.items; registryError.value = null }
  catch (e) { registryError.value = e }
}
function fetchEvaluation() {
  evaluationMissing.value = false
  return evaluation.load(async () => {
    try { return (await http.get<EvaluationResult>('/experiments/evaluations')).data }
    catch (e) { if (e instanceof ApiError && e.status === 501) { evaluationMissing.value = true; return null }; throw e }
  })
}
function fetchEvents() {
  if (source.value !== 'app' || !auth.account) return
  return events.load(async () => (await http.get<EventTraceResult>('/me/extensions/events')).data)
}
function refreshAll() { refreshKey.value++; void fetchRatings(); if (mode.value === 'realtime') void fetchEvents() }
// 兴趣偏好保存后（无论来自自动弹窗还是这里的手动入口），重新拉取当前算法的推荐。
const interestOpen = ref(false)
function refreshOnInterestsSaved() { refreshAll() }
window.addEventListener('cinelab:interests-saved', refreshOnInterestsSaved)
onBeforeUnmount(() => window.removeEventListener('cinelab:interests-saved', refreshOnInterestsSaved))
function onInterestDialogSaved() { interestOpen.value = false; refreshAll() }
function switchToApp() { source.value = 'app'; updateUrl() }
watch(() => route.query, () => {
  source.value = route.query.source === 'app' ? 'app' : 'ml'
  const requested = typeof route.query.algorithm === 'string' ? route.query.algorithm : 'content'
  algorithm.value = (CORE_ALGORITHMS as readonly string[]).includes(requested) ? requested : 'content'
  mode.value = requested === 'rrf' || requested === 'realtime' ? requested : route.query.view === 'compare' ? 'compare' : 'single'
  const parsed = parseHistoryUser(route.query.user)
  selected.value = parsed.id; userInput.value = parsed.input; userInputError.value = parsed.error
}, { immediate: true })
watch(subject, () => { ratingPage.value = 1; ratings.data.value = null; void fetchRatings() }, { immediate: true })
watch(() => [source.value, mode.value, auth.account?.account_id], () => { events.data.value = null; if (mode.value === 'realtime') void fetchEvents() }, { immediate: true })
void fetchUsers(); void fetchRegistry(); void fetchEvaluation()
</script>
<template>
  <div class="page lab-page">
    <div class="lab-page-heading"><div><p class="eyebrow">CINELAB / 操作与比较</p><h1>推荐实验台<span class="heading-dot">.</span></h1><p class="muted">选一个真实用户，追踪每一部电影为何被推荐。</p></div><div class="lab-edition"><span>五核心算法</span><strong>标准版 · RRF + Kafka</strong></div></div>
    <div class="lab-notice"><span class="status-pill">身份隔离</span><p>ml: 历史匿名用户只读；app: 是当前注册账号。离线评价与业务推荐分离，界面不会补造电影、指标或事件。</p></div>
    <div class="lab-layout">
      <aside class="lab-controls">
        <p class="eyebrow">01 / 选择观众</p><h2>实验设置</h2>
        <label for="subject-source">用户来源</label><el-select id="subject-source" v-model="source" @change="updateUrl"><el-option label="MovieLens 历史用户（只读）" value="ml" /><el-option label="当前注册账号" value="app" /></el-select>
        <template v-if="source==='ml'">
          <label for="history-user">真实历史用户列表</label><el-select id="history-user" v-model="selected" placeholder="从真实分页结果中选择" filterable @change="userInput=selected;updateUrl()"><el-option v-for="u in users.data.value?.items" :key="u.ml_user_id" :label="`${u.subject_key} · ${u.evaluation_role}`" :value="u.ml_user_id" /></el-select>
          <StatePanel v-if="users.error.value" :error="users.error.value" @retry="fetchUsers()" /><p v-if="users.loading.value" class="muted" role="status">正在加载真实用户…</p>
          <div class="user-pager"><el-button :disabled="userPage===1 || users.loading.value" @click="fetchUsers(userPage-1)">上一组</el-button><span>{{ userPage }} / {{ Math.ceil((users.data.value?.total || 0)/20) || 1 }}</span><el-button :disabled="!users.data.value || userPage*20>=users.data.value.total || users.loading.value" @click="fetchUsers(userPage+1)">下一组</el-button></div>
          <p class="form-hint">共 {{ users.data.value?.total ?? '—' }} 位真实历史用户；每组 20 位，保留全部访问。</p>
          <form class="history-id-form" @submit.prevent="chooseId"><label for="history-id">或输入已有用户 ID</label><div><el-input id="history-id" v-model="userInput" inputmode="numeric" placeholder="原始 ID，不含 ml:" :maxlength="20" /><el-button native-type="submit">切换</el-button></div><p v-if="userInputError" class="form-error" role="alert">{{ userInputError }}</p></form>
        </template>
        <div v-else class="current-subject"><p>{{ auth.account?.login_name || '请先登录自己的注册账号' }}</p><span>{{ auth.account?.subject_key || '尚未登录' }}</span><RouterLink v-if="!auth.account" :to="{path:'/login',query:{redirect:'/experiments?source=app&algorithm=cold_start'}}" class="text-link">登录 / 创建账号 →</RouterLink></div>
        <div class="subject-summary"><span class="small-label">当前实验主体</span><strong class="mono">{{ subject || '尚未选择' }}</strong><p>{{ ratingCount === null ? '评分数待读取' : `${ratingCount} 条真实评分` }}</p></div>
        <template v-if="mode==='single'"><label for="algorithm-select">推荐算法 · 来自服务端注册表</label><el-select id="algorithm-select" v-model="algorithm" @change="updateUrl"><el-option-group v-for="group in algorithmGroups" :key="group.label" :label="group.label"><el-option v-for="a in group.items" :key="a.algorithm" :label="`${ALGORITHM_LABELS[a.algorithm] || a.algorithm} · ${a.status}`" :value="a.algorithm" /></el-option-group></el-select></template>
        <StatePanel v-if="registryError" :error="registryError" @retry="fetchRegistry" />
        <div class="algorithm-scope"><strong>{{ mode==='compare' ? '比较同一位用户' : ALGORITHM_LABELS[activeAlgorithm] }}</strong><p>{{ mode==='compare' ? '内容推荐依据电影类型；ItemCF 依据交互邻居。比较候选与来源，而不是直接比较两边的分数。' : ALGORITHM_EXPLANATIONS[activeAlgorithm] }}</p></div>
        <RouterLink to="/movies" class="button primary onboarding-link">去选电影评分 →</RouterLink><p class="form-hint">零评分先看热门基线；保存有效评分，再返回刷新。收藏与评分是不同信号。</p>
      </aside>
      <div class="lab-results">
        <div class="lab-workbar"><div class="lab-mode-tabs" role="group" aria-label="推荐实验模式"><button :class="{active:mode==='single'}" @click="mode='single';updateUrl()">核心推荐</button><button :class="{active:mode==='compare'}" @click="mode='compare';updateUrl()">内容 vs ItemCF</button><button :class="{active:mode==='rrf'}" @click="mode='rrf';updateUrl()">RRF 融合</button><button :class="{active:mode==='realtime'}" @click="mode='realtime';updateUrl()">Kafka 实时</button></div><el-button v-if="auth.account" @click="interestOpen=true">设置兴趣偏好</el-button><el-button @click="refreshAll">刷新实验</el-button></div>
        <div v-if="source==='app' && ratingCount===0" class="cold-start-banner"><p class="eyebrow">从零开始 / COLD START</p><h2>先留下你的第一条评分</h2><p>现在的热门候选不是个性化。为真正看过的电影评分，再刷新；若仍回退热门，请检查正反馈与邻居是否足够。</p><RouterLink to="/movies" class="text-link">挑一部看过的电影 →</RouterLink></div>
        <div v-if="mode==='realtime' && source!=='app'" class="extension-gate"><span class="status-pill">仅限当前 app: 账号</span><h2>历史用户不能产生实时行为</h2><p>Kafka 追踪只读取当前登录账号，绝不会把 ml: 身份映射成 app:。</p><el-button type="primary" @click="switchToApp">切换到当前注册账号</el-button></div>
        <div v-else-if="mode==='compare'" class="algorithm-compare"><AlgorithmPanel label="内容推荐 · content" :endpoint="contentEndpoint" :rating-count="ratingCount" :refresh-key="refreshKey" :empty-subject="source==='ml' ? '请选择真实历史用户' : '登录后体验'" @loaded="fetchRatings" /><AlgorithmPanel label="协同过滤 · itemcf" :endpoint="itemcfEndpoint" :rating-count="ratingCount" :refresh-key="refreshKey" :empty-subject="source==='ml' ? '请选择真实历史用户' : '登录后体验'" @loaded="fetchRatings" /></div>
        <AlgorithmPanel v-else :label="`${ALGORITHM_LABELS[activeAlgorithm]} · ${activeAlgorithm}`" :endpoint="endpoint" :rating-count="ratingCount" :refresh-key="refreshKey" :empty-subject="source==='ml' ? '请选择真实历史用户' : '登录后体验'" @loaded="fetchRatings" />
        <section v-if="mode==='realtime' && source==='app' && auth.account" class="event-panel"><div class="section-top"><h2>Kafka 事件轨迹</h2><el-button :loading="events.loading.value" @click="fetchEvents">刷新消费状态</el-button></div><StatePanel :loading="events.loading.value" :error="events.error.value" @retry="fetchEvents"><EventTrace v-if="events.data.value" :trace="events.data.value" /></StatePanel></section>
        <section class="history-panel"><div class="section-top"><div><p class="eyebrow">02 / 真实行为依据</p><h2>{{ source==='ml' ? '历史评分' : '我的评分' }}</h2></div><span class="status-pill">{{ subject || '未选择用户' }} · {{ source==='ml' ? '只读' : '仅本人' }}</span></div>
          <StatePanel :loading="!!subject && ratings.loading.value" :error="subject ? ratings.error.value : null" :empty="!subject || !!ratings.data.value && !ratings.data.value.items.length" :title="subject ? '尚无评分记录' : '选择一位观众'" description="这里展示真实评分；没有记录时不伪造兴趣画像。" @retry="fetchRatings"><div class="table-scroll"><table class="lab-table"><thead><tr><th scope="col">电影</th><th scope="col">类型特征</th><th scope="col">MovieLens ID</th><th scope="col">{{ source==='ml' ? '历史评分' : '我的评分' }}</th></tr></thead><tbody><tr v-for="movie in ratings.data.value?.items" :key="movie.movie_id"><td><RouterLink :to="`/movies/${movie.movie_id}`" class="text-link">{{ movie.title_zh || movie.movielens_title }}</RouterLink></td><td class="muted">{{ movie.genres.join(' / ') || '类型未收录' }}</td><td class="mono">{{ movie.movie_id }}</td><td class="metric">{{ movie.rating }}</td></tr></tbody></table></div><PageNav :page="ratingPage" :size="10" :total="ratings.data.value?.total || 0" @change="ratingPage=$event;fetchRatings()" /></StatePanel>
        </section>
        <section class="evaluation-panel"><div class="section-top"><div><p class="eyebrow">03 / 固定协议 · 离线结果</p><h2>真实算法评价</h2></div><el-button :loading="evaluation.loading.value" @click="fetchEvaluation">读取评价文件</el-button></div><StatePanel :loading="evaluation.loading.value" :error="evaluation.error.value" @retry="fetchEvaluation"><p v-if="evaluationMissing" class="evaluation-footnote">评价接口返回 501：尚未产出真实文件。</p><EvaluationTable :result="evaluation.data.value" /></StatePanel></section>
      </div>
    </div>
    <InterestDialog :open="interestOpen" @close="interestOpen=false" @saved="onInterestDialogSaved" />
  </div>
</template>

