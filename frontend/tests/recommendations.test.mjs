import test from 'node:test'
import assert from 'node:assert/strict'

// A missing feature is an assertion failure, not a missing-module crash.
const feature = await import('../src/recommendations.ts').catch(() => ({}))
const evaluation = await import('../src/evaluations.ts').catch(() => ({}))
const events = await import('../src/events.ts').catch(() => ({}))

test('business success and Kafka ACK/failed/disabled receipts remain distinct, including 204 headers', () => {
  assert.equal(typeof events.eventReceipt, 'function', '缺少 Kafka 写入回执解释')
  assert.equal(events.eventReceipt({}, {}), null, '旧后端没有回执时不能假称发布成功')
  const published = events.eventReceipt({ event_status: 'published', event_id: 'real-uuid' }, {})
  assert.equal(published.id, 'real-uuid')
  assert.match(published.message, /不代表.*消费/)
  const failed = events.eventReceipt({ event_status: 'failed', event_id: 'real-uuid', event_error_code: 'BROKER_UNAVAILABLE' }, {})
  assert.match(failed.message, /业务.*已保存/)
  assert.match(failed.message, /失败/)
  assert.equal(failed.errorCode, 'BROKER_UNAVAILABLE')
  const removed = events.eventReceipt(null, { 'x-event-status': 'published', 'x-event-id': 'remove-uuid' })
  assert.equal(removed.id, 'remove-uuid')
  assert.equal(removed.status, 'published')
  assert.match(events.eventReceipt({ event_status: 'disabled' }, {}).message, /未启用/)
})

test('routes five core algorithms and RRF to the real selected namespace', () => {
  assert.equal(typeof feature.recommendationEndpoint, 'function', '缺少真实推荐 endpoint 映射')
  for (const algorithm of ['popular', 'content', 'itemcf', 'als', 'cold_start']) {
    assert.equal(feature.recommendationEndpoint('ml', '987', false, algorithm), `/demo/history-users/987/recommendations/${algorithm}?limit=10`)
    assert.equal(feature.recommendationEndpoint('app', '987', true, algorithm), `/me/recommendations/${algorithm}?limit=10`)
  }
  assert.equal(feature.recommendationEndpoint('ml', '987', false, 'rrf'), '/demo/history-users/987/extensions/rrf?limit=10')
  assert.equal(feature.recommendationEndpoint('app', '', true, 'rrf'), '/me/extensions/rrf?limit=10')
  assert.equal(feature.recommendationEndpoint('app', '', true, 'realtime'), '/me/extensions/realtime?limit=10')
  assert.equal(feature.recommendationEndpoint('ml', '987', false, 'realtime'), null, '不能把 ml 用户映射到实时 app 账号')
  assert.equal(feature.recommendationEndpoint('app', '987', false, 'content'), null)
  for (const id of ['', '0', '014', '14/../me', 'ml:14', ' 14']) assert.equal(feature.recommendationEndpoint('ml', id, false, 'content'), null)
  assert.equal(feature.recommendationEndpoint('ml', '987', false, 'invented'), null)
})

test('evaluation view only accepts completed actual rows and preserves real zeros', () => {
  assert.equal(typeof evaluation.evaluationRows, 'function', '缺少真实离线评价映射')
  assert.deepEqual(evaluation.evaluationRows({ status: 'not_implemented', items: [] }), [])
  assert.deepEqual(evaluation.evaluationRows({ status: 'running', items: [{ algorithm: 'content', precision_at_10: .2, recall_at_10: .1 }] }), [])
  const row = { algorithm: 'content', partition: 'validation', user_count: 4209, precision_at_10: 0, recall_at_10: 0.03456, model_version: 'actual-file-v1' }
  assert.deepEqual(evaluation.evaluationRows({ status: 'completed', items: [row] }), [row])
  assert.equal(evaluation.formatMetric(0), '0.0000')
  assert.equal(evaluation.formatMetric(0.03456), '0.0346')
  for (const value of [null, undefined, NaN, Infinity, '0.5']) assert.equal(evaluation.formatMetric(value), '—')
})

test('history ID validation rejects rather than repairs malformed route tokens', () => {
  assert.equal(typeof feature.parseHistoryUser, 'function', '缺少历史 ID 的统一验证')
  assert.deepEqual(feature.parseHistoryUser(undefined), { id: '', input: '', error: '' })
  assert.deepEqual(feature.parseHistoryUser('987'), { id: '987', input: '987', error: '' })
  for (const token of ['014', ' 14', '0', 'ml:14', '14/../me']) {
    const state = feature.parseHistoryUser(token)
    assert.equal(state.id, '')
    assert.equal(state.input, token, '保留原输入，不静默改 ID')
    assert.match(state.error, /原始正整数/)
  }
})

test('ratings alone are not evidence of an effective personalized profile', () => {
  assert.equal(feature.recommendationState({ algorithm: 'content', items: [{ movie_id: '777' }] }, 3).personalized, null)
})

test('zero-rating and popular fallbacks never claim personalization', () => {
  assert.equal(typeof feature.recommendationState, 'function', '缺少冷启动与真实策略状态')
  assert.equal(feature.recommendationState({ algorithm: 'content', items: [] }, 0).personalized, false)
  assert.match(feature.recommendationState({ algorithm: 'content', items: [] }, 0).explanation, /评分/)
  assert.equal(feature.recommendationState({ algorithm: 'popular', items: [] }, 10).personalized, false)
  assert.equal(feature.recommendationState({ algorithm: 'itemcf', strategy: 'popular_fallback', items: [] }, 10).personalized, false)
  assert.equal(feature.recommendationState({ algorithm: 'content', items: [{ recall_sources: ['popular'] }] }, 10).personalized, false)
  assert.equal(feature.recommendationState({ algorithm: 'content', items: [] }, null).personalized, null, '未知画像不能冒充个性化')
  assert.equal(feature.recommendationState({ algorithm: 'content', items: [{ recall_sources: ['content'] }] }, 3).personalized, true)
})
