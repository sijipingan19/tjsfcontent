# CineLab 推荐实验台（中文标准版）

## 本次前端范围

- 五种核心算法仍从 `/experiments/algorithms` 注册表读取；不扩充后端核心枚举。
- 历史用户从真实分页接口读取，可逐组访问全部用户，也可输入已有原始 ID。默认只选择接口实际返回的第一位用户；非法输入不会被修正为其他用户。
- 核心结果、同用户 Content / ItemCF 比较、独立 RRF 扩展、当前账号 Kafka 实时推荐共享真实结果列表。电影资料来自 `/movies/{movie_id}`；资料读取失败仍保留推荐 ID，不编造影片。
- 当前账号零评分明确标记非个性化起点；电影详情可评分、修改评分、收藏及取消收藏，保存后可返回实验台刷新。
- RRF 展示 `recall_sources` / `recall_ranks`；实时结果还展示基础名次、基础分数、短期调整、`ranking_changed`、已消费事件 UUID 和短期兴趣。
- ACK、消费完成、实时重排是不同状态。`static_fallback_no_events`、关闭、失败、过期状态不伪装成成功。
- 评价页只读 `/experiments/evaluations` 的已完成文件。501、未完成或空结果保留空态，缺失指标显示“—”，真实 0 正常显示；页面没有训练入口。
- 电影库搜索/分类/分页、详情、注册登录、片单与收藏分页全部保留。`api.ts`、会话恢复、鉴权、Cookie / CSRF、安全凭据手动恢复机制未改。

## 验证命令

Node >=22.12，使用原有 package-lock：

```sh
npm ci
npm test
npm run build
npm run test:layout
```

`npm test` 使用 Node 内置测试运行器及现有锁文件中的 Vue 编译器 / SSR / esbuild，不新增测试框架。测试包含 namespace / endpoint、冷启动真实性、非法 ID、真实文件指标映射、204 事件回执、Vue 组件实际 SSR 输出，以及页面接线检查。测试 fixture 仅存在于 tests，不是页面数据源。新增行为先运行缺失断言失败，再实现。

`npm run test:layout` 必须在构建后运行：临时启动 127.0.0.1:4179 的 Vite preview，使用安装的 Chrome（Windows）或 Playwright Chromium，检查 1440 / 390 / 320 宽度的首页、电影库、实验台、历史实时权限门和登录页，无请求拦截、不提交凭据。核查无横向溢出、无未捕获异常、非法 ID 反馈和 ml: 实时拒绝。退出会关闭临时浏览器及服务。报告写入 `test-results/layout-smoke.json`。此检查不等于推荐/数据库/Kafka 的真实 E2E。

## 联调契约

基础前缀 `/api/v1` 由原 axios 实例提供；所有推荐显式传 `limit=10`：

- `GET /demo/history-users/{id}/recommendations/{popular|content|itemcf|als|cold_start}`
- `GET /me/recommendations/{algorithm}`
- `GET /demo/history-users/{id}/extensions/rrf`
- `GET /me/extensions/rrf`
- `GET /me/extensions/realtime`
- `GET /me/extensions/events`（不提交账号 ID，仅由登录身份确定）

扩展结果接受 `algorithm, model_version, generated_at, subject_key, strategy, items`，items 的核心字段为 `movie_id, rank, score`，来源字段可选；实时额外字段以 `docs/student-extensions-api.md` 为准。未知 score 显示“—”，不是 0；模型分数不是电影评分，也不能跨算法直接比较。

评分和收藏写入 JSON 的 `event_status/event_id/event_error_code`，以及取消收藏 204 的 `X-Event-*` 响应头都会显示。业务保存成功但事件失败不会被标为 Kafka 成功，也不会自动重放写请求。

评价结构：`{status:'completed', items:[{algorithm,partition,user_count,precision_at_10,recall_at_10,coverage_at_10?,model_version?,...}], dataset_version, split_version}`。未知额外字段在原始凭据中保留；数字由真实文件提供。

## 仍需带真实服务验收

1. 注册、登录、会话过期、CSRF 恢复、评分修改、收藏/取消及片单分页；确认业务持久化。
2. 不同历史用户及同一用户 Content / ItemCF 的真实差异；五核心算法的排除已评分、版本及来源。
3. 新账号 0 评分 → 在真实电影上评分 → 返回刷新；区分有效画像和热门回退。
4. RRF 两路来源及名次；不能将跨算法分数直接相加。
5. Kafka 评分/收藏 ACK → 消费 UUID → 短期兴趣 → 基础排名与重排结果；断开 Kafka 后业务仍保存、事件明确 failed。
6. 离线程序真实生成结果文件后评价页显示数值、用户数和版本；缺失文件仍无示例数字。
