// Usage: set NODE_PATH to a directory containing Playwright, then node this file.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const out = path.join(__dirname,'evidence');
fs.mkdirSync(out,{recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1080},deviceScaleFactor:1});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400 && !(r.status()===409 && r.url().endsWith('/api/control/capture-start')))errors.push(`${r.status()} ${r.url()}`)});
  try{
    await page.goto('http://127.0.0.1:18088/',{waitUntil:'networkidle'});
    for(const title of ['实时拓扑','与旅行智能体对话','协议路径','数据包详情','四智能体执行视图']){
      await page.getByText(title,{exact:true}).waitFor();
    }
    if(await page.locator('.fin-sidebar').count())throw new Error('Unexpected replacement dashboard');
    await page.getByRole('button',{name:'金融订单与账本',exact:true}).click();
    await page.getByText('金融业务 ATP 已连接',{exact:true}).waitFor();
    await page.getByRole('button',{name:'创建订单并查询报价'}).click();
    await page.getByRole('button',{name:'确认订单并授权模拟支付'}).waitFor();
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('确认订单并授权模拟支付')).disabled);
    await page.getByRole('button',{name:'确认订单并授权模拟支付'}).click();
    await page.getByText('已收到支付域成功回执',{exact:true}).waitFor({timeout:60000});
    await page.getByRole('button',{name:'金额不符',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.finance-panel-tests')?.textContent.includes('PAYMENT_MISMATCH'));
    await page.getByRole('button',{name:'重复付款幂等',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('.finance-panel-tests')?.textContent.includes('IDEMPOTENT'));
    await page.getByRole('button',{name:'查询模拟账本'}).click();
    await page.waitForFunction(()=>document.querySelector('.finance-panel-balances'));
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(x=>x.textContent.includes('创建订单并查询报价')).disabled);
    await page.waitForTimeout(1500);
    await page.screenshot({path:path.join(out,'finance-panel.png'),fullPage:true});
    await page.locator('.finance-panel-evidence > summary').click();
    await page.locator('.finance-panel-evidence details > summary').first().click();
    await page.screenshot({path:path.join(out,'finance-evidence.png'),fullPage:true});
    const downloadPromise=page.waitForEvent('download');
    await page.getByRole('button',{name:'导出金融记录'}).click();
    const download=await downloadPromise;
    await download.saveAs(path.join(out,'browser-export.json'));
    await page.getByRole('button',{name:'关闭金融订单与账本'}).click();
    await page.waitForFunction(()=>document.querySelector('.timeline-list')?.querySelectorAll('button').length>0,{timeout:30000});
    await page.screenshot({path:path.join(out,'finance-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(out,'finance-mobile.png'),fullPage:true});
    const fits=await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth);
    if(!fits)throw new Error('Mobile horizontal overflow');
    if(errors.length)throw new Error(errors.join('\n'));
    await page.getByRole('button',{name:'金融订单与账本',exact:true}).click();
    const dialogFits=await page.evaluate(()=>{const d=document.querySelector('dialog');return d.getBoundingClientRect().right<=innerWidth && d.scrollWidth<=d.clientWidth+1});
    if(!dialogFits)throw new Error('Financial dialog overflows mobile viewport');
    await page.screenshot({path:path.join(out,'finance-panel-mobile.png'),fullPage:true});
    fs.writeFileSync(path.join(out,'browser-test.json'),JSON.stringify({passed:true,original_dashboard_preserved:true,original_panels:['topology','conversation','protocol','packet_detail','four_agent_audit'],financial_actions_captured_in_original_timeline:true,desktop:[1440,1080],mobile:[390,844],horizontal_overflow:false,console_errors:errors,operations:['create','authorize_pay','amount_mismatch','repeat_idempotency','statement','evidence','export']},null,2));
    console.log('Browser acceptance passed');
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
