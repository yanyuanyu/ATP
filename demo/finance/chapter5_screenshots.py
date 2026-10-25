"""Capture readable report-sized crops of the real application, without UI edits."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT=Path(__file__).resolve().parents[3]/'第五章测试材料'/'06_应用协作与截图'/'报告截图'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1400},device_scale_factor=2)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:18088/',wait_until='networkidle')
        page.get_by_text('四智能体执行视图',exact=True).wait_for()
        page.screenshot(path=str(OUT/'00_系统完整运行界面.png'),full_page=True)
        page.locator('.topology-card').screenshot(path=str(OUT/'01_三域实时拓扑.png'))
        page.locator('.chat-card').screenshot(path=str(OUT/'02_模型任务与模拟付款结果.png'))
        page.locator('.chat-message.is-assistant .chat-bubble').last.screenshot(path=str(OUT/'02b_模型已核验模拟付款回执.png'))
        page.locator('.agent-trace > summary').last.click()
        page.locator('.agent-trace').last.screenshot(path=str(OUT/'03_模型ATP工具执行轨迹.png'))
        page.locator('.agent-activities').last.evaluate('(e)=>e.scrollTo(0,e.scrollHeight)')
        page.locator('.agent-trace').last.screenshot(path=str(OUT/'03b_模型ATP工具执行轨迹_付款与取信.png'))
        page.locator('.agent-activities').last.evaluate('(e)=>e.scrollTo(0,0)')
        card=page.locator('.chat-card').bounding_box()
        trace=page.locator('.agent-trace').last.bounding_box()
        page.screenshot(path=str(OUT/'02c_模型任务输入结果与工具轨迹.png'),clip={'x':card['x'],'y':card['y'],'width':card['width'],'height':min(card['height'],trace['y']+trace['height']-card['y']+12)})
        page.locator('.agent-audit-card').screenshot(path=str(OUT/'04_四智能体审计视图_任务输入.png'))
        for viewport in page.locator('.agent-audit-scroll [data-radix-scroll-area-viewport]').all():
            viewport.evaluate('(e)=>e.scrollTo(0,e.scrollHeight)')
        page.locator('.agent-audit-card').screenshot(path=str(OUT/'05_四智能体审计视图_处理结果.png'))
        page.set_viewport_size({'width':1100,'height':1400})
        page.locator('.agent-audit-card').screenshot(path=str(OUT/'05b_四智能体响应式审计视图.png'))
        page.set_viewport_size({'width':1440,'height':1400})
        page.locator('.timeline-card').screenshot(path=str(OUT/'06_真实网络数据包记录.png'))
        # Select a real captured handshake record so the detail panel exposes its cipher.
        handshake=page.locator('.timeline-row').filter(has_text='TLCP').filter(has_text='握手').last
        if handshake.count():handshake.click()
        page.locator('.protocol-card').screenshot(path=str(OUT/'07_国密通信协议路径.png'))
        page.locator('.detail-card').screenshot(path=str(OUT/'08_TLCP数据包详情.png'))
        page.get_by_role('button',name='金融订单与账本',exact=True).click()
        page.get_by_text('金融业务 ATP 已连接',exact=True).wait_for()
        # Display one fresh result for each of the five available rule checks.
        for label,code in [('金额不符','PAYMENT_MISMATCH'),('收款方不符','PAYMENT_MISMATCH'),
                           ('报价被篡改','INVALID_QUOTE_SIGNATURE'),('缺少授权','AUTHORIZATION_REQUIRED'),
                           ('重复付款幂等','IDEMPOTENT')]:
            before=page.locator('.finance-panel-tests').inner_text()
            page.get_by_role('button',name=label,exact=True).click()
            page.wait_for_function('(before)=>document.querySelector(".finance-panel-tests").innerText!==before',arg=before)
            page.wait_for_function('()=>!Array.from(document.querySelectorAll("button")).find(x=>x.textContent.includes("金额不符")).disabled')
            assert code in page.locator('.finance-panel-tests').inner_text()
        page.locator('dialog').screenshot(path=str(OUT/'09_金融订单报价与支付账本.png'))
        page.locator('.finance-panel-grid').screenshot(path=str(OUT/'10_签名报价授权回执与规则校验.png'))
        page.locator('.finance-panel-evidence > summary').click()
        page.locator('.finance-panel-evidence details > summary').first.click()
        page.locator('.finance-panel-evidence').screenshot(path=str(OUT/'11_金融业务请求与回执关联记录.png'))
        (OUT.parent/'原始数据'/'screenshot-finance-state.json').write_text(json.dumps(page.request.get('http://127.0.0.1:18088/api/finance/state').json(),ensure_ascii=False,indent=2),encoding='utf-8')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('dialog').screenshot(path=str(OUT/'12_金融窗口手机适配.png'))
        assert not errors,errors
        (OUT/'截图检查.json').write_text(json.dumps({'passed':True,'source':'actual local application','device_scale_factor':2,'script_errors':errors,'screenshots':len(list(OUT.glob('*.png')))},ensure_ascii=False,indent=2),encoding='utf-8')
        browser.close()
    print('Report screenshots captured')


if __name__=='__main__':main()
