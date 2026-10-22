<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import http from '../api'
import { useAuth } from '../stores/auth'
import { useRemote } from '../composables/useRemote'
import { recommendationEndpoint } from '../recommendations'
import type { Movie, Page } from '../types'
import MovieCard from '../components/MovieCard.vue'
import StatePanel from '../components/StatePanel.vue'
import AlgorithmPanel from '../components/AlgorithmPanel.vue'
const auth = useAuth()
const { data, error, loading, load } = useRemote<Page<Movie>>()
const ratings = useRemote<Page<Movie>>()
const count = computed(() => auth.account && ratings.data.value ? ratings.data.value.total : null)
const nextEndpoint = computed(() => recommendationEndpoint('app', '', !!auth.account, 'cold_start'))
const popularEndpoint = computed(() => recommendationEndpoint('app', '', !!auth.account, 'popular'))
const fetchMovies = () => load(async () => (await http.get<Page<Movie>>('/movies', { params: { per_page: 4 } })).data)
function fetchRatings() { if (auth.account) return ratings.load(async () => (await http.get<Page<Movie>>('/me/ratings', { params: { page: 1, per_page: 1 } })).data) }
watch(() => auth.account?.account_id, () => { ratings.data.value = null; void fetchRatings() }, { immediate: true })
onMounted(fetchMovies)
</script>
<template>
  <div class="page home-page">
    <section class="cinema-desk">
      <div><p class="eyebrow">片刻 / CINELAB 推荐实验台</p><h1>你的喜好，<br />不止一种解法<span class="heading-dot">.</span></h1><p class="desk-description">从真实评分出发，比较内容、协同过滤与隐因子推荐。<br />让每一部入选电影，都有可检查的来源。</p><div class="hero-actions"><RouterLink class="button primary" :to="auth.account ? '/experiments?source=app&algorithm=cold_start' : '/experiments'">打开推荐实验台 ↗</RouterLink><RouterLink class="button subtle" to="/movies">去选电影评分</RouterLink></div></div>
      <aside class="desk-workflow" aria-label="个性化体验流程"><p class="small-label">观影偏好 / 可验证的闭环</p><ol><li><span>01</span><div><strong>留下真实评分</strong><small>从看过的电影开始，不替你猜偏好</small></div></li><li><span>02</span><div><strong>比较推荐与来源</strong><small>内容 · ItemCF · ALS · RRF 多路融合</small></div></li><li><span>03</span><div><strong>追踪行为后的变化</strong><small>Kafka 发送、消费、短期兴趣分别可查</small></div></li></ol></aside>
    </section>
    <div class="desk-status"><span class="status-pill">{{ auth.account ? auth.account.subject_key : '访客 · 历史用户只读体验' }}</span><p>{{ auth.account ? (count === null ? '正在读取你的真实评分；未确认画像前，不声称已个性化。' : count === 0 ? '零评分起点：先看热门候选，评分后刷新观察个性化。' : `已有 ${count} 条评分，推荐依据以服务端策略和召回来源为准。`) : '无需登录也能比较真实历史用户；评分、收藏与实时体验需要自己的注册账号。' }}</p><RouterLink v-if="!auth.account" to="/register" class="text-link">创建自己的账号 →</RouterLink><RouterLink v-else to="/profile" class="text-link">查看我的片单 →</RouterLink></div>
    <StatePanel v-if="ratings.error.value && auth.account" :error="ratings.error.value" @retry="fetchRatings" />
    <section class="recommendation-section"><div class="section-heading"><div><p class="eyebrow">本次推荐 / 服务端真实结果</p><h2>下一场，看什么<span class="heading-dot">.</span></h2><p class="muted">零评分不等于已有画像；热门基线始终明确标识。</p></div><RouterLink to="/experiments" class="text-link">比较算法与评价 →</RouterLink></div><div class="recommendation-columns"><AlgorithmPanel :label="count===0 ? '冷启动 · 从热门开始' : '内容画像 / 冷启动策略'" :endpoint="nextEndpoint" :rating-count="count" @loaded="fetchRatings" /><AlgorithmPanel label="热门基线 · 非个性化" :endpoint="popularEndpoint" :rating-count="count" /></div></section>
    <section class="browse-section"><div class="section-heading"><div><p class="eyebrow">电影档案 / 真实片库</p><h2>从一部看过的电影开始<span class="heading-dot">.</span></h2><p class="muted">此处按原始编号展示，不是个性化推荐。</p></div><RouterLink to="/movies" class="text-link">搜索全部电影 →</RouterLink></div><StatePanel :loading="loading" :error="error" :empty="!!data && !data.items.length" @retry="fetchMovies"><div class="movie-grid"><MovieCard v-for="movie in data?.items" :key="movie.movie_id" :movie="movie" /></div></StatePanel></section>
  </div>
</template>

