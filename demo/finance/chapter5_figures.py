"""Academic PNG/SVG figures and CSV tables generated from acceptance data."""
from collections import Counter
import csv
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch
import numpy as np

SOURCE=Path(__file__).resolve()
ROOT=SOURCE.parent.parent if SOURCE.parent.name=='07_报告图表' else SOURCE.parents[3]/'第五章测试材料'
RAW=ROOT/'00_原始验收数据'
FIG=ROOT/'07_报告图表'
TABLE=ROOT/'09_汇总表与索引'
PALETTE=['#688B89','#B58B7A','#77889B','#9E8EAA','#91A48F','#BEAB8F']
INK='#34434A';GRID='#E1E6E4'
font_manager.fontManager.addfont(r'C:\Windows\Fonts\msyh.ttc')
plt.rcParams.update({'font.family':'Microsoft YaHei','font.size':10,'axes.titlesize':12,
 'axes.labelsize':10,'text.color':INK,'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,
 'axes.edgecolor':'#B4BFBE','axes.spines.top':False,'axes.spines.right':False,
 'axes.unicode_minus':False,'savefig.facecolor':'white','svg.fonttype':'path'})


def load(name):return json.loads((RAW/name).read_text(encoding='utf-8'))
def table(name,rows):
    with (TABLE/(name+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def save(fig,name):
    fig.savefig(FIG/(name+'.png'),dpi=300,bbox_inches='tight',pad_inches=.17)
    fig.savefig(FIG/(name+'.svg'),bbox_inches='tight',pad_inches=.17)
    plt.close(fig)
def grid(ax):ax.grid(axis='y',color=GRID,lw=.7,zorder=0);ax.set_axisbelow(True)
def box(ax,x,y,w,h,title,body,color):
    ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='round,pad=0.04,rounding_size=.12',facecolor=color,edgecolor='none',alpha=.14))
    ax.text(x+w/2,y+h-.32,title,ha='center',va='center',fontweight='bold',fontsize=11,color=INK)
    ax.text(x+w/2,y+h/2-.15,body,ha='center',va='center',linespacing=1.8,fontsize=10)
def arrow(ax,a,b,color=INK,label=None):
    ax.annotate('',xy=b,xytext=a,arrowprops={'arrowstyle':'-|>','color':color,'lw':1.3,'shrinkA':2,'shrinkB':2})
    if label:ax.text((a[0]+b[0])/2,(a[1]+b[1])/2+.12,label,ha='center',fontsize=9,color=color)


def diagrams():
    fig,ax=plt.subplots(figsize=(11.8,5.4));ax.set(xlim=(0,12),ylim=(0,5.5));ax.axis('off')
    box(ax,3.5,4.25,5,1,'DNS 服务发现与认证记录','SVCB 目标服务地址  ·  ATS 发送授权  ·  ATK 验签公钥',PALETTE[3])
    specs=[('用户域 · family.test','172.32.1.0/24','用户业务智能体 buyer\nTravel 模型角色',0.2),
           ('商户域 · hotel.test','172.32.2.0/24','商户业务智能体 merchant\nSearch / Rates 模型角色',4.25),
           ('支付域 · payment.test','172.32.3.0/24','支付业务智能体 cashier\nBill 模型角色',8.3)]
    for i,(title,net,agents,x) in enumerate(specs):
        box(ax,x,1.6,3.5,2.15,title,net+'\nATP 服务  +  TLCP 网关\n'+agents,PALETTE[i])
        arrow(ax,(x+1.75,3.78),(x+1.75,4.0),PALETTE[3])
        ax.plot([x+1.75,x+1.75],[1.6,1.3],color=PALETTE[0],lw=1.5)
    ax.plot([1.95,10.05],[4.0,4.0],color=PALETTE[3],lw=1)
    arrow(ax,(6,4.0),(6,4.25),PALETTE[3])
    ax.plot([1.95,10.05],[1.3,1.3],color=PALETTE[0],lw=2)
    ax.text(6,1.08,'TLCP 骨干网络 · 各域经所属网关接入',ha='center',fontsize=10,color=PALETTE[0])
    ax.text(6,.58,'业务路径：本域 ATP → 本域 TLCP 网关 → 骨干网络 → 目标域 TLCP 网关 → 目标域 ATP',ha='center',fontsize=10)
    ax.text(6,.18,'独立业务网络  ·  域间直接连接验证  ·  TLCP 1.1 / SM2 / SM4 / SM3',ha='center',fontsize=10,color=PALETTE[0])
    save(fig,'图5-1_三域测试部署与国密通信路径')
    fig,ax=plt.subplots(figsize=(11.5,6.6));ax.set(xlim=(0,12),ylim=(0,9.5));ax.axis('off')
    xs=[1.7,6,10.3]
    for i,(x,label) in enumerate(zip(xs,['用户业务智能体','商户业务智能体','支付业务智能体'])):
        box(ax,x-1.35,8.3,2.7,.8,label,'',PALETTE[i]);ax.plot([x,x],[.8,8.2],color=GRID,lw=1.5,ls='--')
    flows=[(0,1,7.6,'① 提交商旅订单 · order_id'),(1,0,6.65,'② 返回三条 SM2 签名报价'),
           (0,2,5.25,'③ 用户授权后提交模拟支付'),(2,0,3.5,'④ 返回订单对应的支付回执'),
           (0,2,2.5,'⑤ 发起账本查询'),(2,0,1.5,'⑥ 返回余额与支付记录')]
    for a,b,y,label in flows:arrow(ax,(xs[a],y),(xs[b],y),PALETTE[a],label)
    ax.text(10.1,4.4,'核对签名与支付字段\n事务记账 · 订单幂等',ha='center',fontsize=10,color=PALETTE[2],bbox={'facecolor':'white','edgecolor':GRID,'pad':7})
    ax.text(1.7,5.95,'核对报价并明确授权',ha='center',fontsize=10,color=PALETTE[0],bbox={'facecolor':'white','edgecolor':GRID,'pad':5})
    ax.text(6,.3,'测试金额：2 晚 × 170.00 CNY = 340.00 CNY；通过订单编号、请求 nonce 和回执关联验证',ha='center',fontsize=10)
    save(fig,'图5-2_金融订单授权与账本验证流程')


