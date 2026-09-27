import test from 'node:test'
import assert from 'node:assert/strict'
import { Trip, publicError } from './guards.mjs'

function reply(trip, data, nonce = 'reply') {
  return { nonce, from: trip.pending.to, to: 'travel@family.test', task_id: trip.taskId, context_id: trip.contextId, in_reply_to: trip.pending.nonce, body: JSON.stringify(data) }
}
function send(trip, subject, extra = {}) {
  const params = { subject, to: { search: 'search@hotel.test', subscribe: 'rates@hotel.test', 'book+pay': 'bill@payment.test' }[subject], ...extra }
  trip.validateSend(params); trip.sentRequest(params, { nonce: subject, status: 'accepted' })
}
const search = { kind: 'search-result', nights: 2, matches: [{ hotel: 'ParisGarden' }] }
const price = i => ({ kind: 'price-change', event_index: i, total_events: 3, hotel: 'ParisGarden', price: [180,170,190][i-1], currency: 'USD' })

test('reject stale, unrelated and forged sender messages; never pay without three events', () => {
  const t = new Trip()
  assert.throws(() => send(t, 'book+pay'))
  send(t, 'search')
  for (const fields of [{task_id:'old'}, {context_id:'old'}, {in_reply_to:'old'}, {from:'bill@payment.test'}]) assert.equal(t.accept({...reply(t, search), ...fields}), false)
  assert.throws(() => t.finalText('已付款'))
  assert.equal(t.accept(reply(t, search)), true)
  send(t, 'subscribe')
  assert.equal(t.accept(reply(t, price(1), 'p1')), true)
  assert.equal(t.accept(reply(t, price(1), 'duplicate-index')), false)
  assert.throws(() => send(t, 'book+pay'))
  assert.equal(t.accept(reply(t, price(2), 'p2')), true)
  assert.equal(t.accept(reply(t, price(3), 'p3')), true)
  assert.throws(() => send(t, 'book+pay', {hotel:'ParisGarden', nights:2, nightly_amount:180}))
  send(t, 'book+pay', {hotel:'ParisGarden', nights:2, nightly_amount:170})
  assert.throws(() => t.finalText('成功'))
  assert.equal(t.accept(reply(t, {kind:'payment-result', approved:true, simulation:true, hotel:'ParisGarden', nights:2, nightly_amount:170, total:340, currency:'USD', authorization_id:'sim-1'}, 'payment')), true)
  assert.match(t.finalText('made up'), /340/)
  assert.equal(t.summary().received_nonces.length, 5)
  assert.throws(() => send(t, 'book+pay', {hotel:'ParisGarden', nights:2, nightly_amount:170}))
})

test('equal prices are distinct events; unapproved payment is not success', () => {
  const t = new Trip(); send(t, 'search'); t.accept(reply(t, search)); send(t,'subscribe')
  for (let i=1;i<=3;i++) t.accept(reply(t, {...price(i),price:170}, `p${i}`))
  send(t,'book+pay',{hotel:'ParisGarden',nights:2,nightly_amount:170})
  t.accept(reply(t,{kind:'payment-result',approved:false,simulation:true,hotel:'ParisGarden',nights:2,nightly_amount:170,total:340,currency:'USD'},'declined'))
  assert.throws(() => t.finalText('success'), {code:'PAYMENT_DECLINED'})
})

test('provider failures use Chinese safe messages and quota precedes rate limit', () => {
  for (const [message,code] of [['401 bad key sk-secret','API_KEY'],['404 model_not_found','MODEL_UNAVAILABLE'],['429 insufficient_quota','QUOTA'],['429 rate_limit','RATE_LIMIT'],['request timed out','MODEL_TIMEOUT'],['fetch failed','NETWORK'],['unexpected secret','MODEL_ERROR']]) {
    const result=publicError(new Error(message))
    assert.equal(result.code,code); assert.match(result.message,/[\u4e00-\u9fff]/); assert.ok(!result.message.includes('secret'))
  }
})
