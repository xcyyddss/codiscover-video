/** Native DSH bridge. Host dependency loader adapted from dsh-bili-scroll (MIT). */
import { loadHostDeps } from './lib/hostdeps.mjs'
const { defineTool } = await loadHostDeps()
export const name = 'codiscover_video'
export const inject = ['tools']

export function localBaseUrl(value = 'http://127.0.0.1:8765') {
  const url = new URL(value)
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('CoDiscover Video API must be a local http://loopback:port origin')
  }
  return url.origin
}

export function apply(ctx, config = {}) {
  const base = localBaseUrl(config.baseUrl || process.env.CDV_API_URL)
  const request = async (path, body) => {
    const response = await fetch(base + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(120000),
    })
    let value
    try { value = await response.json() } catch { throw new Error(`CoDiscover Video HTTP ${response.status}`) }
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `CoDiscover Video HTTP ${response.status}`)
    return { result: value }
  }
  const objectOutput = {
    schema: { type: 'object', properties: {}, additionalProperties: true },
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
  }
  const add = (name, description, parameters, execute) => ctx.tools.register(defineTool({
    name, description, parameters, output: objectOutput, execute,
  }))
  const text = (description, required = false) => ({ type: 'string', description, ...(required ? { required: true } : {}) })
  add('cdv_status', '查看 CoDiscover Video 本地服务、数据源与任务状态。', {}, () => request('/api/status'))
  add('cdv_goals', '列出用户已建立的内容体验目标。', {}, () => request('/api/goals'))
  add('cdv_create_goal', '保存用户明确提出的体验目标，用于后续探索。', {
    text: text('用户希望获得什么体验', true), context: text('使用场景，可为空'),
  }, args => request('/api/goals', { text: args.text, context: args.context || '' }))
  add('cdv_explore', '为已有目标启动一次有界探索，保存视频证据。不会评论、私信或收藏。', {
    goal_id: text('目标ID', true), max_videos: { type: 'number', description: '本轮最多分析1-12条，默认4' },
  }, args => request('/api/jobs', { goal_id: args.goal_id, max_videos: args.max_videos ?? 4 }))
  add('cdv_jobs', '查看探索进度和需要用户扫码登录的暂停状态。', {}, () => request('/api/jobs'))
  add('cdv_recommend', '根据本地证据和用户体验反馈，返回可解释推荐。证据不足时应如实说明。', {
    goal_id: text('目标ID', true), query: text('可选本次具体需要'),
  }, args => request('/api/recommendations', { goal_id: args.goal_id, query: args.query || '' }))
  add('cdv_chat', '与用户共同澄清意图，引用本地视频证据；返回推荐和下一步问题。', {
    goal_id: text('目标ID', true), message: text('用户本次原话', true), session_id: text('已有会话ID，可省略'),
  }, args => request('/api/chat', { goal_id: args.goal_id, message: args.message, ...(args.session_id ? { session_id: args.session_id } : {}) }))
  add('cdv_feedback', '仅记录用户明确表达的体验反馈；不得替用户推测评分。', {
    goal_id: text('目标ID', true), video_id: text('视频ID，可为空'), rating: { type: 'number', description: '用户评分，1到5', required: true }, note: text('用户反馈原话'),
  }, args => request('/api/feedback', { goal_id: args.goal_id, video_id: args.video_id || null, rating: args.rating, note: args.note || '' }))
}
