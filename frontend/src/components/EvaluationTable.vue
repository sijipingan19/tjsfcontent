<script setup lang="ts">
import { computed } from 'vue'
import type { EvaluationResult } from '../types'
import { evaluationRows, formatMetric } from '../evaluations'
const props = defineProps<{ result: EvaluationResult | null }>()
const rows = computed(() => evaluationRows(props.result))
</script>
<template>
  <div v-if="!rows.length" class="evaluation-empty" role="status"><span class="status-pill">等待真实评价</span><p>尚无已完成的评价文件，不填充示例指标。</p><small>此页面只读取离线结果，刷新不会触发训练或评价。</small></div>
  <template v-else>
    <div class="evaluation-versions"><p>数据版本 <code>{{ result?.dataset_version || '未提供' }}</code></p><p>划分版本 <code>{{ result?.split_version || '未提供' }}</code></p></div>
    <div class="table-scroll"><table class="lab-table"><caption class="visually-hidden">已完成的真实离线评价结果</caption><thead><tr><th scope="col">算法 / 分区</th><th scope="col">固定用户数</th><th scope="col">Precision@10</th><th scope="col">Recall@10</th><th scope="col">Coverage@10</th><th scope="col">模型版本</th></tr></thead><tbody><tr v-for="(row,index) in rows" :key="`${row.algorithm}-${row.partition}-${index}`"><th scope="row">{{ row.algorithm }}<small>{{ row.partition }}</small></th><td>{{ row.user_count ?? '—' }}</td><td class="metric">{{ formatMetric(row.precision_at_10) }}</td><td class="metric">{{ formatMetric(row.recall_at_10) }}</td><td>{{ formatMetric(row.coverage_at_10) }}</td><td class="mono">{{ row.model_version || '未提供' }}</td></tr></tbody></table></div>
    <p class="evaluation-footnote">指标来自后端已完成文件；“—”表示未提供，不代表零。train 拟合、validation 调参、test 最终报告；无结果用户不得被排除。具体评价口径以冻结协议与结果文件为准。</p>
    <details class="result-provenance"><summary>查看评价文件返回的全部元信息</summary><pre>{{ JSON.stringify(result, null, 2) }}</pre></details>
  </template>
</template>
