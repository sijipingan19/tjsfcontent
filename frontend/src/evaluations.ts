import type { EvaluationResult } from './types'

export function evaluationRows(result: EvaluationResult | null) {
  return result?.status === 'completed' && Array.isArray(result.items) ? result.items : []
}
export function formatMetric(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(4) : '—'
}
