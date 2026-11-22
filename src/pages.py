"""Compose logical pages, then impose them on the two physical passes."""
import io, math, re
from dataclasses import dataclass
from pathlib import Path
import pymupdf as pdf
from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, NameObject, FloatObject
from hp_profile import dimensions, PT, GRID
from settings import validate

def text_colors(doc, dark, black):
    if dark=='off' and not black: return doc
    writer=PdfWriter(clone_from=PdfReader(io.BytesIO(doc.tobytes())))
    handled=set()
    def style(stream):
        tokens=ContentStream(stream,writer)
        out=[]; stack=[]
        initial=([([FloatObject(0)],b'g')],[([FloatObject(0)],b'G')])
        fill,stroke=initial
        factor={'off':1,'min':.85,'mid':.7,'max':.45}[dark]
        for operands, op in tokens.operations:
            if op==b'q': stack.append((fill,stroke))
            if op==b'Q' and stack: fill,stroke=stack.pop()
            if op in (b'g',b'rg',b'k',b'cs'): fill=[(operands,op)]
            elif op in (b'G',b'RG',b'K',b'CS'): stroke=[(operands,op)]
            elif op in (b'sc',b'scn'): fill=fill[:1]+[(operands,op)]
            elif op in (b'SC',b'SCN'): stroke=stroke[:1]+[(operands,op)]
            if op not in (b'Tj',b'TJ',b"'",b'"'):
                out.append((operands,op)); continue
            before=[]; restore=[]
            for state,gray in [(fill,b'g'),(stroke,b'G')]:
                args,color=state[-1]
                if black:
                    before.append(([FloatObject(0)],gray))
                elif color.lower() in (b'rg',b'g') and any(float(v) for v in args):
                    before.append(([FloatObject(float(v)*factor) for v in args],color))
                elif color.lower()==b'k':
                    new=list(args); new[-1]=FloatObject(1-(1-float(new[-1]))*factor); before.append((new,color))
                else: continue
                restore.extend(state)
            out.extend(before); out.append((operands,op)); out.extend(restore)
        tokens.operations=out
        return tokens
    def forms(resources):
        if not resources: return
        for ref in resources.get('/XObject',{}).get_object().values() if '/XObject' in resources else ():
            obj=ref.get_object()
            if obj.get('/Subtype')!='/Form' or ref.idnum in handled: continue
            handled.add(ref.idnum)
            child=style(obj); obj[NameObject('/Filter')]=NameObject('/FlateDecode')
            import zlib
            obj._data=zlib.compress(child.get_data())
            obj.pop('/DecodeParms',None)
            if hasattr(obj,'decoded_self'): obj.decoded_self=None
            forms(obj.get('/Resources',{}))
    for page in writer.pages:
        if page.get_contents() is not None: page.replace_contents(style(page.get_contents()))
        forms(page.get('/Resources',{}))
    buf=io.BytesIO(); writer.write(buf)
    return pdf.open(stream=buf.getvalue(),filetype='pdf')

def indices(count,s):
    wanted=set()
    for part in (s['pages'] or f'1-{count}').split(','):
        match=re.fullmatch(r'\s*(\d+)\s*(?:-\s*(\d+)\s*)?',part)
        if not match: raise ValueError('页码范围无效，请填写例如 1-3,5。')
        a=int(match[1]); b=int(match[2] or a)
        if not 1<=a<=b<=count: raise ValueError(f'页码超出文档范围 1–{count}。')
        wanted.update(range(a,b+1))
    out=[i-1 for i in sorted(wanted) if s['subset']=='all' or (i%2==1)==(s['subset']=='odd')]
    return out[::-1] if s['reverse']=='yes' else out

def blank(page,dpi):
    r=page.rect
    for y in range(0,math.ceil(r.height),48):
        clip=pdf.Rect(r.x0,r.y0+y,r.x1,min(r.y1,r.y0+y+48))
        pix=page.get_pixmap(dpi=dpi,colorspace=pdf.csGRAY,alpha=False,clip=clip)
        if pix.samples.count(255)!=pix.width*pix.height: return False
    return True

def centered(frame,w,h):
    x=(frame.x0+frame.x1-w)/2; y=(frame.y0+frame.y1-h)/2
    return pdf.Rect(x,y,x+w,y+h)

def place(page,frame,doc,index,mode='fit',scale=1):
    if index is None or not doc[index].get_contents(): return
    orig=doc[index].rect
    ratio=scale*(min(frame.width/orig.width,frame.height/orig.height) if mode=='fit' else 1)
    dest=centered(frame,orig.width*ratio,orig.height*ratio)
    visible=dest & frame
    if not visible.is_empty:
        clip=pdf.Rect((visible.x0-dest.x0)/ratio,(visible.y0-dest.y0)/ratio,
                      (visible.x1-dest.x0)/ratio,(visible.y1-dest.y0)/ratio) & orig
        page.show_pdf_page(visible,doc,index,clip=clip)

