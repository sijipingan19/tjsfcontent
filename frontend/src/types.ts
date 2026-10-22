export interface Account { account_id: string; subject_key: string; login_name: string }
export interface Movie {
  movie_id: string; movielens_title: string; title_zh: string | null; genres: string[]
  release_year?: number | null; poster_path: string | null; original_title?: string | null
  overview?: string | null; release_date?: string | null; runtime_minutes?: number | null
  original_language?: string | null; directors?: unknown[] | null; cast_top10?: unknown[] | null
  rating?: string | null; created_at?: string; updated_at?: string; timestamp_utc?: number
}
export interface Page<T> { items: T[]; total: number; page: number; per_page: number }
export interface HistoryUser { ml_user_id: string; evaluation_role: string; subject_key: string }
export interface Algorithm { algorithm: string; status: string }
export interface RecommendationItem {
  movie_id: string; rank: number; score: number | null
  algorithm?: string; model_version?: string; recall_sources?: string[]; recall_ranks?: Record<string, number>
  base_rank?: number; base_score?: number | null; short_term_boost?: number | null
}
export interface AlgorithmResult {
  status?: string; algorithm: string; model_version: string | null; batch_id?: string | null
  dataset_version?: string; split_version?: string; runtime_mode?: string
  generated_at?: string; subject_key?: string; strategy?: string | Record<string, unknown>
  items: RecommendationItem[]; short_term?: unknown; last_event_id?: string | null
  consumer_status?: string | Record<string, unknown>; ranking_changed?: boolean; source_models?: unknown
}
export interface EventTraceResult {
  subject_key?: string; enabled?: boolean; consumer_status?: string | Record<string, unknown>
  last_event_id?: string | null; last_event_at?: string | null; short_term?: Record<string, number>
  recent_events?: { event_id: string; occurred_at?: string; movie_id?: string; action?: string; rating?: string | number | null; result?: string; status?: string; [key: string]: unknown }[]
  [key: string]: unknown
}
export interface EvaluationRow {
  algorithm: string; partition: string; user_count: number
  precision_at_10: number | null; recall_at_10: number | null; coverage_at_10?: number | null
  model_version?: string; [key: string]: unknown
}
export interface EvaluationResult {
  status: string; items?: EvaluationRow[]; dataset_version?: string; split_version?: string
  generated_at?: string; [key: string]: unknown
}
