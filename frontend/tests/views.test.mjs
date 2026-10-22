import test from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import path from 'node:path'
import { build } from 'esbuild'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { createSSRApp, h } from 'vue'
import { renderToString } from '@vue/server-renderer'

const root = fileURLToPath(new URL('../', import.meta.url))
async function renderComponent(name, props) {
  const filename = path.join(root, 'src/components', name + '.vue')
  assert.ok(existsSync(filename), `缺少可操作组件 ${name}`)
  const outdir = path.join(root, 'node_modules/.cache/cinelab-tests')
  mkdirSync(outdir, { recursive: true })
  const outfile = path.join(outdir, name + '.mjs')
  await build({ entryPoints: [filename], outfile, bundle: true, platform: 'node', format: 'esm', packages: 'external', plugins: [{
    name: 'vue-ssr-test', setup(api) { api.onLoad({ filter: /\.vue$/ }, args => {
      const { descriptor } = parse(readFileSync(args.path, 'utf8'), { filename: args.path })
      const script = compileScript(descriptor, { id: name, genDefaultAs: '__sfc__' })
      const template = compileTemplate({ source: descriptor.template.content, filename: args.path, id: name, ssr: true, ssrCssVars: descriptor.cssVars, compilerOptions: { bindingMetadata: script.bindings } })
      assert.deepEqual(template.errors, [])
      return { contents: script.content + '\n' + template.code + '\n__sfc__.ssrRender=ssrRender; export default __sfc__', loader: 'ts', resolveDir: path.dirname(args.path) }
    }) }
  }] })
  const { default: Component } = await import(pathToFileURL(outfile).href + '?' + Date.now())
  const app = createSSRApp(Component, props)
  app.component('RouterLink', { props: ['to'], setup: (p, { slots }) => () => h('a', { href: typeof p.to === 'string' ? p.to : p.to.path }, slots.default?.()) })
  app.component('el-icon', { setup: (_, { slots }) => () => h('span', {}, slots.default?.()) })
  app.component('el-button', { setup: (_, { slots }) => () => h('button', {}, slots.default?.()) })
  return renderToString(app)
}

test('event trace distinguishes disabled, consumption and actual event evidence', async () => {
  const disabled = await renderComponent('EventTrace', { trace: { subject_key: 'app:77', enabled: false, recent_events: [] } })
  assert.match(disabled, /Kafka 事件未启用/)
  assert.doesNotMatch(disabled, /消费成功/)
  const html = await renderComponent('EventTrace', { trace: { subject_key: 'app:77', enabled: true, consumer_status: 'healthy', last_event_id: 'actual-event-uuid', short_term: { Drama: 1.5 }, recent_events: [{ event_id: 'actual-event-uuid', movie_id: '777', action: 'favorite_add', occurred_at: '2026-01-01T00:00:00Z', result: 'processed' }] } })
  for (const text of ['app:77', 'actual-event-uuid', '777', 'favorite_add', 'Drama', 'healthy']) assert.ok(html.includes(text), text)
  assert.match(html, /不代表消费者已处理/)
})

test('real-time list exposes actual base rank and short-term boost instead of hiding rerank evidence', async () => {
  const html = await renderComponent('RecommendationList', { items: [{ movie_id: '777', rank: 1, score: .095, base_rank: 8, base_score: .02, short_term_boost: .075 }], movies: {} })
  assert.match(html, /基础名次/)
  assert.match(html, /短期调整/)
  assert.match(html, /0\.0750/)
})

test('account flow connects real cold start, write receipts and recommendation refresh', () => {
  const home = readFileSync(path.join(root, 'src/views/HomeView.vue'), 'utf8')
  const detail = readFileSync(path.join(root, 'src/views/DetailView.vue'), 'utf8')
  const profile = readFileSync(path.join(root, 'src/views/ProfileView.vue'), 'utf8')
  assert.ok(home.includes('/me/ratings'), '首页缺少真实评分数读取')
  assert.ok(home.includes('cold_start'), '首页缺少冷启动入口')
  assert.ok(detail.includes('eventReceipt'), '评分收藏必须解释真实 Kafka 回执')
  assert.ok(detail.includes('response.headers'), '取消收藏 204 的事件信息必须读取响应头')
  assert.ok(profile.includes('algorithm=realtime'), '片单缺少实时轨迹返回入口')
})

test('lab wiring exposes all real users, independent extensions and comparison', () => {
  const source = readFileSync(path.join(root, 'src/views/ExperimentsView.vue'), 'utf8')
  for (const marker of ['history-id', 'recommendationEndpoint', 'EventTrace', 'EvaluationTable', 'compare', '/me/extensions/events']) assert.ok(source.includes(marker), `缺少实验台接入 ${marker}`)
  assert.doesNotMatch(source, /ref\(['"]14['"]\)/)
})

test('evaluation table renders completed file values and an honest missing state', async () => {
  const empty = await renderComponent('EvaluationTable', { result: null })
  assert.match(empty, /尚无已完成的评价文件/)
  assert.doesNotMatch(empty, /0\.0000/)
  const html = await renderComponent('EvaluationTable', { result: { status: 'completed', dataset_version: 'real-dataset', split_version: 'real-split', items: [{ algorithm: 'content', partition: 'validation', user_count: 4209, precision_at_10: 0, recall_at_10: 0.03456, model_version: 'actual-model' }] } })
  for (const text of ['0.0000', '0.0346', '4209', 'actual-model', 'real-dataset', 'real-split']) assert.ok(html.includes(text), text)
})

test('recommendation panel provides an actionable rate-then-refresh path without a selected subject', async () => {
  const html = await renderComponent('AlgorithmPanel', { endpoint: null, label: '内容推荐', ratingCount: 0 })
  assert.match(html, /刷新推荐/)
  assert.match(html, /去选电影评分/)
  assert.match(html, /href="\/movies"/)
  assert.doesNotMatch(html, /电影 #14/)
})

test('recommendation list renders actual titles, ranks, recall evidence and unknown metadata honestly', async () => {
  const html = await renderComponent('RecommendationList', {
    items: [{ movie_id: '777', rank: 1, score: 0, recall_sources: ['content', 'itemcf'], recall_ranks: { content: 3, itemcf: 5 } }, { movie_id: '778', rank: 2, score: null }],
    movies: { '777': { movie_id: '777', movielens_title: 'Fixture title from catalog', title_zh: '目录接口译名', genres: ['Drama'], poster_path: null } },
  })
  assert.match(html, /目录接口译名/)
  assert.match(html, /\/movies\/777/)
  assert.match(html, /content/)
  assert.match(html, /itemcf/)
  assert.match(html, /原始名次/)
  assert.match(html, /0\.0000/)
  assert.match(html, /电影 #778/)
  assert.match(html, /资料暂不可用/)
  assert.doesNotMatch(html, /undefined|NaN/)
})