def frame_border(page,r,kind):
    if kind=='none': return
    r=r+(1,1,-1,-1)
    if kind in ('corner','crop'):
        for x,sgnx in ((r.x0,1),(r.x1,-1)):
            for y,sgny in ((r.y0,1),(r.y1,-1)):
                gap=1 if kind=='crop' else 0
                page.draw_line((x+sgnx*gap,y),(x+sgnx*9,y),width=.55)
                page.draw_line((x,y+sgny*gap),(x,y+sgny*9),width=.55)
        return
    dash={'dash':'[5 3] 0','dot':'[1 2] 0','chain':'[5 2 1 2] 0','chaindouble':'[5 2 1 2 1 2] 0'}.get(kind)
    page.draw_rect(r,color=(.65,) if kind=='trans' else (0,),width=.65,dashes=dash)
    if kind in ('double','3d'):
        page.draw_rect(r+(2,2,-2,-2),color=(.65,) if kind=='3d' else (0,),width=.65)

def watermark(page,s):
    text=s['wm']
    if not text: return
    pt=int(s['wmpt']); c=int(s['wmgray'])/100; font=s['wmfont']
    width=pdf.get_text_length(text,fontname=font,fontsize=pt)
    pos={'top':.2,'bottom':.8,'center':.5}[s['wmalign']]
    mid=pdf.Point(page.rect.width/2,page.rect.height*pos)
    mat=pdf.Matrix(int(s['wmangle']))
    if 'italic' in s['wmstyle']: mat.preshear(.22,0)
    page.insert_text((mid.x-width/2,mid.y+pt*.34),text,fontname=font,fontsize=pt,color=(c,),fill=(c,),
                     render_mode=2 if 'bold' in s['wmstyle'] else 0,border_width=.022,morph=(mid,mat),overlay=True)

def adjusted(doc,s,dpi):
    bright=int(s['brightness']); contrast=int(s['contrast'])
    if not (bright or contrast): return doc
    out=pdf.open()
    factor=(100+contrast)/(100.1-contrast)
    table=[max(0,min(255,round((v-127.5)*factor+127.5+bright*2.55))) for v in range(256)]
    for p in doc:
        dest=out.new_page(width=p.rect.width,height=p.rect.height)
        for start in range(0,math.ceil(p.rect.height),48):
            clip=pdf.Rect(0,start,p.rect.width,min(start+48,p.rect.height))
            pix=p.get_pixmap(dpi=dpi,clip=clip,colorspace=pdf.csGRAY,alpha=False)
            img=Image.frombytes('L',(pix.width,pix.height),pix.samples).point(table)
            buf=io.BytesIO(); img.save(buf,format='PNG')
            dest.insert_image(pdf.Rect(pix.x,pix.y,pix.x+pix.width,pix.y+pix.height)*(72/dpi),stream=buf.getvalue(),keep_proportion=False)
    return out

@dataclass
class PrintPlan:
    passes: list
    binding: str
    pages_per_copy: int

    @property
    def counts(self):
        return [len(doc) for doc in self.passes]


