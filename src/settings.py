from hp_profile import PAPERS, MEDIA, dimensions
from pathlib import Path
import json, os, tempfile
from subprocess import run

APP_NAME = "HP Laser MFP 111-117 Studio"
SUPPORT = Path.home() / "Library/Application Support/HP115nw Studio"
BUILD = Path(__file__).resolve().parents[1] / "build"
def choices(**values):
    return list(values.items())

YES = [('no','关'),('yes','开')]
BINDING_CHOICES = [('none','Off'),('long','On (Long Edge)'),('short','On (Short Edge)')]
SETS = [
("纸张", [
('paper','纸张',[(k,v[0]) for k,v in PAPERS.items()]+[('Custom','自定义')],'A4'),
('width','自定义宽度 mm',None,'210'),('height','自定义长度 mm',None,'297'),
('media','介质',list(MEDIA.items()),'OFF'),('tray','来源',[('Auto','自动'),('Upper','主纸盘')],'Auto'),
('dpi','打印分辨率',[('600','600 × 600 dpi'),('1200','1200 × 1200 dpi')],'600'),
('copies','份数（1–999）',None,'1'),('collate','逐份打印',YES,'yes'),
('orientation','纸张方向',[('auto','随原文'),('portrait','纵向'),('landscape','横向')],'auto'),
('rotate','整体旋转 180°',YES,'no')]),
("双面与布局", [
('binding','手动双面',BINDING_CHOICES,'none'),
('out','出纸时已印面',[('down','朝下'),('up','朝上')],'down'),
('reload','放回时已印面',[('down','朝下 · 空白面朝上'),('up','朝上 · 空白面朝下')],'down'),
('head','第一趟纸张页首',[('outside','朝外 · 靠自己'),('inside','靠机身')],'outside'),
('layout','版式',[('normal','普通'),('nup','每面多页'),('booklet','折页小册子'),('poster2','海报 2 × 2'),('poster3','海报 3 × 3'),('poster4','海报 4 × 4')],'normal'),
('nup','每面页数',[(str(i),str(i)) for i in (2,4,6,9,16)],'2'),
('order','多页排列',[('lrtb','先右后下'),('rltb','先左后下'),('lrbt','先右后上'),('rlbt','先左后上'),('tblr','先下后右'),('tbrl','先下后左'),('btlr','先上后右'),('btrl','先上后左')],'lrtb'),
('border','页框',list(zip(['none','solid','dash','dot','chain','chaindouble','3d','trans','double','crop','corner'],['无','实线','虚线','点线','点划线','双点划线','立体线','浅灰线','双实线','裁切标记','角标记'])),'none'),
('overlap','海报重叠 mm',None,'5'),('scale','缩放百分比',None,'100'),
('fit','缩放基准',[('paper','适合整张纸'),('fit','适合可印区域'),('actual','原件实际尺寸')],'actual'),
('pages','页码范围（如 1-3,5）',None,''),
('subset','打印页',[('all','全部'),('odd','奇数'),('even','偶数')],'all'),('reverse','倒序',YES,'no'),
('skip','跳过空白页（单面）',YES,'no')]),
("图像与文字", [
('econ','省墨',YES,'no'),('edge','边缘增强',list(zip(['OFF','NORMAL','MAXIMUM'],['关','标准','最大'])),'NORMAL'),
('text','加深文字',list(zip(['off','min','mid','max'],['关','最小','中等','最大'])),'mid'),('black','所有文字用黑色',YES,'no'),
('brightness','亮度（−100 至 100）',None,'0'),('contrast','对比度（−100 至 100）',None,'0'),
('altitude','海拔',[('LOW','普通'),('HIGH1','高 1'),('HIGH2','高 2'),('HIGH3','高 3')],'LOW')]),
("水印与覆盖图", [
('wm','水印文本（最多 256 字）',None,''),('wmfirst','只在首页加水印',YES,'no'),
('wmfont','水印字体',[('china-s','宋体 · 中文'),('helv','Helvetica'),('tiro','Times'),('cour','Courier')],'china-s'),
('wmstyle','水印字形',[('regular','常规'),('bold','粗体'),('italic','斜体'),('bolditalic','粗斜体')],'regular'),
('wmangle','角度（−180 至 180°）',None,'45'),('wmpt','字号（pt）',None,'42'),
('wmgray','灰度百分比',None,'55'),('wmalign','水印位置',[('center','中间'),('top','上方'),('bottom','下方')],'center'),
('overlay','单页 PDF 覆盖图','file',''),('overlayconfirm','使用前确认',YES,'no')])]
DEFAULTS={k:v for _,rows in SETS for k,_,_,v in rows}

def limit(s,key,low,high,integer=True):
    value=(int if integer else float)(s[key])
    if not low <= value <= high: raise ValueError(f"{key} 的有效范围为 {low}–{high}。")
    return value

def validate(s):
    for _,rows in SETS:
        for k,label,opts,_ in rows:
            if isinstance(opts,list) and s[k] not in dict(opts): raise ValueError(f"{label}选项无效。")
    for k,lo,hi in [('copies',1,999),('scale',10,400),('brightness',-100,100),('contrast',-100,100),('wmgray',0,100),('wmpt',4,300),('wmangle',-180,180)]:
        limit(s,k,lo,hi)
    limit(s,'overlap',0,25,False); dimensions(s)
    paper=s['paper'];media=s['media']
    envelopes=('DL','C5','Env10','Monarch')
    if paper in envelopes and media!='ENV':raise ValueError('打印信封时请把介质选为「信封」。')
    if media=='ENV' and paper not in envelopes+('Custom',):raise ValueError('请选信封尺寸或自定义尺寸。')
    if paper=='Postcard' and media!='CARD':raise ValueError('4 × 6 英寸卡片请配合「卡纸」介质。')
    if media in ('LABEL','CARD') and paper=='Custom':raise ValueError('此纸材在原厂配置中须选择标准纸型。')
    if len(s['wm'])>256: raise ValueError("水印最多 256 个字符。")
    if s['layout'].startswith('poster') and s['dpi']=='1200': raise ValueError("HP 原厂配置中，海报模式使用 600 dpi。")
    if s['layout']=='booklet' and s['paper'] not in ('A4','Letter','Legal','Folio','Oficio'): raise ValueError("小册子可选 A4、Letter、Legal、Folio 或 Oficio。")
    if s['overlay'] and not Path(s['overlay']).is_file(): raise ValueError("找不到所选覆盖图。")
    return s

def tabs(groups, values):
    result=[]
    for name, fields in groups:
        rows=[]
        for key,label,options,initial in fields:
            row=dict(key=key,label=label,value=str(values.get(key,initial)))
            if isinstance(options,list): row['choices']=options
            elif options: row['picker']=options
            rows.append(row)
        result.append(dict(name=name,rows=rows))
    return result

def panel(title,detail,buttons,groups=(),values=None):
    form=dict(title=title,detail=detail,buttons=buttons,tabs=tabs(groups,values or {}))
    res=run([str(BUILD/'Panel')],input=json.dumps(form),text=True,capture_output=True,check=True)
    answer=json.loads(res.stdout)
    return answer['action'],answer['values']

def read_json(path, default):
    try: return json.loads(path.read_text())
    except FileNotFoundError: return default

def save_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent,mode='w',delete=False) as f:
        json.dump(value,f,ensure_ascii=False,indent=2)
    os.replace(f.name,path)
