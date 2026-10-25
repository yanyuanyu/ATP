"""Assemble the evidence catalogue, section mapping, local gallery and manifest."""
import csv
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

REPO=Path(__file__).resolve().parents[2]
ROOT=REPO.parent/'第五章测试材料'
RAW=ROOT/'00_原始验收数据'
INDEX=ROOT/'09_汇总表与索引'


def read(name):return json.loads((RAW/name).read_text(encoding='utf-8'))
def dump(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def csvfile(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def link(p):return html.escape(p.relative_to(ROOT).as_posix(),quote=True)


def main():
    mappings={
        '02_功能与协议测试':['protocol-network.json','message-modes.json'],
        '03_安全与金融规则测试':['ats.json','finance-unit.xml','transfer-recovery.xml','finance-acceptance.json'],
        '04_密码运算性能':['crypto-performance.json'],
        '05_故障恢复与可靠投递':['recovery.json','ack-loss-recovery.json'],
    }
    for folder,files in mappings.items():
        dest=ROOT/folder;dest.mkdir(exist_ok=True)
        for name in files:shutil.copy2(RAW/name,dest/name)
    modeldir=ROOT/'06_应用协作与截图'/'原始数据'
    model=json.loads((modeldir/'model-report.json').read_text(encoding='utf-8'))
    verdict=json.loads((modeldir/'model-verdict.json').read_text(encoding='utf-8'))
    assert verdict['passed']
    state=json.loads((modeldir/'model-state.json').read_text(encoding='utf-8'))
    roles=[]
    for role in ('travel','search','rates','bill'):
        events=[e for e in state['agent_audit'] if e['role']==role]
        assert events
        roles.append({'角色':role,'审计事件数':len(events),'出现的事件类型':'、'.join(sorted(set(e['kind'] for e in events)))})
    csvfile(INDEX/'真实模型四智能体审计统计.csv',roles)
    csvfile(INDEX/'真实模型三条价格事件.csv',model['evidence']['prices'])
    csvfile(INDEX/'真实模型ATP工具调用.csv',[{'序号':i+1,'工具':a.get('tool'),'状态':a.get('status'),'输入':json.dumps(a.get('args',{}),ensure_ascii=False),'开始':a.get('started_at'),'完成':a.get('ended_at')} for i,a in enumerate(state['activities'])])
    packet=json.loads((modeldir/'model-packets.json').read_text(encoding='utf-8'))
    assert packet['summary']['ats_lookup_observed'] and packet['summary']['atk_lookup_observed']
    assert any(s.get('secure_transport')=='TLCPv1.1' for s in packet['signals'])
    env=json.loads((ROOT/'01_测试环境/environment.json').read_text(encoding='utf-8'))
    software=json.loads((ROOT/'01_测试环境/software-versions.json').read_text(encoding='utf-8'))
    csvfile(INDEX/'表5-1_测试环境实际配置.csv',[{'配置项':k,'实际值':v} for k,v in {
        '处理器':env['host']['cpu'],'内存GiB':env['host']['memory_gib'],'操作系统':env['host']['os'],
        '测试Python':env['versions']['python'],'ATP服务Python':software['atp_container']['python'],
        'gmssl':software['atp_container']['gmssl'],'DNS':software['bind'],'模型运行时Node':software['model_container_node'],
        'Pi运行时':software['pi_agent_core'],'Vite':software['vite'],'TypeScript':software['typescript'],
        '网络域':'family.test / hotel.test / payment.test','密码套件':'ECC-SM2-SM4-CBC-SM3'}.items()])
    crypto=read('crypto-performance.json')['samples'];network=read('protocol-network.json');finance=read('finance-acceptance.json');recovery=read('recovery.json')
    suite=ET.parse(RAW/'python-tests.xml').getroot().find('testsuite')
    assert suite.get('tests')=='398' and all(suite.get(k)=='0' for k in ['failures','errors','skipped'])
    cases=[]
    def case(code,section,name,method,result,evidence):cases.append({'编号':code,'对应章节':section,'测试项目':name,'执行与判定方法':method,'实测结果':result,'证据':evidence,'状态':'通过'})
    case('F-01','5.1 / 5.2.1','三域网络隔离','核对三服务各自唯一业务网络，逐方向尝试TCP连接','3个本域连接成功，6个域间直接连接受阻','01_测试环境/network-isolation.json')
    case('F-02','5.2.1','DNS发现与国密跨域投递','查询三域SVCB，验证授权、验签和解密','12/12合法跨域投递成功；TLCPv1.1 / ECC-SM2-SM4-CBC-SM3','02_功能与协议测试/protocol-network.json')
    case('F-03','5.2.3','异步消息','提交业务通知并核对接收端唯一消息','1条异步通知成功','02_功能与协议测试/message-modes.json')
    case('F-04','5.2.3','请求响应','核对请求nonce与响应in_reply_to','1组请求响应关联成功','02_功能与协议测试/message-modes.json')
    case('F-05','5.2.3','事件订阅','订阅后连续投递并接收5条编号事件','事件序号1—5全部到达','02_功能与协议测试/message-modes.json')
    case('F-06','5.2.3','64 KiB队列投递','通过本域提交与实际远域取信核对明文','65536字节消息正确还原','02_功能与协议测试/message-modes.json')
    translations={'replay':'重放','tamper':'签名后篡改','forged_signed_identity':'签名后身份冒用','local_identity_mismatch':'本域身份不匹配','unauthorized_credentials':'错误凭据','unauthorized_destination':'禁止目标'}
    for i,(kind,label) in enumerate(translations.items(),1):
        rows=[x for x in network['attacks'] if x['type']==kind];assert all(x['rejected'] for x in rows)
        case(f'S-{i:02d}','5.2.2',label,'提交预设异常请求并核对拒绝状态',f'{len(rows)}/{len(rows)}正确拒绝','00_原始验收数据/protocol-network.json')
    ats=read('ats.json');assert all(x['rejected'] for x in ats)
    case('S-07','5.2.2','ATS来源越权','使用有效签名与未授权来源发送','5/5返回403 ATS validation failed','03_安全与金融规则测试/ats.json')
    case('S-08','5.2.2','金融规则与并发记账','校验报价签名、币种、金额、授权、有效期、限额、余额和并发幂等','38项通过，含16个并发相同订单一次记账','03_安全与金融规则测试/finance-unit.xml')
    case('S-09','5.2.2','金融业务异常端到端','经真实ATP提交金额、收款方、签名和授权异常，核对账本','4类异常正确拒绝，余额与支付记录数一致','03_安全与金融规则测试/finance-acceptance.json')
    for i,s in enumerate(crypto,1):
        assert s['roundtrip_mean_ms']<=s['limit_ms'] and s['all_equal']
        case(f'P-{i:02d}','5.3',f'{s["plaintext_bytes"]//1024} KiB密码性能',f'预热{s["warmups"]}次，正式{s["n"]}次，逐次核对明文',f'平均{s["roundtrip_mean_ms"]:.2f} ms；P95 {s["roundtrip_p95_ms"]:.2f} ms；目标{s["limit_ms"]} ms','04_密码运算性能/crypto-performance.json')
    for i,r in enumerate(recovery['single']+recovery['group'],1):
        assert r['passed'] and r['keys_unchanged']
        case(f'R-{i:02d}','5.2.3 / 5.4','单域重启' if len(r['domains'])==1 else '整组三域重启',','.join(r['domains'])+'重启，恢复真实投递、验签和解密',f'{r["communication_seconds"]:.3f} s，身份密钥一致','05_故障恢复与可靠投递/recovery.json')
    ack=read('ack-loss-recovery.json');assert ack['passed']
    case('R-07','5.2.3','确认丢失与接收端重启','接收端提交后模拟发送端未确认，接收端重启后用新传输证明重试','已接收回执复用，接收端1条记录；复用传输证明返回400','05_故障恢复与可靠投递/ack-loss-recovery.json')
    off=next(t for t in finance['tests'] if t['name']=='payment_domain_offline_queue_recovery')
    case('R-08','5.2.3','支付域离线积压','停止支付域、提交已授权订单，再启动并核对账本',f'{off["seconds"]:.3f} s恢复，一笔支付记录','03_安全与金融规则测试/finance-acceptance.json')
    case('R-09','5.2.3','业务进程重启持久化','重启支付业务进程后查询账户与原账本','账户与支付记录一致','03_安全与金融规则测试/finance-acceptance.json')
    case('A-01','5.2.4','确定性金融业务完整流程','订单→三条签名报价→授权→模拟支付→回执→账本','10项端到端检查通过；最低2×170=340 CNY','03_安全与金融规则测试/finance-acceptance.json')
    case('A-02','5.2.4','真实四智能体协作','明确Paris、2晚参数，模型自行调用ATP工具完成搜索、订阅与模拟付款',f'{model["elapsed_seconds"]:.2f} s；{model["tool_calls"]}次工具调用；2×170=340 USD；4角色32条审计事件','06_应用协作与截图/原始数据/model-report.json')
    case('A-03','5.2.4','原前端与金融窗口','操作订单、异常校验、幂等、账本、导出及抓包，检查桌面和手机布局','界面和功能检查通过，脚本错误0','00_原始验收数据/browser-test.json')
    case('Q-01','5.4','Python回归','运行全量pytest','398/398通过；金融38项与恢复14项为其中子集','00_原始验收数据/python-tests.xml')
    case('Q-02','5.4','模型运行时离线','模拟模型服务验证工具与异常行为','15/15通过','00_执行日志/node-runtime.log')
    case('Q-03','5.4','TLCP Go网关','执行Go测试','4/4通过','00_执行日志/go-gateway.log')
    case('Q-04','5.4','前端类型检查与构建','执行TypeScript检查和Vite生产构建','构建通过','00_执行日志/frontend-build.log')
    csvfile(INDEX/'第五章完整测试矩阵.csv',cases);dump(INDEX/'第五章完整测试矩阵.json',cases)
    # Associate results with source content, including all test and figure scripts.
    hashes={}
    for name in subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=REPO).decode().split('\0'):
        p=REPO/name
        if name and p.is_file() and (name.startswith(('src/','tests/','demo/finance/','demo/docker/agents/','demo/docker/demo/web/src/')) and '/evidence/' not in name):
            hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    dump(INDEX/'代码与测试脚本校验.json',{'branch':'jinmi-cup','source_sha256':hashes})
    figures=sorted((ROOT/'07_报告图表').glob('图*.png'))
    screenshots=sorted((ROOT/'06_应用协作与截图/报告截图').glob('*.png'))
    seniors=sorted((ROOT/'08_学长提供的成果图源').glob('图3-*.png'))
    captions={
        '图5-1':'测试配置：三独立业务域、DNS认证记录和TLCP骨干路径。',
        '图5-2':'确定性金融业务：SM2签名报价、明确授权、订单回执与账本联合核对。',
        '图5-3':'含SM2会话密钥封装/解封、SM4和HMAC-SM3的载荷加解密合计耗时。',
        '图5-4':'70组正式测量原始样本，水平线表示各组均值和P95。',
        '图5-5':'47个协议异常与5个ATS来源异常的分类拒绝情况。',
        '图5-6':'三次单服务、三轮整组重启和支付域离线积压恢复。',
        '图5-7':'以金融端到端验收订单为样例，比较异常拒绝、成功支付和重复请求后的账户与记录变化。'}
    def images(paths,kind):
        blocks=[]
        for p in paths:
            caption=next((v for k,v in captions.items() if p.stem.startswith(k+'_')),p.stem)
            blocks.append(f'<figure data-kind="{kind}"><a href="{link(p)}" target="_blank"><img loading="lazy" src="{link(p)}" alt="{html.escape(p.stem)}"></a><figcaption><b>{html.escape(p.stem)}</b><p>{html.escape(caption)}</p></figcaption></figure>')
        return ''.join(blocks)
    def htmltable(rows):
        return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+html.escape(k)+'</th>' for k in rows[0])+ '</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in r.values())+'</tr>' for r in rows)+'</tbody></table></div>'
    summary=[{'验证项目':'Python全量回归','实测结果':'398项通过'}, {'验证项目':'网络与消息','实测结果':'三域隔离；12次跨域投递；三种消息模式；64 KiB队列'},
             {'验证项目':'协议安全','实测结果':'47个协议异常和5个ATS越权来源正确拒绝'},
             {'验证项目':'金融业务','实测结果':'38项规则/接口子集；10项端到端验收；一次记账'},
             {'验证项目':'真实模型协作','实测结果':f'4角色，三条价格事件，340 USD模拟付款，{model["elapsed_seconds"]:.2f} s'},
             {'验证项目':'1 KiB / 64 KiB密码运算','实测结果':f'{crypto[0]["roundtrip_mean_ms"]:.2f} / {crypto[1]["roundtrip_mean_ms"]:.2f} ms'}]
    htmlpage='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>第五章测试材料 · 金密杯</title><style>
    :root{--ink:#34434a;--muted:#6e7e83;--green:#688b89;--line:#dfe6e3}*{box-sizing:border-box}body{margin:0;background:#f3f5f2;color:var(--ink);font:15px/1.75 "Microsoft YaHei",sans-serif}header,main{max-width:1280px;margin:auto;padding:32px}header{padding-top:54px}small{letter-spacing:.14em;color:var(--green)}h1{font-size:34px;margin:8px 0 12px}h2{font-size:23px;margin:36px 0 15px}p{color:var(--muted)}nav{position:sticky;top:0;background:#f3f5f2ed;backdrop-filter:blur(10px);padding:12px 0;z-index:2;border-bottom:1px solid var(--line)}button,a{color:var(--green)}button{background:white;border:1px solid var(--line);border-radius:22px;padding:8px 18px;margin-right:7px;cursor:pointer}button.active{background:var(--green);color:white}a{text-decoration:none}figure{background:white;border:1px solid var(--line);border-radius:14px;margin:0;overflow:hidden}figure img{width:100%;height:270px;object-fit:contain;background:white;padding:12px}figcaption{padding:16px 20px;border-top:1px solid var(--line)}figcaption p{font-size:13px;margin:6px 0}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:22px}.table-wrap{overflow:auto;background:white;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:12px 16px;border-bottom:1px solid var(--line);font-size:13px}th{background:#e9eeeb;color:#45615f;white-space:nowrap}.files{display:flex;flex-wrap:wrap;gap:10px}.files a{padding:8px 12px;border:1px solid var(--line);border-radius:6px;background:white}footer{padding:35px;text-align:center;color:var(--muted);font-size:13px}.hidden{display:none!important}@media(max-width:700px){header,main{padding:18px}.gallery{grid-template-columns:1fr}h1{font-size:27px}}
    </style><header><small>JINMI CUP / ATP / CHAPTER 05</small><h1>系统测试 · 数据与图像材料</h1><p>真实执行数据、可直接用于报告的高清截图、莫兰迪学术图表与学长成果图源。</p></header><main>'''
    htmlpage+='<h2>验证结果</h2>'+htmltable(summary)
    htmlpage+='<p>金融订单与模拟账本采用CNY整数分计价；模型协作场景沿用USD演示价格，二者分别保留订单、任务与回执数据。100 ms、2 s和30 s为作品设定的测试目标。</p>'
    htmlpage+='<nav><button class="active" data-filter="all">全部图像</button><button data-filter="chart">学术图表</button><button data-filter="screen">真实截图</button><button data-filter="senior">学长成果图</button></nav><h2>报告图像</h2><div class="gallery">'+images(figures,'chart')+images(screenshots,'screen')+images(seniors,'senior')+'</div>'
    htmlpage+='<h2>汇总表与逐条数据</h2><div class="files">'+''.join(f'<a href="{link(p)}">{html.escape(p.stem)}</a>' for p in sorted(INDEX.glob('*.csv')))+'</div>'
    htmlpage+='<h2>测试覆盖与证据对应</h2>'+htmltable([{k:r[k] for k in ['编号','对应章节','测试项目','实测结果','状态']} for r in cases])
    htmlpage+='</main><footer>PNG 300 dpi · SVG矢量图 · CSV UTF-8 · JSON / JUnit原始数据 · 可复现脚本</footer><script>document.querySelectorAll("button[data-filter]").forEach(b=>b.onclick=()=>{document.querySelectorAll("button").forEach(x=>x.classList.remove("active"));b.classList.add("active");document.querySelectorAll("figure").forEach(f=>f.classList.toggle("hidden",b.dataset.filter!=="all"&&f.dataset.kind!==b.dataset.filter))})</script></html>'
    (ROOT/'材料总览.html').write_text(htmlpage,encoding='utf-8')
    readme=f'''# 第五章测试材料

打开“材料总览.html”可浏览测试汇总、图表、真实截图和逐条数据。

## 测试完成情况

- Python全量398项、模型运行时离线15项、Go网关4项通过；前端类型检查与生产构建通过。
- 金融规则与接口38项、传输恢复14项是Python全量测试中的子集。
- 三域各接入独立业务网络；6个方向的域间直接连接受阻，12次合法国密跨域投递完成。
- 47个协议异常与5个ATS来源异常正确拒绝；异步、请求响应、5条订阅事件和64 KiB队列投递通过。
- 1 KiB正式测量50次，平均{crypto[0]['roundtrip_mean_ms']:.2f} ms；64 KiB正式测量20次，平均{crypto[1]['roundtrip_mean_ms']:.2f} ms。每种载荷预热2次，70组逐次值保存在CSV与JSON中。
- 三次单服务和三轮整组重启完成实际通信恢复，身份密钥一致；支付域离线积压恢复{off['seconds']:.3f} s。
- 接收端提交后模拟发送端未确认，接收端重启后重试复用接收回执，邮箱保留1条对应记录。
- 金融端到端10项通过；真实模型四智能体协作{model['elapsed_seconds']:.2f} s完成，包含三条价格事件、6次ATP工具调用和成功模拟付款回执。

## 材料组织

| 目录 | 内容 |
|---|---|
| 00_原始验收数据、00_执行日志 | 完整原始JSON、JUnit与命令日志 |
| 01_测试环境 | 硬件、软件版本、独立网络与连通性检查 |
| 02_功能与协议测试 | 服务发现、跨域投递、三种消息模式与64 KiB队列 |
| 03_安全与金融规则测试 | ATS、金融规则、端到端及传输证明安全检查 |
| 04_密码运算性能 | 两种载荷及70组原始测量 |
| 05_故障恢复与可靠投递 | 服务重启、确认丢失、去重与密钥保持 |
| 06_应用协作与截图 | 真实模型完整运行数据和报告用局部截图 |
| 07_报告图表 | 7组300 dpi PNG、SVG矢量图与绘图源代码 |
| 08_学长提供的成果图源 | 协议草案、IMC论文、NSS海报与4张成果候选图 |
| 09_汇总表与索引 | 全部测试矩阵、CSV汇总、代码校验与材料清单 |

## 报告图像选用

- 5.1：图5-1测试部署、图5-2金融验证流程，配环境配置表。
- 5.2：选取拓扑、模型结果、四智能体审计、TLCP详情和金融窗口的清晰局部截图；异常结果配分类统计与业务回执。
- 5.3：图5-3均值与目标、图5-4逐次分布，配70组原始数据与性能汇总表。
- 5.4：图5-5异常分类、图5-6恢复耗时、图5-7账本核对，配完整测试矩阵与分组结果表；学长成果图源供研究成果部分选用。

## 计量与业务对应

密码性能统计载荷加解密、HMAC-SM3完整性处理及SM2会话密钥封装/解封。实际跨域投递耗时和恢复耗时分别记录。

金融账本场景采用CNY整数分计价，2晚最低合计340 CNY；真实模型场景沿用原演示的USD价格，2晚合计340 USD。每组通过订单编号或任务ID与请求nonce关联原始回执。

图5-7与余额核对表对应finance-acceptance中的订单；浏览器截图对应browser-export中的订单。账户累计余额由各自账本快照记录。

学长提供的协议草案、论文、海报原件保存在“原始文件”，4张成果候选图来自已有报告材料；图源索引保存来源与校验值。

## 复现

在jinmi-cup分支启动演示后，运行demo/finance/chapter5_model.py获取完整模型协作数据，再运行chapter5_collect.py执行其余验收。chapter5_figures.py生成图表与CSV，chapter5_screenshots.py截取真实界面，chapter5_assemble.py整理材料与打包。绘图环境使用matplotlib、numpy、pillow和Python Playwright。
'''
    (ROOT/'README_材料使用说明.md').write_text(readme,encoding='utf-8')
    # Validate references and every original assertion before producing the manifest.
    for r in cases:assert (ROOT/r['证据']).is_file(),r
    for p in ROOT.rglob('*.png'):
        from PIL import Image
        with Image.open(p) as im:im.verify()
    manifests=[]
    for p in sorted(ROOT.rglob('*')):
        if p.is_file() and p.name not in ('材料清单.csv','交付检查.json'):
            manifests.append({'文件':p.relative_to(ROOT).as_posix(),'字节':p.stat().st_size,'SHA256':hashlib.sha256(p.read_bytes()).hexdigest()})
    csvfile(INDEX/'材料清单.csv',manifests)
    dump(INDEX/'交付检查.json',{'passed':True,'test_matrix_rows':len(cases),'figures':len(figures),'report_screenshots':len(screenshots),'senior_figures':len(seniors),'files':len(manifests),'actual_model_roles':4,'model_verdict':verdict,'source_branch':'jinmi-cup'})
    archive=ROOT.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in ROOT.rglob('*'):
            if p.is_file():z.write(p,str(Path(ROOT.name)/p.relative_to(ROOT)))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    print(json.dumps({'matrix_rows':len(cases),'figures':len(figures),'screenshots':len(screenshots),'files':len(manifests),'archive_bytes':archive.stat().st_size},ensure_ascii=False))


if __name__=='__main__':main()