def plan_job(source,s):
    validate(s)
    doc=pdf.open(source)
    if not doc.is_pdf or doc.needs_pass or not doc.page_count or not doc.permissions & (pdf.PDF_PERM_PRINT|pdf.PDF_PERM_PRINT_HQ):
        raise ValueError('文档不可打印。')
    # Preserve printable form values and annotations before composing new pages.
    for page in doc:
        for annot in list(page.annots() or []):
            if not annot.flags & pdf.PDF_ANNOT_IS_PRINT or annot.flags & (pdf.PDF_ANNOT_IS_HIDDEN|pdf.PDF_ANNOT_IS_INVISIBLE):
                page.delete_annot(annot)
    doc.bake()
    for page in doc:
        if page.rotation: page.remove_rotation()
    doc=text_colors(doc,s['text'],s['black']=='yes')
    if s['overlay']:
        overlay=pdf.open(s['overlay'])
        if not overlay.is_pdf or len(overlay)!=1: raise ValueError('覆盖图需要单页 PDF。')
    for i,p in enumerate(doc):
        if s['overlay']:
            if abs(overlay[0].rect.width-p.rect.width)>2 or abs(overlay[0].rect.height-p.rect.height)>2: raise ValueError('覆盖图与原件的纸张尺寸不一致。')
            if overlay[0].get_contents(): p.show_pdf_page(p.rect,overlay,0,overlay=True)
        if i==0 or s['wmfirst']=='no': watermark(p,s)
    chosen=indices(len(doc),s)
    typ=s['layout']; binding=s['binding']
    if typ=='booklet': binding='short'
    if binding=='none' and s['skip']=='yes' and s['border']=='none':
        chosen=[i for i in chosen if not blank(doc[i],int(s['dpi']))]
    if not chosen: raise ValueError('所选范围没有需要输出的页面。')
    fw,fh=(v*PT for v in dimensions(s))
    landscape=(typ=='booklet' or s['orientation']=='landscape' or
               s['orientation']=='auto' and doc[chosen[0]].rect.width>doc[chosen[0]].rect.height)
    w,h=(max(fw,fh),min(fw,fh)) if landscape else (min(fw,fh),max(fw,fh))
    # 50 Windows device-grid units on all sides, except Monarch's 55 units.
    margin=13.2 if s['paper']=='Monarch' else 12
    area=pdf.Rect(margin,margin,w-margin,h-margin)
    output=pdf.open(); scale=int(s['scale'])/100
    def sheet(): return output.new_page(width=w,height=h)
    if typ=='booklet':
        slots=chosen+[None]*(-len(chosen)%4)
        for n in range(len(slots)//4):
            for pair in ((slots[-1-2*n],slots[2*n]),(slots[2*n+1],slots[-2-2*n])):
                p=sheet()
                for ix,num in enumerate(pair):
                    r=pdf.Rect(margin if ix==0 else w/2,margin,w/2 if ix==0 else w-margin,h-margin)
                    place(p,r,doc,num,scale=scale); frame_border(p,r,s['border'])
    elif typ.startswith('poster'):
        n=int(typ[-1]); overlap=float(s['overlap'])*PT
        for i in chosen:
            src=doc[i].rect; wide=area.width*n-overlap*(n-1); tall=area.height*n-overlap*(n-1)
            ratio=min(wide/src.width,tall/src.height)*scale
            image=centered(pdf.Rect(0,0,wide,tall),src.width*ratio,src.height*ratio)
            for row in range(n):
                for col in range(n):
                    p=sheet(); x=col*(area.width-overlap); y=row*(area.height-overlap)
                    visible=image & pdf.Rect(x,y,x+area.width,y+area.height)
                    if not visible.is_empty and doc[i].get_contents():
                        clip=(visible+(-image.x0,-image.y0,-image.x0,-image.y0))*(1/ratio)
                        p.show_pdf_page(visible+(margin-x,margin-y,margin-x,margin-y),doc,i,clip=clip)
    else:
        n=int(s['nup']) if typ=='nup' else 1
        cols,rows=GRID[n]
        if w<h and n in (2,6): cols,rows=rows,cols
        for base in range(0,len(chosen),n):
            p=sheet()
            for j,i in enumerate(chosen[base:base+n]):
                order=s['order']
                x,y=(j%cols,j//cols) if order[:2] in ('lr','rl') else (j//rows,j%rows)
                if 'rl' in order: x=cols-1-x
                if 'bt' in order: y=rows-1-y
                r=pdf.Rect(area.x0+x*area.width/cols,area.y0+y*area.height/rows,
                           area.x0+(x+1)*area.width/cols,area.y0+(y+1)*area.height/rows)
                frame=p.rect if n==1 and s['fit']=='paper' else r
                place(p,frame,doc,i,mode='fit' if n>1 or s['fit']=='paper' else s['fit'],scale=scale)
                frame_border(p,r,s['border'])
    output=adjusted(output,s,int(s['dpi']))
    count=len(output); copies=int(s['copies']); size=count if binding=='none' else (count+1)//2
    base=list(range(size))
    order=base*copies if s['collate']=='yes' else [i for i in base for _ in range(copies)]
    if binding=='none': runs=[order]
    else:
        fronts=order if s['out']=='down' else order[::-1]
        backs=fronts[::-1] if s['out']==s['reload'] else fronts
        runs=[[2*i+1 for i in backs],[2*i for i in fronts]]
    jobs=[]
    for runno,run in enumerate(runs):
        out=pdf.open()
        # HP sends long-edge even pages rotated and odd pages upright. The second
        # pass stays upright for both bindings with the factory feed direction.
        backrotate=binding!='none' and runno==0 and ((binding=='short') ^ (s['head']=='outside') ^ (fw>fh))
        angle=(90 if (w>h)!=(fw>fh) else 0)+(180 if (s['rotate']=='yes') ^ backrotate else 0)
        for i in run:
            p=out.new_page(width=fw,height=fh)
            if i<count and output[i].get_contents(): p.show_pdf_page(p.rect,output,i,rotate=angle)
        jobs.append(out)
    # Copies are already imposed in collated or uncollated order. Inner separator
    # blanks in an expanded set are real sides as far as the firmware is concerned.
    pages_per_copy = count if copies == 1 else sum(len(part) for part in jobs)
    return PrintPlan(jobs, binding, pages_per_copy)


def prepare(source,s):
    return plan_job(source,s).passes

def id_page(front,back):
    """Both inputs were acquired from the same marked corner at full card size."""
    out=pdf.open(); page=out.new_page(width=210*PT,height=297*PT)
    for i,scan in enumerate((front,back)):
        r=centered(pdf.Rect(0,page.rect.height*i/2,page.rect.width,page.rect.height*(i+1)/2),85.60*PT,53.98*PT)
        page.show_pdf_page(r,scan,0)
    return out
