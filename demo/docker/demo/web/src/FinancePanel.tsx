import { useEffect, useRef, useState } from 'react'
import { Download, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import './finance-panel.css'

type Quote = {quote: {total_fen: number; unit_price_fen: number; event_index: number}; signature: string}
type Receipt = {approved: boolean; receipt_id?: string; amount_fen?: number; message?: string}
type Order = {order_id: string; quantity: number; status: string; quotes: Quote[]; receipt?: Receipt}
type Evidence = {ts: number; kind: string; test?: string; passed?: boolean; order_id?: string; sender?: string; recipient?: string; detail?: unknown; [key:string]:unknown}
type State = {connection: {ready:boolean;error:string|null}; orders: Order[]; events: Evidence[]; statement: {accounts:{id:string;balance_fen:number}[];payments:Receipt[]} | null}
const status:Record<string,string>={QUOTING:'接收签名报价',AWAITING_AUTHORIZATION:'等待用户授权',PAYMENT_PENDING:'等待支付回执',PAID:'模拟支付完成',REJECTED:'已拒绝',SUBMIT_FAILED:'提交失败'}
const money=(n:number)=>new Intl.NumberFormat('zh-CN',{style:'currency',currency:'CNY'}).format(n/100)

export default function FinancePanel({open,onClose,onCapture}:{open:boolean;onClose:()=>void;onCapture:()=>Promise<void>}) {
  const dialog=useRef<HTMLDialogElement>(null)
  const [state,setState]=useState<State|null>(null)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [quantity,setQuantity]=useState(2)
  const [selected,setSelected]=useState('')
  useEffect(()=>{const node=dialog.current;if(open&&!node?.open)node?.showModal();else if(!open&&node?.open)node.close()},[open])
  useEffect(()=>{
    if(!open)return
    let active=true
    let timer:ReturnType<typeof setTimeout>
    const abort=new AbortController()
    async function poll(){
      try{
        const response=await fetch('/api/finance/state',{cache:'no-store',signal:abort.signal})
        if(!response.ok)throw new Error('金融业务服务未就绪，请使用金融演示启动脚本。')
        const data=await response.json()
        if(active){setState(data);setError('')}
      }catch(e){if(active&&!abort.signal.aborted)setError(e instanceof Error?e.message:'读取失败')}
      finally{if(active)timer=setTimeout(poll,1000)}
    }
    void poll()
    return()=>{active=false;clearTimeout(timer);abort.abort()}
  },[open])
  const order=state?.orders.find(o=>o.order_id===selected)??state?.orders[0]
  const quotes=[...(order?.quotes??[])].sort((a,b)=>a.quote.event_index-b.quote.event_index)
  const ready=state?.connection.ready===true
  async function act(action:string,extra:Record<string,unknown>={}){
    setBusy(true);setError('')
    try{
      await onCapture()
      const response=await fetch('/api/finance/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,order_id:order?.order_id,...extra})})
      const data=await response.json()
      if(!response.ok)throw new Error(data.error??'操作失败')
      if(action==='create')setSelected(data.result.order_id)
      const latest=await fetch('/api/finance/state',{cache:'no-store'})
      if(latest.ok)setState(await latest.json())
    }catch(e){setError(e instanceof Error?e.message:'操作失败')}
    finally{setBusy(false)}
  }
  function download(){
    const url=URL.createObjectURL(new Blob([JSON.stringify({exported_at:new Date().toISOString(),...state},null,2)],{type:'application/json'}))
    const a=document.createElement('a');a.href=url;a.download='金密杯-金融协作运行记录.json';a.click();URL.revokeObjectURL(url)
  }
  return <dialog ref={dialog} onCancel={onClose} onClose={onClose} className="finance-dialog" aria-labelledby="finance-title">
    <div className="finance-dialog-heading"><div><h2 id="finance-title">金融订单与账本</h2><p>订单、签名报价及支付回执通过真实 ATP 通信；关闭此窗口可查看原页面中的抓包记录。</p></div><Button variant="ghost" size="icon-sm" aria-label="关闭金融订单与账本" onClick={onClose}><X/></Button></div>
    <div className="finance-dialog-body">
      <div className="finance-panel-toolbar"><Badge variant={ready?'success':'secondary'}>{ready?'金融业务 ATP 已连接':'金融业务 ATP 未连接'}</Badge><span>使用虚构订单与测试余额，不发生真实扣款。</span><Button variant="outline" size="sm" disabled={!state} onClick={download}><Download/>导出金融记录</Button></div>
      {(error||state?.connection.error)&&<p role="alert" className="text-destructive text-xs">{error||state?.connection.error}</p>}
      <div className="finance-panel-grid">
        <Card><CardHeader><CardTitle className="text-sm">订单与签名报价</CardTitle><CardDescription className="text-xs">沿用商旅住宿业务，用户域 → 商户域 → 支付域。</CardDescription></CardHeader><CardContent className="space-y-3">
          <div className="finance-panel-create"><label htmlFor="finance-quantity">住宿晚数</label><input id="finance-quantity" type="number" min="1" max="7" value={quantity} onChange={e=>setQuantity(Number(e.target.value))}/><Button size="sm" disabled={busy||!ready||!Number.isInteger(quantity)||quantity<1||quantity>7} onClick={()=>void act('create',{quantity})}>创建订单并查询报价</Button></div>
          {!!state?.orders.length&&<><label htmlFor="finance-order" className="text-xs text-muted-foreground">查看订单</label><select id="finance-order" className="finance-panel-select" value={order?.order_id??''} onChange={e=>setSelected(e.target.value)}>{state.orders.map(o=><option key={o.order_id} value={o.order_id}>{o.order_id} · {status[o.status]??o.status}</option>)}</select></>}
          {order?<><div className="finance-panel-order"><code>{order.order_id}</code><Badge variant={order.status==='PAID'?'success':'secondary'}>{status[order.status]??order.status}</Badge></div><p className="text-xs text-muted-foreground">商旅住宿服务 · {order.quantity}晚 · 收款方 merchant-demo-001</p><div className="finance-panel-quotes">{[1,2,3].map(index=>{const q=quotes.find(q=>q.quote.event_index===index);return <div key={index}><span>报价事件 {index}</span><strong>{q?money(q.quote.unit_price_fen):'等待回执'}</strong><small>{q?'SM2签名已核验':'从商户域接收'}</small></div>})}</div><div className="finance-panel-total"><span>最低报价合计</span><strong>{quotes.length?money(Math.min(...quotes.map(q=>q.quote.total_fen))):'—'}</strong></div><Button size="sm" className="w-full" disabled={busy||!ready||order.status!=='AWAITING_AUTHORIZATION'} onClick={()=>void act('pay',{authorized:true})}><ShieldCheck/>确认订单并授权模拟支付</Button><p className="text-xs text-muted-foreground">点击即授权本订单按最低已核验报价模拟支付；成功状态以支付域回执为准。</p>{order.receipt&&<div className="finance-panel-receipt"><strong>{order.receipt.approved?'已收到支付域成功回执':'支付域拒绝了请求'}</strong><code>{order.receipt.receipt_id??order.receipt.message}</code>{order.receipt.amount_fen!==undefined&&<span>实记金额 {money(order.receipt.amount_fen)}</span>}</div>}</>:<p className="text-xs text-muted-foreground py-5">创建订单后，将接收三条可核验的商户报价。</p>}
        </CardContent></Card>
        <div className="space-y-3"><Card><CardHeader><div className="flex items-center justify-between gap-3"><CardTitle className="text-sm">模拟账户与账本</CardTitle><Button variant="ghost" size="icon-sm" aria-label="查询模拟账本" disabled={busy||!ready} onClick={()=>void act('statement')}><RefreshCw/></Button></div></CardHeader><CardContent>{state?.statement?<><div className="finance-panel-balances">{state.statement.accounts.map(a=><div key={a.id}><span>{a.id==='buyer@family.test'?'用户测试余额':'商户累计入账'}</span><strong>{money(a.balance_fen)}</strong></div>)}</div><p className="text-xs text-muted-foreground mt-3">支付域账本快照 · 已记账 {state.statement.payments.length} 笔</p></>:<p className="text-xs text-muted-foreground">通过ATP查询支付域账本，支付完成后也会自动查询。</p>}</CardContent></Card>
          <Card><CardHeader><CardTitle className="text-sm">业务规则验证</CardTitle><CardDescription className="text-xs">实际提交请求并核对回执；协议层安全检查与业务规则分别判定。</CardDescription></CardHeader><CardContent><div className="finance-panel-test-buttons">{[['amount','金额不符'],['payee','收款方不符'],['signature','报价被篡改'],['unauthorized','缺少授权'],['repeat','重复付款幂等']].map(([variant,label])=><Button key={variant} variant="outline" size="sm" disabled={busy||!ready||quotes.length!==3||(variant==='repeat'&&order?.status!=='PAID')} onClick={()=>void act('test',{variant})}>{label}</Button>)}</div><div className="finance-panel-tests">{state?.events.filter(e=>e.kind==='test').slice(0,5).map((e,i)=><div key={i}><code>{e.test}</code><Badge variant={e.passed?'success':'destructive'}>{e.passed?'通过':'未通过'}</Badge></div>)}</div></CardContent></Card>
        </div>
      </div>
      <details className="finance-panel-evidence"><summary>金融业务关联记录（{state?.events.length??0} 条）</summary><p>请求与回执关联记录在此查看；网络报文、协议路径及包头详情继续在原页面查看。</p>{state?.events.slice(0,60).map((e,i)=><details key={i}><summary>{new Date(e.ts*1000).toLocaleTimeString('zh-CN')} · {e.kind} · {e.order_id??e.test??''}</summary><pre>{JSON.stringify(e,null,2)}</pre></details>)}</details>
    </div>
  </dialog>
}
