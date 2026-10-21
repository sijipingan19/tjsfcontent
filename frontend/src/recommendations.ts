export const CORE_ALGORITHMS = ['popular', 'content', 'itemcf', 'als', 'cold_start'] as const
export const ALGORITHM_LABELS: Record<string, string> = {
  popular: '热门基线', content: '内容推荐', itemcf: 'ItemCF 协同过滤', als: 'Spark ALS',
  cold_start: '新用户冷启动', rrf: 'RRF 多路融合', realtime: 'Kafka 实时推荐',
}
export const ALGORITHM_EXPLANATIONS: Record<string, string> = {
  popular: '依据训练集的评分与支持度形成热门基线；这不是个性化推荐。',
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
  if (ratingCount === 0) return { personalized: false, label: '零评分 · 非个性化起点', explanation: '当前没有评分。先查看热门候选，去选电影评分，再刷新推荐；收藏不是评分。' }
  if (result.algorithm === 'popular' || /popular|no_history|no_positive/.test(strategy) || onlyPopular) return { personalized: false, label: '热门基线 · 非个性化', explanation: '当前结果来自热门基线或回退策略；有评分也可能缺少有效正反馈或可用邻居。' }
  if (ratingCount === null || !result.items.length || !result.items.some(item => item.recall_sources?.some(personalizedSource))) return { personalized: null, label: '查看服务端策略', explanation: '未确认有效画像；以服务端返回的策略、召回来源和结果为准。' }
  if (result.items.some(item => (item.recall_sources ?? []).some(source => source.startsWith('cold_start:interest_genre_profile')))) return { personalized: true, label: '兴趣画像 · 新用户冷启动', explanation: '根据你选择兴趣类型生成的冷启动画像召回；去选电影评分后，结果会转向行为画像。' }
  return { personalized: true, label: '基于当前用户行为', explanation: '根据该用户记录生成；展开来源可追溯召回依据。推荐分数不是电影评价，也不能跨算法直接比较。' }
}
