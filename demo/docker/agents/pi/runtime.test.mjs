import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { fileURLToPath } from 'node:url'
import readline from 'node:readline'

const tool = (name, args) => ({tool_calls:[{index:0,id:`call-${Math.random()}`,type:'function',function:{name,arguments:JSON.stringify(args)}}]})
const steps = [
  tool('atp_send',{to:'search@hotel.test',subject:'search',body:'Paris 2 nights'}), tool('atp_receive',{wait_seconds:1}),
  tool('atp_send',{to:'rates@hotel.test',subject:'subscribe',body:'ParisGarden'}), tool('atp_receive',{wait_seconds:1}),
  tool('atp_send',{to:'bill@payment.test',subject:'book+pay',body:'book',hotel:'ParisGarden',nights:2,nightly_amount:170}), tool('atp_receive',{wait_seconds:1}),
  {content:'完成'},
]

async function run(t, {status, error, sequence=steps, env={}, hang=false}={}) {
  let calls=0
  const server=http.createServer(async(req,res)=>{
    for await (const chunk of req) {} // consume the small test request
    calls++
    if(hang) return
    if(status) {res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{message:error,code:error}}));return}
    const delta=sequence[Math.min(calls-1,sequence.length-1)]
    res.writeHead(200,{'Content-Type':'text/event-stream'})
    for(const choice of [{index:0,delta:{role:'assistant',...delta},finish_reason:null},{index:0,delta:{},finish_reason:delta.tool_calls?'tool_calls':'stop'}]) res.write(`data: ${JSON.stringify({id:'chat-test',object:'chat.completion.chunk',created:1,model:'test-model',choices:[choice]})}\n\n`)
    res.end('data: [DONE]\n\n')
  })
  server.listen(0,'127.0.0.1');await once(server,'listening')
  const events=[]
  const child=spawn(process.execPath,[fileURLToPath(new URL('./runtime.mjs',import.meta.url)),'travel'],{env:{...process.env,LLM_API_KEY:'fake-test-key',LLM_API_BASE:`http://127.0.0.1:${server.address().port}/v1`,LLM_API_MODEL:'test-model',ATP_PI_ADAPTER:fileURLToPath(new URL('./fake_adapter.py',import.meta.url)),ATP_TASK_TIMEOUT_MS:'10000',...env},stdio:['pipe','pipe','pipe']})
  let stderr='';child.stderr.on('data',data=>stderr+=data)
  t.after(async()=>{child.kill(); server.closeAllConnections();server.close()})
  const result=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error(`test stalled: ${stderr}`)),20000)
    readline.createInterface({input:child.stdout}).on('line',line=>{
      const event=JSON.parse(line);events.push(event)
      if(event.type==='runtime_ready') {
        assert.equal(events.some(x=>x.status==='connected'),false)
        child.stdin.write(JSON.stringify({type:'prompt',id:'test-request',prompt:'请搜索酒店，订阅三次价格，按最低价格完成模拟预订付款。'})+'\n')
      }
      if(event.type==='prompt_result') {clearTimeout(timer);resolve(event)}
    })
    child.once('exit',code=>{clearTimeout(timer);reject(new Error(`runtime exited ${code}: ${stderr}`))})
  })
  return {result,events,calls}
}

test('real Pi loop completes only with five correlated replies',async t=>{
  const {result,events,calls}=await run(t)
  assert.equal(result.status,'completed');assert.equal(result.evidence.price_count,3);assert.equal(result.evidence.payment_approved,true)
  assert.equal(result.evidence.received_nonces.length,5);assert.match(result.text,/340/);assert.equal(calls,7)
  assert.ok(events.some(x=>x.type==='model_status'&&x.status==='connected'))
})
for(const [status,error,code] of [[401,'invalid_api_key','API_KEY'],[404,'model_not_found','MODEL_UNAVAILABLE'],[429,'insufficient_quota','QUOTA'],[429,'rate_limit_exceeded','RATE_LIMIT']]) test(`provider ${error} is a failure, not empty success`,async t=>{
  const {result,events}=await run(t,{status,error})
  assert.equal(result.status,'failed');assert.equal(result.error.code,code);assert.ok(!events.some(x=>x.status==='connected'))
})
test('model claim without receipt cannot become success',async t=>{
  const {result}=await run(t,{sequence:[steps[0],{content:'付款成功'}]})
  assert.equal(result.status,'failed');assert.equal(result.error.code,'INCOMPLETE')
})
test('ATP wait is bounded inside one receive call',async t=>{
  const {result,calls}=await run(t,{env:{FAKE_NO_REPLY:'1',ATP_STAGE_TIMEOUT_MS:'200'}})
  assert.equal(result.status,'failed');assert.equal(result.error.code,'ATP_TIMEOUT');assert.equal(calls,2)
})
test('model timeout is classified',async t=>{
  const {result}=await run(t,{hang:true,env:{LLM_API_TIMEOUT:'0.2'}})
  assert.equal(result.status,'failed');assert.equal(result.error.code,'MODEL_TIMEOUT')
})
test('total deadline aborts the pending model call',async t=>{
  const {result,calls}=await run(t,{hang:true,env:{ATP_TASK_TIMEOUT_MS:'300'}})
  assert.equal(result.status,'failed');assert.equal(result.error.code,'TASK_TIMEOUT');assert.equal(calls,1)
})
