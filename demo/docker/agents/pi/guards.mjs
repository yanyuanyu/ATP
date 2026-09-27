import { randomUUID } from 'node:crypto'

export class TaskError extends Error {
  constructor(code, message) { super(message); this.code = code }
}

export function publicError(error) {
  const text = String(error?.message || error || '')
  const known = {
    TASK_TIMEOUT: '本次任务超过执行时限，已停止继续调用工具。已发送的付款请求不会自动重试。',
    ATP_TIMEOUT: '等待智能体回复超时，任务尚未完成。',
    INCOMPLETE: '任务证据不完整，不能报告成功。',
    PAYMENT_DECLINED: '模拟付款未获批准，预订尚未完成。',
    MODEL_TIMEOUT: '模型服务响应超时，请稍后重试。',
    MODEL_OUTPUT: '模型未返回有效结果，请重试。',
    STEP_LIMIT: '模型调用次数超过限制，任务已停止。',
  }
  if (known[error?.code]) return { code: error.code, message: known[error.code], retryable: false }
  const rules = [
    ['QUOTA', /insufficient[_ ]quota|quota.{0,20}exceed|balance|credit|余额|额度/i, '模型账户额度不足，请检查余额或套餐。', false],
    ['API_KEY', /401|invalid.?api.?key|incorrect.?api.?key|authentication|API key is not configured/i, '模型 API Key 无效、缺失或已失效，请检查配置。', false],
    ['MODEL_UNAVAILABLE', /model[_ ]not[_ ]found|model.{0,50}(not exist|not found|access|permission)|404|403/i, '模型不存在或账号无权访问，请核对模型名称和权限。', false],
    ['RATE_LIMIT', /429|rate.?limit|too many requests/i, '模型请求过于频繁，请稍后重试。', true],
    ['MODEL_TIMEOUT', /timeout|timed out|abort/i, '模型服务响应超时，请稍后重试。', true],
    ['NETWORK', /fetch failed|ECONN|ENOTFOUND|network|connecterror/i, '无法连接模型服务，请检查网络或稍后重试。', true],
  ]
  for (const [code, pattern, message, retryable] of rules) if (pattern.test(text)) return { code, message, retryable }
  return { code: 'MODEL_ERROR', message: '模型或智能体执行失败，请重试并查看运行记录。', retryable: false }
}

export class Trip {
  constructor() {
    this.taskId = randomUUID(); this.contextId = randomUUID()
    this.sent = []; this.received = []; this.prices = []; this.seen = new Set()
    this.search = null; this.payment = null; this.pending = null
  }
  validateSend(params) {
    if (this.pending) throw new TaskError('INCOMPLETE', '请先接收当前请求的回复')
    if (this.sent.some(x => x.subject === params.subject)) throw new TaskError('INCOMPLETE', '当前任务已发送过该请求，禁止重复发送')
    if (params.subject === 'subscribe' && !this.search) throw new TaskError('INCOMPLETE', '尚未收到搜索结果')
    if (params.subject === 'book+pay') {
      if (!this.search || this.prices.length !== 3) throw new TaskError('INCOMPLETE', '必须先收到搜索结果和三条价格')
      if (params.hotel !== this.prices[0].hotel || params.nights !== this.search.nights || params.nightly_amount !== Math.min(...this.prices.map(p => p.price))) throw new TaskError('INCOMPLETE', '付款参数与已验证价格和入住晚数不符')
      params.body = JSON.stringify({ hotel: params.hotel, nights: params.nights, nightly_amount: params.nightly_amount, currency: 'USD' })
    }
  }
  sentRequest(params, result) {
    if (!result.nonce || result.status !== 'accepted') throw new TaskError('INCOMPLETE', 'ATP 未确认请求')
    const entry = { ...params, nonce: result.nonce, status: result.status }
    this.sent.push(entry); this.pending = entry
  }
  accept(message) {
    const request = this.pending
    if (!request || this.seen.has(message.nonce) || !message.nonce || message.task_id !== this.taskId || message.context_id !== this.contextId || message.in_reply_to !== request.nonce || message.from !== request.to || message.to !== 'travel@family.test') return false
    let data
    try { data = JSON.parse(message.body) } catch { return false }
    if (request.subject === 'search') {
      if (data.kind !== 'search-result' || !Array.isArray(data.matches) || !Number.isInteger(data.nights) || data.nights < 1) return false
      this.search = data; this.pending = null
    } else if (request.subject === 'subscribe') {
      if (data.kind !== 'price-change' || data.total_events !== 3 || ![1,2,3].includes(data.event_index) || !Number.isFinite(data.price) || data.price <= 0 || data.currency !== 'USD' || !this.search.matches.some(x => x.hotel === data.hotel) || this.prices.some(x => x.event_index === data.event_index || x.hotel !== data.hotel)) return false
      this.prices.push(data)
      if (this.prices.length === 3) this.pending = null
    } else if (request.subject === 'book+pay') {
      if (data.kind !== 'payment-result' || data.simulation !== true || typeof data.approved !== 'boolean' || data.hotel !== request.hotel || data.nights !== request.nights || data.nightly_amount !== request.nightly_amount || data.total !== request.nights * request.nightly_amount || data.currency !== 'USD' || (data.approved && !data.authorization_id)) return false
      this.payment = data; this.pending = null
    } else return false
    this.seen.add(message.nonce); this.received.push(message); return true
  }
  summary() {
    return { task_id: this.taskId, context_id: this.contextId, search_received: !!this.search, price_count: this.prices.length, payment_received: !!this.payment, payment_approved: this.payment?.approved === true, pending: this.pending?.subject || null, received_nonces: [...this.seen], sent: this.sent, prices: this.prices, payment: this.payment }
  }
  finalText(fallback) {
    if (!this.sent.length) return fallback
    if (this.pending) throw new TaskError('INCOMPLETE', '仍缺少关联回复')
    if (this.payment) {
      if (!this.payment.approved) throw new TaskError('PAYMENT_DECLINED', '付款被拒绝')
      return `已完成模拟预订和付款：${this.payment.hotel}，${this.payment.nights} 晚，最低每晚 ${this.payment.nightly_amount} ${this.payment.currency}，总价 ${this.payment.total} ${this.payment.currency}。已核验搜索结果、三条价格及付款回执（${this.payment.authorization_id}）。未发生真实扣款。`
    }
    if (this.prices.length === 3) return `已收到三条价格，最低每晚 ${Math.min(...this.prices.map(p => p.price))} USD。尚未完成模拟预订或付款。`
    return `已收到搜索结果：${this.search.matches.map(x => x.hotel).join('、') || '没有符合条件的酒店'}。尚未完成模拟预订或付款。`
  }
}
