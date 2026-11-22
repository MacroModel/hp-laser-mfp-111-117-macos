"""Flatbed scan over eSCL. Each scan explicitly requests one original."""
from contextlib import suppress
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
import io, os, re, time, zipfile
from xml.sax.saxutils import escape
import pymupdf as pdf
from PIL import Image, ImageOps
from hp_profile import PT
from settings import BUILD
from subprocess import run
import json

PWG='http://www.pwg.org/schemas/2010/12/sm'
ESCL='http://schemas.hp.com/imaging/escl/2011/05/03'
ET.register_namespace('pwg',PWG); ET.register_namespace('scan',ESCL)
NS={'p':PWG,'s':ESCL}
def parse_caps(data):
    root=ET.fromstring(data); platen=root.find('.//s:PlatenInputCaps',NS)
    if platen is None: raise ValueError('扫描仪未报告平板。')
    def values(node,key): return [item.text for item in node.findall('.//s:'+key,NS)]
    profiles=[]
    for p in platen.findall('.//s:SettingProfile',NS):
        profiles.append((set(values(p,'ColorMode')),set(values(p,'DocumentFormatExt')+[x.text for x in p.findall('.//p:DocumentFormat',NS)]),
                         {int(point.findtext('s:XResolution',namespaces=NS)) for point in p.findall('.//s:DiscreteResolution',NS)
                          if point.findtext('s:XResolution',namespaces=NS)==point.findtext('s:YResolution',namespaces=NS)}))
    return dict(model=root.findtext('.//p:MakeAndModel',namespaces=NS),profiles=profiles,
                intents=set(values(platen,'Intent')),**{k:int(values(platen,k)[0]) for k in ('MinWidth','MinHeight','MaxWidth','MaxHeight')})

class Scanner:
    def __init__(self,url):
        self.url=urlsplit(url); self.base=self.url.path.rstrip('/')
        if self.url.scheme not in ('http','https') or not self.url.hostname: raise ValueError('扫描仪地址无效。')

    def request(self,method,path,body=None,timeout=45):
        c=(HTTPSConnection if self.url.scheme=='https' else HTTPConnection)(self.url.hostname,self.url.port,timeout=timeout)
        try:
            c.request(method,path,body,{'Content-Type':'text/xml; charset=utf-8'} if body else {})
            r=c.getresponse()
            # The 115nw platen at its largest advertised RGB resolution fits well under this limit.
            data=r.read(100*1024*1024+1)
            if len(data)>100*1024*1024: raise ValueError('扫描结果超过本机缓冲区。')
            return r.status,r.getheader('Location'),data
        finally: c.close()

    def capabilities(self):
        status,_,body=self.request('GET',self.base+'/ScannerCapabilities')
        if status!=200: raise ValueError(f'读取扫描能力失败（HTTP {status}）。')
        return parse_caps(body)

    def scan(self,dpi,color,intent,w,h,x=0,y=0,fmt='application/pdf'):
        caps=self.capabilities()
        if not any(color in colors and fmt in formats and dpi in resolutions for colors,formats,resolutions in caps['profiles']):
            raise ValueError('扫描仪不接受所选颜色、格式和分辨率组合。')
        if intent not in caps['intents']: raise ValueError('扫描仪不支持此原件类型。')
        x,y,w,h=[round(mm*300/25.4) for mm in (x,y,w,h)]
        # A4 at 300/in rounds to 3508; this platen advertises 3507.
        if abs(x+w-caps['MaxWidth'])<=1:w=caps['MaxWidth']-x
        if abs(y+h-caps['MaxHeight'])<=1:h=caps['MaxHeight']-y
        if x<0 or y<0 or w<caps['MinWidth'] or h<caps['MinHeight'] or x+w>caps['MaxWidth'] or y+h>caps['MaxHeight']: raise ValueError('所选区域超出了 115nw 平板的扫描范围。')
        root=ET.Element(f'{{{ESCL}}}ScanSettings',{'xmlns:escl':ESCL})
        def node(parent,ns,name,value=None):
            element=ET.SubElement(parent,f'{{{ns}}}{name}')
            if value is not None: element.text=str(value)
            return element
        node(root,PWG,'Version','2.5');node(root,ESCL,'Intent',intent)
        region=node(node(root,PWG,'ScanRegions'),PWG,'ScanRegion')
        for key,val in [('ContentRegionUnits','escl:ThreeHundredthsOfInches'),('Height',h),('Width',w),('XOffset',x),('YOffset',y)]:
            node(region,PWG,key,val)
        node(root,PWG,'InputSource','Platen');node(root,ESCL,'InputSource','Platen')
        node(root,PWG,'DocumentFormat',fmt);node(root,ESCL,'DocumentFormatExt',fmt)
        node(root,ESCL,'ColorMode',color);node(root,ESCL,'XResolution',dpi);node(root,ESCL,'YResolution',dpi)
        status,location,_=self.request('POST',self.base+'/ScanJobs',ET.tostring(root,encoding='utf-8',xml_declaration=True))
        if status!=201 or not location: raise ValueError(f'扫描仪未接收任务（HTTP {status}）。')
        path=urlsplit(location).path
        if not path.startswith('/'): path=self.base+'/ScanJobs/'+path.split('/')[-1]
        if not re.fullmatch(re.escape(self.base)+r'/ScanJobs/[A-Za-z0-9_.:-]+',path) or path.rsplit('/',1)[1] in ('.','..'): raise ValueError('扫描仪返回了无效的作业地址。')
        try:
            deadline=time.monotonic()+90
            while True:
                code,_,data=self.request('GET',path+'/NextDocument',timeout=90)
                if code==200: break
                if code not in (503,409) or time.monotonic()>=deadline: raise ValueError(f'读取扫描结果失败（HTTP {code}）。')
                time.sleep(1)
            if fmt=='application/pdf': out=pdf.open(stream=data,filetype='pdf')
            else:
                with Image.open(io.BytesIO(data)) as img: img.verify()
                out=pdf.open();p=out.new_page(width=w*72/300,height=h*72/300);p.insert_image(p.rect,stream=data)
            if len(out)!=1: raise ValueError('平板没有返回一页有效原件。')
            return out
        finally:
            with suppress(OSError): self.request('DELETE',path,timeout=4)