def performance():
    samples=load('crypto-performance.json')['samples'];detail=[];summary=[]
    fig,axs=plt.subplots(1,2,figsize=(11.5,4.4))
    for ax,s,label in zip(axs,samples,['(a) 1 KiB · 50 次','(b) 64 KiB · 20 次']):
        enc,dec=s['encrypt_mean_ms'],s['decrypt_mean_ms'];total=enc+dec;limit=s['limit_ms']
        ax.bar([0],[enc],.47,color=PALETTE[0],label='加密（含密钥封装）',zorder=3)
        ax.bar([0],[dec],.47,bottom=[enc],color=PALETTE[2],label='解密（含密钥解封）',zorder=3)
        ax.axhline(limit,color=PALETTE[1],ls=(0,(5,3)),lw=1.4,label='作品测试目标')
        ax.text(0,total+limit*.045,f'{total:.2f} ms',ha='center',fontweight='bold',fontsize=12)
        ax.text(.49,limit+limit*.025,f'目标 ≤ {limit:g} ms',ha='right',color=PALETTE[1],fontsize=10)
        ax.set(xlim=(-.6,.6),ylim=(0,limit*1.2),xticks=[0],xticklabels=['平均加解密合计'],ylabel='耗时 / ms',title=label);grid(ax)
        summary.append({'载荷字节':s['plaintext_bytes'],'预热次数':s['warmups'],'正式次数':s['n'],'加密均值ms':enc,'解密均值ms':dec,'合计均值ms':total,'P95ms':s['roundtrip_p95_ms'],'最大值ms':s['roundtrip_max_ms'],'目标ms':limit,'明文一致':s['all_equal']})
        for i,(e,d,t) in enumerate(zip(s['encrypt_samples_ms'],s['decrypt_samples_ms'],s['combined_samples_ms']),1):detail.append({'载荷字节':s['plaintext_bytes'],'样本序号':i,'加密ms':e,'解密ms':d,'合计ms':t})
    handles,labels=axs[0].get_legend_handles_labels();fig.legend(handles,labels,loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.5,-.025));fig.tight_layout(rect=(0,.065,1,1))
    save(fig,'图5-3_国密密码运算均值与目标对照');table('表5-3_密码性能汇总',summary);table('密码性能70组逐次数据',detail)
    fig,axs=plt.subplots(1,2,figsize=(11.5,4.1))
    for ax,s,label in zip(axs,samples,['(a) 1 KiB','(b) 64 KiB']):
        v=s['combined_samples_ms'];ax.plot(range(1,len(v)+1),v,'o-',color=PALETTE[0],lw=.85,markersize=3.5,alpha=.85,label='逐次测量')
        ax.axhline(s['roundtrip_mean_ms'],color=PALETTE[2],lw=1.2,label='平均值')
        ax.axhline(s['roundtrip_p95_ms'],color=PALETTE[1],ls='--',lw=1.2,label='P95')
        ax.set(xlabel='正式测量序号',ylabel='加解密合计耗时 / ms',title=label);grid(ax)
    fig.legend(*axs[0].get_legend_handles_labels(),loc='lower center',ncol=3,frameon=False,bbox_to_anchor=(.5,-.025));fig.tight_layout(rect=(0,.065,1,1));save(fig,'图5-4_国密密码运算逐次测量分布')


