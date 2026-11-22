import argparse, json, sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image, ImageChops, ImageOps, ImageStat
from streams import image,read
from settings import DEFAULTS
from printing import encode
from pages import prepare

def report(wins,source,out):
    out.mkdir(exist_ok=True,parents=True);results={}
    for dpi,ref in wins.items():
        metadata,wp=read(ref.read_bytes())
        assert len(wp)==4 and all(p['dpi']==(dpi,dpi) for p in wp)
        assert all(p['code']==0 and p['geometry']==(2550,3300) for p in wp)
        s=DEFAULTS|dict(paper='Letter',dpi=str(dpi),fit='paper',text='off')
        jobs=prepare(source,s)
        with TemporaryDirectory() as tmp:
            wire=encode(jobs[0],s,Path(tmp),1)
            mac,mp=read(wire.read_bytes())
        critical=['RESOLUTION','DUPLEX','PAPERTYPE','COLORMODE','EDGEENHANCE','COPIES','XIGNOREFF','ALTITUDE','ECONOMODE','IMAGEQUALITY','TIMESTAMP','BANNERSHEET']
        assert all(mac.get(k)==metadata.get(k) for k in critical)
        assert all(x['header']==y['header'] for x,y in zip(mp,wp)) and len(mp)==len(wp)
        assert {b[1:3] for x in mp for b in x['bands']}=={b[1:3] for x in wp for b in x['bands']}
        result={'windows_bytes':ref.stat().st_size,'pages':len(wp),'paper':'Letter','page_headers_identical':True,'critical_PJL_matched':critical,
                'win_algorithms':dict(Counter(hex(b[3]) for x in wp for b in x['bands'])),'mac_algorithms':dict(Counter(hex(b[3]) for x in mp for b in x['bands'])),
                'vendor_extra_records_per_page':[len(x['extras']) for x in wp]}
        if dpi in (600,1200):
            layouts=[]; previews=[]
            for i,(x,y) in enumerate(zip(wp,mp)):
                pair=[image(x),image(y)]
                bounds=[]
                for im in pair:
                    w,h=im.size;crop=(int(w*.70),int(h*.018),int(w*.92),int(h*.13))
                    with ImageOps.invert(im.crop(crop).convert('L')) as black:
                        a,b,c,d=black.getbbox()
                        bounds.append([a+crop[0],b+crop[1],c+crop[0],d+crop[1]])
                layouts.append({'page':i+1,'windows_top_right_marker':bounds[0],'mac_top_right_marker':bounds[1]})
                if i==0:
                    for im in pair:previews.append(im.convert('L').resize((340,440),Image.Resampling.LANCZOS))
            contact=Image.new('L',(680,440),255)
            for i,im in enumerate(previews):contact.paste(im,(340*i,0))
            contact.save(out/f'{dpi}-comparison.png');result['decoded_registration']=layouts
        results[str(dpi)]=result
    (out/'windows-comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    return results
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('sample',type=Path);parser.add_argument('windows600',type=Path);parser.add_argument('windows1200',type=Path);parser.add_argument('output',type=Path)
    a=parser.parse_args();print(json.dumps(report({600:a.windows600,1200:a.windows1200},a.sample,a.output),ensure_ascii=False))