def copy_background(doc,mode,dpi):
    if mode=='off': return doc
    out=pdf.open()
    for page in doc:
        pix=page.get_pixmap(dpi=dpi,colorspace=pdf.csGRAY,alpha=False)
        img=Image.frombytes('L',(pix.width,pix.height),pix.samples)
        if mode=='auto':
            img=ImageOps.autocontrast(img,cutoff=(0,2))
        elif mode.startswith('erase'):
            white={1:248,2:235,3:218,4:196}[int(mode[-1])]
            img=img.point([min(255,round(255*v/white)) for v in range(256)])
        elif mode.startswith('enhance'):
            gamma={'enhance1':1.18,'enhance2':1.38}[mode]
            img=img.point([round(255*(v/255)**gamma) for v in range(256)])
        else: raise ValueError('未知背景处理档位。')
        buf=io.BytesIO();img.save(buf,format='PNG')
        dest=out.new_page(width=page.rect.width,height=page.rect.height)
        dest.insert_image(dest.rect,stream=buf.getvalue(),keep_proportion=False)
    return out

def export_scans(doc,path,fmt,dpi,temp):
    path=Path(path);temp=Path(temp)
    if fmt=='pdf': doc.save(path);return
    if fmt in ('jpeg','png','tiff'):
        if fmt!='tiff' and len(doc)!=1: raise ValueError('多页扫描请选 PDF 或 TIFF。')
        imgs=[]
        for p in doc:
            pix=p.get_pixmap(dpi=dpi,alpha=False);imgs.append(Image.frombytes('RGB',(pix.width,pix.height),pix.samples))
        imgs[0].save(path,format=fmt.upper(),dpi=(dpi,dpi),**({'save_all':True,'append_images':imgs[1:]} if fmt=='tiff' else {}));return
    paragraphs=[]
    for i,p in enumerate(doc):
        pix=p.get_pixmap(dpi=dpi,alpha=False); img=temp/f'ocr-{i}.png';pix.save(str(img))
        words=json.loads(run([str(BUILD/'Panel'),'ocr',str(img)],capture_output=True,text=True,check=True).stdout)
        paragraphs.extend(w['text'] for w in words)
        if fmt=='ocrpdf':
            for word in words:
                x,y,w,h=word['box'];t=word['text']
                pt=min(p.rect.height*h*.86,p.rect.width*w/max(.01,pdf.get_text_length(t,fontname='china-s',fontsize=1)))
                p.insert_text((x*p.rect.width,(1-y)*p.rect.height),t,fontname='china-s',fontsize=pt,render_mode=3)
    if fmt=='ocrpdf': doc.save(path);return
    if fmt=='txt': path.write_text('\n'.join(paragraphs),encoding='utf8');return
    from uuid import uuid4
    from datetime import datetime,timezone
    with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('mimetype','application/epub+zip',compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml','<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('book.opf',f'<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">urn:uuid:{uuid4()}</dc:identifier><dc:title>扫描文档</dc:title><dc:language>zh</dc:language><meta property="dcterms:modified">{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}</meta></metadata><manifest><item id="p" href="page.xhtml" media-type="application/xhtml+xml"/><item id="n" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="p"/></spine></package>')
        z.writestr('nav.xhtml','<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>目录</title></head><body><nav epub:type="toc"><ol><li><a href="page.xhtml">扫描文档</a></li></ol></nav></body></html>')
        z.writestr('page.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><head><title>扫描文档</title></head><body>'+''.join('<p>'+escape(t)+'</p>' for t in paragraphs)+'</body></html>')
