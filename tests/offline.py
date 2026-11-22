"""Verify against synthetic documents, HP Windows XML and a loopback-only scanner."""
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
import argparse,collections,io,json,re,socket,sys,zipfile
import pymupdf as pdf
from PIL import Image,ImageOps
from hp_profile import PAPERS,MEDIA,PT
from reference_sample import sample
from settings import DEFAULTS
from pages import prepare,plan_job,id_page
from printing import encode,encode_job,dispatch,Cancelled,send_spl
from scan import Scanner,export_scans,NS,copy_background
from streams import read,image
ROOT=Path(__file__).resolve().parents[1]
def labels(doc):
    return [[int(n) for n in re.findall(r'PAGE (\d+)',p.get_text())] for p in doc]
def listing(jobs):return [labels(x) for x in jobs]
def has_ink(page):return bool(page.get_contents())
def run_all(folder,reference):
    passed=[]
    def ok(name):passed.append(name);print('PASS',name,flush=True)
    src=folder/'four.pdf';sample(src);s=DEFAULTS|{'text':'off'}
    windows=ET.parse(reference).getroot() if reference else None
    if windows is not None:
        def cap(k):return windows.find(f".//Capability[@ID='{k}']")
        assert {(int(x.get('PUBLIC')),int(x.get('PRIVATE'))) for x in cap('Quality').findall('Items/Item')}=={(600,600),(1200,1200)}
        assert [cap(k).get('VALUE') for k in ('MDEvenPageReverseOrder','MDOddPageFirst','MDOddPageReverseOrder')]==['TRUE','FALSE','FALSE']
        assert set(MEDIA)=={x.get('PJLCOMMAND') for x in cap('PaperType').findall('Items/Item')}
        for key,d in PAPERS.items():
            orig=next(a for a in cap('PaperSize').findall('Items/Item') if a.get('VALUE')==str(d[6]))
            dims=(int(orig.get('WIDTH')),int(orig.get('HEIGHT')))
            assert dims==(d[4:6][::-1] if key=='A5LEF' else d[4:6])
        ok('原厂 XML 的两档精度、11 种纸材、纸型尺寸及手动双面页序')
    for length in (3,4,28,51):
        f=folder/f'pages-{length}.pdf';sample(f,length)
        for edge in ('short','long'):
            for face in ('up','down'):
                for reload in ('up','down'):
                    for head in ('inside','outside'):
                        opts=s|dict(binding=edge,out=face,reload=reload,head=head)
                        jobs=prepare(f,opts); first,second=listing(jobs)
                        seq=list(range(1,length+1,2));expected=seq if face=='down' else seq[::-1]
                        assert second==[[i] for i in expected]
                        backs=first[::-1] if face==reload else first
                        assert backs==[([i+1] if i<length else []) for i in expected]
                        for part, job in enumerate(jobs):
                            pg=next(p for p in job if 'PAGE ' in p.get_text())
                            vec=next(line['dir'] for b in pg.get_text('dict')['blocks'] if 'lines' in b for line in b['lines'] if any('PAGE ' in span['text'] for span in line['spans']))
                            assert (vec[0]<0)==(part==0 and ((edge=='short')!=(head=='outside')))
        ok(f'{length} 页，长短边、两种出纸面、放回面和页首，共 16 组；奇数页补白')
    for dpi in [600,1200]:
        for length in [3,4]:
            source=folder/f'pages-{length}.pdf'
            for edge in ['short','long']:
                opts=s|dict(binding=edge,dpi=str(dpi))
                plan=plan_job(source,opts);data=encode_job(plan,opts,folder).read_bytes();pjl,p=read(data)
                assert data.count(b'@PJL ENTER LANGUAGE')==1
                assert all(v['dpi']==(dpi,dpi) and v['geometry']==(2480,3507) for v in p)
                assert pjl['XIGNOREFF']=='OFF' and pjl['DUPLEX']=='MANUAL'
                assert pjl['PAGESPERCOPIES']==str(length) and pjl['BINDING']==edge.upper()+'EDGE'
                assert [v['header'][9] for v in p]==[1,1,4,4]
                assert [v['header'][11] for v in p]==[int(edge=='long')]*4
                assert [v['header'][12] for v in p]==[1,1,0,0]
                assert [bool(v['bands']) for v in p]==[length==4,True,True,True]
        ok(f'{dpi} dpi，长短边各 3、4 页；单个机身手动双面作业的纸盘、翻面、补白和条带校验和')
    opts=s|dict(binding='long',pages='1-3',copies='2',collate='yes')
    expanded=plan_job(src,opts); ticket,sheets=read(encode_job(expanded,opts,folder).read_bytes())
    assert expanded.counts==[4,4] and len(sheets)==8 and ticket['PAGESPERCOPIES']=='8'
    assert [bool(p['bands']) for p in sheets[:4]]==[False,True,False,True]
    ok('奇数页多份可逐份排列，每份独立补白')
    from compare_duplex import identify
    options=s|dict(binding='long',paper='Letter',fit='paper')
    long_plan=plan_job(folder/'pages-51.pdf',options)
    ticket,wire_pages=read(encode_job(long_plan,options,folder).read_bytes())
    assert ticket['PAGESPERCOPIES']=='51' and len(wire_pages)==52 and long_plan.counts==[26,26]
    evens,odds = wire_pages[:26],wire_pages[26:]
    assert not evens[0]['bands']
    for i,(even,odd) in enumerate(zip(reversed(evens),odds),1):
        assert identify(odd,6)=={'page':2*i-1,'rotation':0}
        if i<26:assert identify(even,6)=={'page':2*i,'rotation':180}
    ok('51 页实编码共 52 个面；每趟 26 张，逐张还原页码，含 23/24、25/26 和 51/空白')
    for name, data in PAPERS.items():
        sel=s|dict(paper=name,media='ENV' if name in ('DL','C5','Env10','Monarch') else ('CARD' if name=='Postcard' else 'OFF'),pages='1')
        job,=prepare(src,sel);pjl,pages=read(encode(job,sel,folder,1).read_bytes())
        assert (pages[0]['code'],*pages[0]['geometry'])==data[3:6] and pjl['PAPERTYPE']==sel['media']
    sel=s|dict(paper='Custom',width='105',height='190',pages='1');job,=prepare(src,sel)
    assert read(encode(job,sel,folder,0).read_bytes())[1][0]['geometry']==(1240,2244)
    ok('15 种固定纸型、自定义 105 × 190 mm、实际纸型页头')
    for media in MEDIA:
        sel=s|dict(media=media,paper='DL' if media=='ENV' else 'A4',pages='1',econ='yes',edge='MAXIMUM',altitude='HIGH1')
        job,=prepare(src,sel);pjl,page=read(encode(job,sel,folder,1).read_bytes())
        assert pjl['PAPERTYPE']==media and pjl['ECONOMODE']=='ON' and pjl['EDGEENHANCE']=='MAXIMUM'
    ok('11 种介质、省墨、边缘增强和海拔进入实际 PJL')
    assert listing(prepare(src,s|dict(copies='2',collate='no',pages='1,3')))==[[[1],[1],[3],[3]]]
    assert listing(prepare(src,s|dict(reverse='yes',subset='even')))==[[[4],[2]]]
    assert listing(prepare(src,s|dict(layout='booklet')))==[[[2,3]],[[4,1]]]
    for n in (2,4,6,9,16):
        for border in ('none','solid','dot','dash','chain','chaindouble','3d','trans','double','crop','corner'):
            doc,=prepare(src,s|dict(layout='nup',nup=str(n),border=border))
            assert len(doc)==(4+n-1)//n and sorted(sum(labels(doc),[]))==[1,2,3,4]
    for n in (2,3,4):
        job,=prepare(src,s|dict(layout='poster'+str(n),pages='1',overlap='6'))
        assert len(job)==n*n
    ok('份数与逐份、奇偶和倒序、折页小册子、5 种多页拼版、11 种页框、3 种海报')
    sim=pdf.open();sim.new_page();p=sim.new_page();p.insert_text((100,110),'visible',color=(.7,.3,.2));f=folder/'blank.pdf';sim.save(f)
    assert len(prepare(f,s|dict(skip='yes'))[0])==1
    assert len(prepare(f,s|dict(skip='yes',binding='short')))==2
    job,=prepare(f,s|dict(black='yes',text='max'));assert all(span['color']==0 for b in job[1].get_text('dict')['blocks'] if 'lines' in b for l in b['lines'] for span in l['spans'])
    for adj in [('brightness','12'),('contrast','12')]:
        assert len(prepare(f,s|dict([adj]))[0][1].get_images())>0
    over=pdf.open();over.new_page(width=210*PT,height=297*PT).insert_text((45,370),'LETTERHEAD');over.save(folder/'overlay.pdf')
    d,=prepare(src,s|dict(overlay=str(folder/'overlay.pdf'),wm='Draft',wmfont='helv',wmfirst='yes',wmstyle='bolditalic'))
    assert all('LETTERHEAD' in p.get_text() for p in d) and 'Draft' in d[0].get_text() and 'Draft' not in d[1].get_text()
    ok('单面跳白、双面保留空白、文字全黑、亮度、对比度、首页水印、叠加信头')
    scans=[]
    class Fake(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,n,data=b'',typ='text/xml',location=None):
            self.send_response(n);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(data)))
            if location:self.send_header('Location',location)
            self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if self.path.endswith('ScannerCapabilities'):self.reply(200,(ROOT/'reference/scan-capabilities.xml').read_bytes());return
            assert self.path.endswith('/NextDocument')
            p=pdf.open();x=p.new_page(width=85.60*PT,height=53.98*PT);x.insert_text((20,30),'FRONT' if len(scans)==1 else 'BACK',fontsize=14)
            self.reply(200,p.tobytes(),'application/pdf')
        def do_POST(self):
            data=self.rfile.read(int(self.headers['Content-Length']));root=ET.fromstring(data)
            assert root.findtext('s:XResolution',namespaces=NS)=='300' and root.findtext('s:ColorMode',namespaces=NS)=='Grayscale8'
            assert int(root.findtext('.//p:Width',namespaces=NS))==1011 and int(root.findtext('.//p:Height',namespaces=NS))==638
            scans.append(data);self.reply(201,location='/eSCL/ScanJobs/job'+str(len(scans)))
        def do_DELETE(self):self.reply(200)
    with ThreadingHTTPServer(('127.0.0.1',0),Fake) as mock:
        t=Thread(target=mock.serve_forever,daemon=True);t.start();sc=Scanner(f'http://127.0.0.1:{mock.server_port}/eSCL')
        front=sc.scan(300,'Grayscale8','Document',85.60,53.98);back=sc.scan(300,'Grayscale8','Document',85.60,53.98)
        try:sc.scan(600,'RGB24','Document',210,297)
        except ValueError:pass
        else:raise AssertionError('Unsupported scan DPI accepted')
        mock.shutdown()
    iddoc=id_page(front,back);p=iddoc[0];f,b=(p.search_for(k)[0] for k in ('FRONT','BACK'))
    assert f.y1<p.rect.height/2<b.y0 and abs(b.y0-f.y0-p.rect.height/2)<.02
    for mode in ('auto','enhance1','enhance2','erase1','erase2','erase3','erase4'):
        processed=copy_background(iddoc,mode,100)
        assert len(processed[0].get_images())==1
    for fmt in ('png','jpeg','tiff','pdf','txt','epub','ocrpdf'):
        target=folder/f'copy.{fmt}';export_scans(iddoc,target,fmt,200,folder);assert target.stat().st_size>0
        if fmt=='epub':
            with zipfile.ZipFile(target) as z:assert z.testzip() is None and 'FRONT' in z.read('page.xhtml').decode().upper()
    ok('本机模拟扫描仪的建任务、取图、释放、拒绝 600 dpi、身份证原尺寸正反合印、7 种导出含 OCR')
    opts=s|dict(binding='long');plan=plan_job(src,opts);wire=encode_job(plan,opts,folder)
    captured=[];stages=[]
    def popup(title,detail):
        stages.append(title)
        return nullcontext()
    def should_not_confirm(*args):
        raise AssertionError('firmware reload should not require a computer confirmation')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));sock.listen()
        def receive():
            c,_=sock.accept()
            with c, c.makefile('rb') as stream:captured.append(stream.read())
        thread=Thread(target=receive);thread.start();port=sock.getsockname()[1]
        readings=iter([100,100,101,102,102,103,104])
        from time import sleep
        dispatch(wire,plan.counts,opts,lambda f:send_spl(f,'127.0.0.1',port),lambda t,d:stages.append(t),
                 should_not_confirm,lambda:next(readings,104),lambda:'none',popup,sleep=lambda _:sleep(.005))
        thread.join(5)
    assert captured==[wire.read_bytes()]
    assert stages==['正在打印第一趟','等待在打印机上换纸','正在打印第二趟','打印完成']
    sent=[]
    try:
        dispatch(wire,plan.counts,opts,sent.append,should_not_confirm,lambda *_:False,
                 lambda:None,lambda:'none',popup,sleep=lambda _:sleep(.005))
    except Cancelled:
        pass
    else:
        raise AssertionError('a confirmed device cancellation was marked complete')
    assert sent==[wire]
    ok('环回模拟机身接收一个完整作业；等待机身键、第二趟、完成和机身取消均不重发')
    return passed
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--windows-xml',type=Path);parser.add_argument('--report',type=Path);args=parser.parse_args()
    with TemporaryDirectory() as temp:result=run_all(Path(temp),args.windows_xml)
    if args.report:args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
