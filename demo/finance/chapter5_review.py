"""Verify the generated evidence gallery and the report artefacts."""
import csv
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]/'第五章测试材料'


def main():
    figures=sorted((ROOT/'07_报告图表').glob('图*.png'))
    font=ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc',16)
    canvas=Image.new('RGB',(1600,((len(figures)+1)//2)*430),'#eef0ee');draw=ImageDraw.Draw(canvas)
    for i,path in enumerate(figures):
        with Image.open(path) as im:
            assert min(im.size)>1000 and min(im.info.get('dpi',(0,0)))>299
            thumb=im.convert('RGB');thumb.thumbnail((780,385))
        x=i%2*800+10;y=i//2*430+30
        canvas.paste(thumb,(x,y));draw.text((x,y-25),path.stem,font=font,fill='#34434a')
    canvas.save(ROOT/'09_汇总表与索引'/'图表检查总览.png')
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1100},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:18090/材料总览.html',wait_until='networkidle')
        page.locator('img').evaluate_all('(images)=>images.forEach(im=>im.loading="eager")')
        page.wait_for_function('()=>Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
        screenshot_count=len(list((ROOT/'06_应用协作与截图/报告截图').glob('*.png')))
        total=len(figures)+screenshot_count+4
        assert page.locator('figure').count()==total
        for label,count in [('学术图表',len(figures)),('真实截图',screenshot_count),('学长成果图',4),('全部图像',total)]:
            page.get_by_role('button',name=label,exact=True).click()
            assert page.locator('figure:visible').count()==count
        page.screenshot(path=str(ROOT/'09_汇总表与索引'/'材料总览预览.png'),full_page=False)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors,errors
        browser.close()
    checks={'passed':True,'gallery_images':total,'missing_images':0,'filter_checks':4,'mobile_horizontal_overflow':False,
            'script_errors':errors,'png_dpi':300,'figure_groups':len(figures),'report_screenshots':screenshot_count}
    (ROOT/'09_汇总表与索引'/'图像与总览检查.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False))


if __name__=='__main__':main()
