<script setup lang="ts">
import type { EventTraceResult } from '../types'
defineProps<{ trace: EventTraceResult }>()
</script>
<template>
  <div class="event-trace">
    <div class="event-status"><span class="status-pill">{{ trace.enabled === false ? 'Kafka 事件未启用' : trace.enabled === true ? 'Kafka 事件已启用' : '启用状态未提供' }}</span><code>{{ trace.subject_key }}</code></div>
    <p>消费状态 <strong>{{ typeof trace.consumer_status === 'object' ? JSON.stringify(trace.consumer_status) : trace.consumer_status || '未提供' }}</strong></p>
    <p class="muted">发送 ACK 不代表消费者已处理；磁盘中的兴趣状态也不能证明 broker 当前健康。</p>
    <p class="mono">最新事件：{{ trace.last_event_id || '暂无已消费事件' }}</p>
    <div v-if="trace.short_term && Object.keys(trace.short_term).length" class="interest-weights"><span v-for="(weight, genre) in trace.short_term" :key="genre">{{ genre }} <strong>{{ weight }}</strong></span></div>
    <div v-if="trace.recent_events?.length" class="table-scroll"><table class="lab-table event-table"><caption class="visually-hidden">当前账号真实已消费事件</caption><thead><tr><th scope="col">事件 UUID / 时间 UTC</th><th scope="col">动作</th><th scope="col">电影 / 评分</th><th scope="col">处理结果</th></tr></thead><tbody><tr v-for="event in trace.recent_events" :key="event.event_id"><td class="mono">{{ event.event_id }}<small>{{ event.occurred_at || '未提供' }}</small></td><td>{{ event.action || '未提供' }}</td><td><RouterLink v-if="event.movie_id" :to="`/movies/${event.movie_id}`" class="text-link">#{{ event.movie_id }}</RouterLink><small v-if="event.rating != null">评分 {{ event.rating }}</small></td><td>{{ event.result || event.status || '查看原始凭据' }}</td></tr></tbody></table></div>
    <p v-else class="inline-empty">暂无已消费事件。去电影详情评分或收藏，再刷新轨迹与推荐。</p>
    <details class="result-provenance"><summary>查看原始事件与短期兴趣凭据</summary><pre>{{ JSON.stringify(trace, null, 2) }}</pre></details>
  </div>
</template>
