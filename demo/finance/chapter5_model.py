"""Capture a complete real-model collaboration for the chapter-five evidence set."""
import json
from pathlib import Path
import time
import httpx

OUT = Path(__file__).resolve().parents[3] / "第五章测试材料" / "06_应用协作与截图" / "原始数据"
BASE = "http://127.0.0.1:18088"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
        c.post('/api/chat/reset').raise_for_status()
        prompt = '执行一次完整的模拟商旅预订。任务参数：city=Paris，nights=2。首先通过atp_send向search@hotel.test发送search，body写为Paris 2 nights；核对搜索结果nights=2。然后向rates@hotel.test订阅ParisGarden，收齐三条价格事件180、170、190 USD。最后按最低170 USD每晚、2晚、总计340 USD，向bill@payment.test发起book+pay并等待成功回执。我授权本次模拟付款。请用中文说明结果。'
        r = c.post('/api/chat/messages', json={'message': prompt})
        r.raise_for_status()
        run = r.json()['run_id']
        print('Real-model run:', run, flush=True)
        start = time.monotonic()
        while time.monotonic() - start < 240:
            state = c.get('/api/state/chat').json()['chat']
            if not state.get('active_request_id'):
                break
            time.sleep(2)
        report_response = c.get(f'/api/runs/{run}/report')
        report = report_response.json()
        for name, data in [('model-state.json',state),('model-report.json',report),
                           ('model-trace.json',c.get(f'/api/runs/{run}/trace').json()),
                           ('model-packets.json',c.get(f'/api/runs/{run}/packets').json())]:
            (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        evidence = report.get('evidence') or {}
        payment = evidence.get('payment') or {}
        passed = (report.get('status') == 'idle' and not report.get('error')
                  and evidence.get('price_count') == 3 and evidence.get('payment_approved') is True
                  and payment.get('nights') == 2 and payment.get('nightly_amount') == 170
                  and payment.get('total') == 340 and payment.get('currency') == 'USD')
        (OUT/'model-verdict.json').write_text(json.dumps({'passed':passed,'expected':{'nights':2,'nightly_amount':170,'total':340,'currency':'USD'},'actual':payment},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':passed,'status':report.get('status'),'error':report.get('error'),
                          'elapsed_seconds':report.get('elapsed_seconds'),'evidence':evidence},ensure_ascii=False),flush=True)
        assert passed, 'Full model collaboration needs a successful correlated payment receipt'


if __name__ == '__main__':
    main()
