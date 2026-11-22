"""Aqua-session entry points for print preferences, scan, copy and WPS jobs."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory, NamedTemporaryFile
import fcntl, json, os, shutil, subprocess, sys, time
import pymupdf as pdf
from hp_profile import PAPERS
from settings import SUPPORT, BUILD, SETS, DEFAULTS, YES, panel, read_json, save_json, validate
from pages import plan_job, id_page
from printing import prefs_for_job, encode_job, dispatch, send_spl, status, counter, Cancelled
from scan import Scanner, export_scans, copy_background

def ask(title,detail,button): return panel(title,detail,[button,'取消'])[0]==button
def note(title,detail): panel(title,detail,['确定'])
def conf(): return read_json(SUPPORT/'device.json',{})
def preferences():return DEFAULTS | read_json(SUPPORT/'preferences.json',{})
def presets():return read_json(SUPPORT/'presets.json',{})
def select_preset(s):
    choices={'标准':DEFAULTS,'每面 2 页':DEFAULTS|dict(layout='nup',nup='2'),'小册子':DEFAULTS|dict(layout='booklet'),**presets()}
    act,val=panel('打印预设','选择已有预设，或保存当前选项。',['载入','保存当前','删除已存预设','取消'],[('预设',[('name','已有预设',[(k,k) for k in choices],next(iter(choices))),('new','新预设名称',None,'')])])
    if act=='载入':return choices[val['name']].copy()
    saved=presets()
    if act=='保存当前' and val['new'].strip() and val['new'] not in ('标准','每面 2 页','小册子'):
        saved[val['new'].strip()]=s.copy();save_json(SUPPORT/'presets.json',saved)
    if act=='删除已存预设' and val['name'] in saved:
        del saved[val['name']];save_json(SUPPORT/'presets.json',saved)
    return s
def preview(jobs,folder):
    # Hold the preview PDF on disk until the user has finished looking at it.
    paths=[]
    for i,j in enumerate(jobs,1):
        f=folder/f'预览-{i}.pdf';j.save(f);paths.append(str(f))
    subprocess.run(['/usr/bin/open','-a','Preview',*paths],check=True)
    note('排版预览','预览窗口按送纸顺序显示每一趟。查看后可返回修改设置。')

def run_print(source,s,temp,save_only=False,device=None,title=None):
    while True:
        btns=['保存为默认','预设…','取消'] if save_only else ['打印','预览','保存配置','预设…','保存为覆盖图','取消']
        act,updated=panel('HP 115nw 打印设置','Wi-Fi · SPL3     '+(title or (Path(source).name if source else '默认设置')),btns,SETS,s)
        s.update(updated)
        if act in ('取消','关闭'):raise Cancelled()
        try:
            if act=='预设…':s=select_preset(s);continue
            validate(s)
            if act.startswith('保存'):
                if act=='保存为覆盖图':
                    if s['overlay'] or s['wm']:raise ValueError('保存覆盖图前，请清空水印和已选覆盖图。')
                    with pdf.open(source) as d:
                        if len(d)!=1:raise ValueError('创建覆盖图需要一页原件。')
                        f=save_location('覆盖图','pdf')
                        if f:atomic_export(f,lambda dest:d.save(dest))
                    continue
                save_json(SUPPORT/'preferences.json',s)
                if save_only:return
                continue
            plan=plan_job(source,s)
            if act=='预览':preview(plan.passes,temp);continue
            if s['overlay'] and s['overlayconfirm']=='yes' and not ask('使用覆盖图',Path(s['overlay']).name,'确认使用'):continue
            wire=encode_job(plan,s,temp)
        except (ValueError,RuntimeError,OSError) as e:
            note('请检查打印设置',str(e));continue
        send_job(wire,plan.counts,s,device or conf())
        return

def send_job(wire,counts,s,device):
    host=device['host']
    def device_state():
        try:
            return status(host)['reason']
        except (OSError, subprocess.SubprocessError):
            return ''
    def confirm_output(title,detail):
        action,_=panel(title,detail,['成品已出齐','已在机身取消'])
        return action=='成品已出齐'
    # Copy and print jobs share the physical lock until the last sheet is accounted for.
    with (SUPPORT/'physical.lock').open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        dispatch(wire,counts,s,lambda p:send_spl(p,host),note,confirm_output,
                 lambda:counter(host),device_state)

def save_location(name,fmt):
    ex={'ocrpdf':'pdf','jpeg':'jpg','tiff':'tiff'}.get(fmt,fmt)
    initial=str(Path.home()/'Documents'/f'{name}.{ex}')
    act,val=panel('保存文件','选择保存位置。',['保存','取消'],[('文件',[('path','文件位置','save',initial)])])
    if act!='保存' or not val['path'].strip():return None
    loc=Path(val['path']).expanduser()
    if loc.exists() and not ask('替换已有文件',loc.name,'替换'):return None
    return loc

def atomic_export(dest,write):
    with NamedTemporaryFile(dir=dest.parent,suffix=dest.suffix,delete=False) as temp: path=Path(temp.name)
    try:
        write(path);os.replace(path,dest)
    finally:path.unlink(missing_ok=True)

def scan_settings(caps,mode):
    profiles=caps['profiles']; resolutions=sorted(set.union(*(p[2] for p in profiles)));colors=set.union(*(p[0] for p in profiles))
    rows=[('scan_dpi','扫描分辨率',[(str(v),f'{v} dpi') for v in resolutions],str(300 if 300 in resolutions else resolutions[-1])),
          ('color','扫描颜色',[(k,v) for k,v in [('RGB24','彩色'),('Grayscale8','灰度'),('BlackAndWhite1','黑白')] if k in colors],'RGB24' if mode=='扫描' else 'Grayscale8'),
          ('intent','原件类型',[(k,v) for k,v in [('Document','文字'),('TextAndGraphic','文字与照片'),('Photo','照片')] if k in caps['intents']],'TextAndGraphic'),
          ('x','扫描区域左边 mm',None,'0'),('y','扫描区域上边 mm',None,'0'),
          ('w','原件宽 mm',None,'210'),('h','原件长 mm',None,'297'),('multi','逐张更换原件',YES,'no')]
    if mode=='扫描': rows.append(('fmt','保存格式',[('pdf','PDF'),('ocrpdf','可搜索 PDF · OCR'),('jpeg','JPEG'),('png','PNG'),('tiff','多页 TIFF'),('txt','OCR 文本'),('epub','OCR 电子书 EPUB')],'pdf'))
    else: rows.extend([('copies','复印份数（1–99）',None,'1'),('copy_scale','缩放（25%–400%）',None,'100'),
                       ('darkness','浓淡（−5 至 5）',None,'0'),('background','复印背景',[('off','关'),('auto','自动'),('enhance1','增强 1'),('enhance2','增强 2'),('erase1','擦除 1'),('erase2','擦除 2'),('erase3','擦除 3'),('erase4','擦除 4')],'off'),('copy_layout','复印布局',[('normal','普通'),('id','身份证正反面同张'),('2','2 合 1'),('4','4 合 1')],'normal'),
                       ('collate','逐份输出',YES,'yes'),('paper','输出纸张',[(k,PAPERS[k][0]) for k in ('A4','Letter','A5','Legal')],'A4')])
    return [('扫描或复印',rows)]

def scan_copy(mode,folder):
    device=conf(); scanner=Scanner('http://'+device['host']+'/eSCL')
    caps=scanner.capabilities()
    groups=scan_settings(caps,mode);s=read_json(SUPPORT/('scan.json' if mode=='扫描' else 'copy.json'),{})
    while True:
        act,vals=panel('HP 115nw · '+mode,'玻璃板原件正面朝下，靠扫描原点对齐。',['开始'+mode,'预扫描','保存配置','取消'],groups,s)
        s.update(vals)
        if act=='取消':return
        try:
            dpi=int(s['scan_dpi']);w,h,x,y=[float(s[k]) for k in ('w','h','x','y')]
            is_id=mode=='复印' and s['copy_layout']=='id'
            if is_id:w,h=85.6,53.98
            if mode=='复印':
                if not 1<=int(s['copies'])<=99 or not 25<=int(s['copy_scale'])<=400 or not -5<=int(s['darkness'])<=5:raise ValueError('请检查份数、缩放和浓淡。')
            if act=='保存配置':save_json(SUPPORT/('scan.json' if mode=='扫描' else 'copy.json'),s);continue
            if act=='预扫描':
                prev=scanner.scan(min(set.union(*(p[2] for p in caps['profiles']))),s['color'],'Preview',w,h,x,y)
                preview([prev],folder);continue
            dest=save_location('扫描文档',s['fmt']) if mode=='扫描' else None
            if mode=='扫描' and dest is None:continue
            if is_id and not ask('身份证正面','正面朝下，卡片长边横放，两边贴齐玻璃板扫描原点。','扫描正面'):return
            def get():return scanner.scan(dpi,s['color'],s['intent'],w,h,x,y)
            first=get()
            if is_id:
                if not ask('身份证背面','翻到背面，按相同方向和位置对齐。','扫描背面'):return
                result=id_page(first,get())
            else:
                result=pdf.open();result.insert_pdf(first)
                while s['multi']=='yes':
                    btn,_=panel('逐张扫描','更换原件后可继续扫描。',['已放好，扫描下一张','完成','取消'])
                    if btn=='完成':break
                    if btn=='取消':return
                    result.insert_pdf(get())
            if mode=='扫描':
                atomic_export(dest,lambda f:export_scans(result,f,s['fmt'],dpi,folder))
                note('扫描已保存',str(dest));return
            output=DEFAULTS|dict(paper=s['paper'],copies=s['copies'],collate=s['collate'],scale='100' if is_id else s['copy_scale'],
                                  brightness=str(-int(s['darkness'])*8),binding='none',text='off')
            if s['copy_layout'] in ('2','4'):output.update(layout='nup',nup=s['copy_layout'])
            source=folder/'复印原件.pdf';copy_background(result,s['background'],dpi).save(source)
            run_print(source,output,folder,device=device);return
        except (ValueError,OSError) as e:note('扫描或复印未完成',str(e))

def library_home():
    with TemporaryDirectory(prefix='hp115nw-') as d:
        folder=Path(d)
        while True:
            mode,_=panel('HP 115nw 打印与扫描','Wi-Fi · 打印、扫描、复印和设备状态',['扫描','复印','打印设置','设备状态','关闭'])
            if mode=='关闭':return
            if mode in ('扫描','复印'):scan_copy(mode,folder)
            elif mode=='打印设置':
                try:run_print(None,preferences(),folder,True)
                except Cancelled:pass
            elif mode=='设备状态':
                host=conf()['host'];stat=status(host);lifecount=counter(host)
                sc=Scanner('http://'+host+'/eSCL')
                code,_,body=sc.request('GET','/eSCL/ScannerStatus')
                import xml.etree.ElementTree as ET
                state=ET.fromstring(body).findtext('{http://www.pwg.org/schemas/2010/12/sm}State') if code==200 else '未知'
                info=f"打印机 {stat['model']}\n打印状态 {stat['state']} · {stat['reason']}\n黑色碳粉 {stat['toner'] or '未知'}%\n累计输出 {lifecount if lifecount is not None else '未知'}\n扫描仪 {state}"
                note('设备状态',info)

def main():
    args=sys.argv[1:];os.umask(0o077)
    if not args or args==['home']:library_home();return
    if len(args)==3 and args[0]=='process':
        source=Path(args[1]);opts=json.loads(Path(args[2]).read_text())['Options']
        with TemporaryDirectory(prefix='hp115nw-job-') as d:run_print(source,prefs_for_job(opts,preferences()),Path(d),title=opts.get('job-name'))
    elif args[0]=='pdf-service' and len(args)>=4:
        # The Mac print panel has already laid out this PDF. Relay supported quality and copies.
        import shlex
        tokens=dict(t.split('=',1) for t in shlex.split(args[2]) if '=' in t)
        raw=tokens.copy()
        if tokens.get('cupsPrintQuality')=='High' or tokens.get('Resolution','').startswith('1200'):raw['print-quality']='5'
        s=prefs_for_job(raw,preferences())
        s['binding']=read_json(SUPPORT/'preferences.json',{}).get('binding',read_json(SUPPORT/'duplex-calibration.json',{}).get('binding','long'))
        if s['binding']=='none':s['binding']='long'
        with TemporaryDirectory(prefix='hp115nw-duplex-') as d:run_print(Path(args[3]),s,Path(d),title=args[1])
    else:raise ValueError('未知操作。')
if __name__=='__main__':
    try:main()
    except Cancelled:sys.exit(2)
    except Exception as e:
        print(str(e),file=sys.stderr)
        note('HP 115nw 未完成操作',str(e));sys.exit(1)