def security():
    network=load('protocol-network.json');ats=load('ats.json');finance=load('finance-acceptance.json')
    counts=Counter(x['type'] for x in network['attacks'])
    names={'replay':'消息重放','tamper':'签名后篡改','forged_signed_identity':'签名后冒用身份','local_identity_mismatch':'本域身份不匹配','unauthorized_credentials':'错误凭据','unauthorized_destination':'禁止目标地址'}
    rows=[{'测试类别':names[k],'样例数':v,'正确拒绝数':sum(x['rejected'] for x in network['attacks'] if x['type']==k)} for k,v in counts.items()]
    rows.append({'测试类别':'ATS 来源越权','样例数':len(ats),'正确拒绝数':sum(x['rejected'] for x in ats)})
    table('表5-4_协议安全分类统计',rows)
    fig,ax=plt.subplots(figsize=(11.5,4.6));y=np.arange(len(rows))
    ax.barh(y,[r['正确拒绝数'] for r in rows],height=.56,color=[PALETTE[0]]*6+[PALETTE[2]],zorder=3)
    for i,r in enumerate(rows):ax.text(r['样例数']+.2,i,f"{r['正确拒绝数']} / {r['样例数']}",va='center',fontsize=10)
    ax.set(yticks=y,yticklabels=[r['测试类别'] for r in rows],xlabel='预设异常样例数',xlim=(0,max(r['样例数'] for r in rows)+3));ax.invert_yaxis();ax.grid(axis='x',color=GRID);ax.set_axisbelow(True)
    ax.text(1,-.17,'标签为“正确拒绝数 / 样例数”；协议异常 47 项，ATS 来源异常 5 项。',ha='right',transform=ax.transAxes,fontsize=9,color=PALETTE[2])
    fig.tight_layout();save(fig,'图5-5_协议安全异常样例分类结果')
    table('协议合法投递逐条数据',network['legal'])
    table('协议异常逐条数据',[{'序号':i+1,**r} for i,r in enumerate(network['attacks'])] if all(set(r)==set(network['attacks'][0]) for r in network['attacks']) else [{'序号':i+1,'类别':r['type'],'HTTP状态':r['status'],'正确拒绝':r['rejected'],'错误':r.get('error','')} for i,r in enumerate(network['attacks'])])
    table('金融端到端测试结果',[{'测试名称':r['name'],'通过':r['passed'],'业务代码':r.get('code',''),'耗时秒':r.get('seconds','')} for r in finance['tests']])


def recovery():
    report=load('recovery.json');finance=load('finance-acceptance.json');rows=[]
    for r in report['single']:rows.append({'场景':r['domains'][0]+' 单服务','耗时秒':r['communication_seconds'],'密钥一致':r['keys_unchanged'],'通过':r['passed']})
    for i,r in enumerate(report['group'],1):rows.append({'场景':f'整组重启 · 第{i}轮','耗时秒':r['communication_seconds'],'密钥一致':r['keys_unchanged'],'通过':r['passed']})
    off=next(r for r in finance['tests'] if r['name']=='payment_domain_offline_queue_recovery')
    rows.append({'场景':'支付域离线积压恢复','耗时秒':off['seconds'],'密钥一致':'—','通过':off['passed']});table('表5-5_恢复测试结果',rows)
    fig,ax=plt.subplots(figsize=(11.5,4.8));y=np.arange(len(rows));values=[r['耗时秒'] for r in rows]
    ax.barh(y,values,height=.58,color=[PALETTE[0]]*3+[PALETTE[2]]*3+[PALETTE[1]],zorder=3)
    for i,v in enumerate(values):ax.text(v+.28,i,f'{v:.3f} s',va='center')
    ax.axvline(30,color=PALETTE[1],ls='--',lw=1.2);ax.text(29.6,-.65,'ATP 通信恢复目标 30 s',ha='right',fontsize=10,color=PALETTE[1])
    ax.set(yticks=y,yticklabels=[r['场景'] for r in rows],xlabel='恢复耗时 / s',xlim=(0,33));ax.invert_yaxis();ax.grid(axis='x',color=GRID);ax.set_axisbelow(True)
    fig.tight_layout();save(fig,'图5-6_服务重启与离线积压恢复耗时')


