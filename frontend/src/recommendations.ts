export const CORE_ALGORITHMS = ['popular', 'content', 'itemcf', 'als', 'cold_start'] as const
export const ALGORITHM_LABELS: Record<string, string> = {
  popular: '热门基线', content: '内容推荐', itemcf: 'ItemCF 协同过滤', als: 'Spark ALS',
  cold_start: '新用户冷启动', rrf: 'RRF 多路融合', realtime: 'Kafka 实时推荐',
}
/** recall_sources 后端内部字符串 → 可读中文标签（Recommendations/RecommendationList 共用） */
export const SOURCE_LABELS: Record<string, string> = {
  'popular:positive_support_rank': '热门榜 · 正反馈支持度排名',
  'popular_fallback:no_positive_profile': '回退热门 · 无正反馈画像',
  'popular_fallback:no_personalized_candidates': '回退热门 · 无个性化候选',
  'content:positive_genre_cosine': '内容推荐 · 正反馈类型余弦',
  'itemcf:positive_binary_cosine_shrinkage': 'ItemCF · 正反馈共现余弦',
  'cold_start:itemcf_five_or_more_positive_ratings': '冷启动 · ItemCF(≥5正反馈)',
  'cold_start:content_few_positive_ratings': '冷启动 · 内容画像(少量正反馈)',
  'cold_start:interest_genre_profile': '冷启动 · 兴趣画像',
  'als:trained_user_factor_cosine': 'ALS · 训练用户因子余弦',
  'als:ridge_fold_in_cosine': 'ALS · Ridge折叠入因子余弦',
  'short_term:recent_interaction': '短期兴趣 · 近期交互',
  'profile:explicit_ratings_ge_4': '画像来源 · 评分≥4',
  'profile:interest_genres': '画像来源 · 用户兴趣类型',
  'profile:short_term_events': '画像来源 · 短期事件',
}
export function sourceLabel(source: string): string { return SOURCE_LABELS[source] ?? source }

/** 电影类型英文 → 中文（InterestDialog 显示用，保存仍用英文 key） */
export const GENRE_LABELS: Record<string, string> = {
  Action: '动作', Adventure: '冒险', Animation: '动画', Children: '儿童',
  Comedy: '喜剧', Crime: '犯罪', Documentary: '纪录片', Drama: '剧情',
  Fantasy: '奇幻', 'Film-Noir': '黑色电影', Horror: '恐怖', IMAX: 'IMAX',
  Musical: '歌舞', Mystery: '悬疑', Romance: '爱情', 'Sci-Fi': '科幻',
  Thriller: '惊悚', War: '战争', Western: '西部',
}
export function genreLabel(g: string): string { return GENRE_LABELS[g] ?? g }

export const ALGORITHM_EXPLANATIONS: Record<string, string> = {
  popular: '按训练集正反馈数量（评分≥4）形成热门基线；这不是个性化推荐。',
  content: '使用电影类型特征与用户正反馈构建兴趣画像，按内容相似度排序。',
  itemcf: '从真实训练交互学习电影之间的邻居关系，再根据用户喜欢的电影召回。',
  als: '使用 Spark ALS 学得的电影隐因子与用户画像进行排序；不同算法的分数不能直接比较。',
  cold_start: '没有评分时从热门基线开始；产生有效评分后，由后端选择可用的个性化策略。',
  rrf: '融合至少两路真实候选的名次，而非直接相加不同量纲的分数。展开每部电影可检查来源与原始名次。',
  realtime: '评分或收藏经过 Kafka 消费后形成短期兴趣；刷新推荐和事件轨迹，确认行为是否已生效。',
}
export function recommendationEndpoint(source: string, userId: string, authenticated: boolean, algorithm: string): string | null {
  if (![...CORE_ALGORITHMS, 'rrf', 'realtime'].includes(algorithm)) return null
  if (source !== 'ml' && source !== 'app') return null
  if (source === 'app' && !authenticated) return null
  if (source === 'ml' && (!/^[1-9][0-9]*$/.test(userId) || algorithm === 'realtime')) return null
  const base = source === 'ml' ? `/demo/history-users/${userId}` : '/me'
  const group = algorithm === 'rrf' || algorithm === 'realtime' ? 'extensions' : 'recommendations'
  return `${base}/${group}/${algorithm}?limit=10`
}

export function parseHistoryUser(value: unknown) {
  if (value === undefined) return { id: '', input: '', error: '' }
  const input = typeof value === 'string' ? value : ''
  return /^[1-9][0-9]*$/.test(input) ? { id: input, input, error: '' } : { id: '', input, error: '请输入原始正整数 ID（不含 ml:、空格或前导零）' }
}

interface StrategyInput { algorithm: string; strategy?: unknown; items: { recall_sources?: string[] }[] }
const personalizedSource = (source: string) => source.startsWith('short_term') || source.startsWith('profile:') || /^(content|itemcf|als)(:|$)/.test(source)
export function recommendationState(result: StrategyInput, ratingCount: number | null) {
  const strategy = typeof result.strategy === 'string' ? result.strategy : ''
  const onlyPopular = result.items.length > 0 && result.items.every(item => item.recall_sources?.length && item.recall_sources.every(source => source.startsWith('popular')))
  // 兴趣画像冷启动优先：零评分但已选择兴趣类型，后端走 interest_scores → 不可被 ratingCount===0 短路
  const hasInterestProfile = result.items.some(item => (item.recall_sources ?? []).some(source => source.startsWith('cold_start:interest_genre_profile')))
  if (hasInterestProfile) return { personalized: true, label: '兴趣画像 · 新用户冷启动', explanation: '根据你选择兴趣类型生成的冷启动画像召回；去选电影评分后，结果会转向行为画像。' }
  if (ratingCount === 0) return { personalized: false, label: '零评分 · 非个性化起点', explanation: '当前没有评分。先查看热门候选，去选电影评分，再刷新推荐；收藏不是评分。' }
  if (result.algorithm === 'popular' || /popular|no_history|no_positive/.test(strategy) || onlyPopular) return { personalized: false, label: '热门基线 · 非个性化', explanation: '当前结果来自热门基线或回退策略；有评分也可能缺少有效正反馈或可用邻居。' }
  if (ratingCount === null || !result.items.length || !result.items.some(item => item.recall_sources?.some(personalizedSource))) return { personalized: null, label: '查看服务端策略', explanation: '未确认有效画像；以服务端返回的策略、召回来源和结果为准。' }
  return { personalized: true, label: '基于当前用户行为', explanation: '根据该用户记录生成；展开来源可追溯召回依据。推荐分数不是电影评价，也不能跨算法直接比较。' }
}
