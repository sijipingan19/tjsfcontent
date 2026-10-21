<script setup lang="ts">
import type { Movie, RecommendationItem } from '../types'
import MoviePoster from './MoviePoster.vue'
import { formatMetric } from '../evaluations'
import { sourceLabel } from '../recommendations'
defineProps<{ items: RecommendationItem[]; movies: Record<string, Movie> }>()
</script>
<template>
  <ol class="recommendation-list">
    <li v-for="item in items" :key="item.movie_id" class="recommendation-row">
      <span class="recommendation-rank" :aria-label="`排名 ${item.rank}`">{{ String(item.rank).padStart(2, '0') }}</span>
      <RouterLink :to="`/movies/${item.movie_id}`" class="recommendation-poster" :aria-label="`查看电影 ${item.movie_id}`"><MoviePoster :path="movies[item.movie_id]?.poster_path" :title="movies[item.movie_id]?.movielens_title || `电影 #${item.movie_id}`" /></RouterLink>
      <div class="recommendation-info">
        <RouterLink :to="`/movies/${item.movie_id}`" class="recommendation-title">{{ movies[item.movie_id]?.title_zh || movies[item.movie_id]?.movielens_title || `电影 #${item.movie_id}` }}</RouterLink>
        <p class="muted">{{ movies[item.movie_id] ? (movies[item.movie_id].genres.join(' / ') || '类型未收录') : '资料暂不可用 · 仍可打开详情重试' }} <span class="mono">#{{ item.movie_id }}</span></p>
        <div class="recall-tags"><span v-for="source in item.recall_sources" :key="source">{{ sourceLabel(source) }}<template v-if="item.recall_ranks?.[source]"> · #{{ item.recall_ranks[source] }}</template></span><span v-if="!item.recall_sources?.length" class="muted">召回来源未提供</span></div>
        <p v-if="item.base_rank != null" class="rerank-evidence">基础名次 #{{ item.base_rank }} · 基础分数 {{ formatMetric(item.base_score) }} · 短期调整 {{ formatMetric(item.short_term_boost) }}</p>
        <details v-if="item.recall_sources?.length || item.recall_ranks" class="recall-detail"><summary>查看推荐来源 / 原始名次</summary><p v-for="(rank, source) in item.recall_ranks" :key="source">{{ sourceLabel(source) }} · 原始名次 {{ rank }}</p><p v-if="!item.recall_ranks">此结果未提供原始名次。</p></details>
      </div>
      <div class="recommendation-score"><small>模型分数</small><strong>{{ formatMetric(item.score) }}</strong></div>
    </li>
  </ol>
</template>