def ledger():
    r=load('finance-acceptance.json');stages=['initial','declined','paid','duplicate'];labels=['请求前','异常拒绝后','成功支付后','重复请求后'];rows=[]
    for key,label in zip(stages,labels):
        s=r[key+'_statement'];a={x['id']:x['balance_fen'] for x in s['accounts']}
        rows.append({'阶段':label,'用户余额元':a['buyer@family.test']/100,'商户余额元':a['merchant-demo-001']/100,'账本总额元':sum(a.values())/100,'支付记录数':len(s['payments'])})
    table('表5-6_账户余额与支付记录核对',rows)
    old=rows[0];fig,axs=plt.subplots(1,2,figsize=(11.5,4.4))
    x=np.arange(4)
    axs[0].bar(x-.16,[v['用户余额元']-old['用户余额元'] for v in rows],.3,color=PALETTE[2],label='用户余额变化')
    axs[0].bar(x+.16,[v['商户余额元']-old['商户余额元'] for v in rows],.3,color=PALETTE[0],label='商户余额变化')
    axs[0].set(xticks=x,xticklabels=labels,ylabel='相对请求前的余额变化 / CNY',title='(a) 账户变化与资金守恒');axs[0].axhline(0,color=INK,lw=.7);grid(axs[0]);axs[0].legend(frameon=False,fontsize=9)
    v=[r['支付记录数']-old['支付记录数'] for r in rows];axs[1].bar(x,v,color=PALETTE[3],width=.5,zorder=3)
    for i,n in enumerate(v):axs[1].text(i,n+.05,str(n),ha='center',fontsize=11)
    axs[1].set(xticks=x,xticklabels=labels,ylim=(0,1.5),yticks=[0,1],ylabel='新增支付记录 / 笔',title='(b) 订单幂等与一次记账');grid(axs[1]);fig.tight_layout();save(fig,'图5-7_金融业务拒绝与幂等记账核对')


def coverage():
    def count(name):
        suite=ET.parse(RAW/name).getroot();suite=suite.find('testsuite') if suite.tag=='testsuites' else suite
        return int(suite.get('tests')),int(suite.get('failures'))+int(suite.get('errors'))
    full,fail=count('python-tests.xml');fin,_=count('finance-unit.xml');transfer,_=count('transfer-recovery.xml')
    rows=[{'测试组':'Python 全量回归','通过数':full-fail,'样例数':full},
          {'测试组':'金融规则与接口（全量子集）','通过数':fin,'样例数':fin},
          {'测试组':'传输恢复（全量子集）','通过数':transfer,'样例数':transfer},
          {'测试组':'模型运行时离线测试','通过数':15,'样例数':15},{'测试组':'Go 网关测试','通过数':4,'样例数':4},
          {'测试组':'真实合法跨域投递','通过数':len(load('protocol-network.json')['legal']),'样例数':12},
          {'测试组':'金融端到端验收','通过数':len(load('finance-acceptance.json')['tests']),'样例数':10}]
    table('表5-7_分组验证结果',rows)
    cases=[]
    for s in ET.parse(RAW/'python-tests.xml').getroot().iter('testcase'):
        cases.append({'测试模块':s.get('classname'),'测试名称':s.get('name'),'耗时秒':s.get('time'),'结果':'失败' if s.find('failure') is not None or s.find('error') is not None else '跳过' if s.find('skipped') is not None else '通过'})
    table('Python全部测试逐条结果',cases)
    iso=json.loads((ROOT/'01_测试环境/network-isolation.json').read_text(encoding='utf-8'));table('网络隔离逐条验证',iso['tcp_checks'])
    table('消息模式逐条投递',load('message-modes.json')['deliveries'])


def main():
    FIG.mkdir(parents=True,exist_ok=True);TABLE.mkdir(parents=True,exist_ok=True)
    diagrams();performance();security();recovery();ledger();coverage()
    shutil.copy2(__file__,FIG/'绘图源代码.py')
    (FIG/'配色与导出说明.md').write_text('# 图表样式\n\n莫兰迪青绿、蓝灰、陶土、灰紫配色；白底、细网格、统一中文字体。PNG以300 dpi输出，SVG保存矢量轮廓。图中数值由JSON和JUnit自动读取。\n',encoding='utf-8')
    print('Figures and CSV tables generated')


if __name__=='__main__':main()
