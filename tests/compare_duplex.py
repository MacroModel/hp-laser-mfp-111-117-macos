"""Compare the native Mac job with the two captured Windows manual-duplex jobs."""
import argparse
import json
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from pages import plan_job
from printing import encode_job
from settings import DEFAULTS
from streams import image, read


def identify(page, bits=3):
    if not page['bands']:
        return {'page': None, 'rotation': None}
    # Registration square and three binary page-number marks on the supplied
    # four-page target. These coordinates are for A4 fitted to US Letter.
    thumb = image(page).convert('L').resize((255, 330), Image.Resampling.BOX)
    upright = thumb.getpixel((214, 22)) < 64
    inverted = thumb.getpixel((30, 295)) < 64
    assert upright != inverted, 'missing or ambiguous corner marker'
    coords = [(round(27+12.6*i),218) if upright else (round(217-12.6*i),104)
              for i in range(bits)]
    number = sum(1 << i for i, xy in enumerate(coords) if thumb.getpixel(xy) < 64)
    assert number >= 1, 'unreadable page-number marker'
    return {'page': number, 'rotation': 0 if upright else 180}


def compare(source, long_reference, short_reference, output):
    result = {}
    for edge, reference, logical in [('long',long_reference,4),('short',short_reference,3)]:
        ticket, win = read(reference.read_bytes())
        options = DEFAULTS | dict(binding=edge,pages=f'1-{logical}',paper='Letter',dpi='600',fit='paper',text='off')
        with TemporaryDirectory() as tmp:
            wire = encode_job(plan_job(source, options), options, Path(tmp)).read_bytes()
        mac_ticket, mac = read(wire)
        assert wire.count(b'@PJL ENTER LANGUAGE') == 1
        keys = ['COPIES','PAGESPERCOPIES','DUPLEX','BINDING','RESOLUTION','PAPERTYPE','XIGNOREFF']
        assert {k:ticket[k] for k in keys} == {k:mac_ticket[k] for k in keys}
        assert len(win)==len(mac)==4
        assert all(w['header']==m['header'] for w,m in zip(win,mac))
        windows_marks = [identify(p) for p in win]
        mac_marks = [identify(p) for p in mac]
        assert windows_marks == mac_marks
        assert [p['page'] for p in mac_marks] == ([4,2,1,3] if edge=='long' else [None,2,1,3])
        assert [p['rotation'] for p in mac_marks] == ([180,180,0,0] if edge=='long' else [None,0,0,0])
        result[edge] = dict(windows_ticket={k:ticket[k] for k in keys},mac_job_count=1,
                            physical_passes=[2,2],matching_page_headers=4,
                            windows_raster_marks=windows_marks,mac_raster_marks=mac_marks,
                            matching_auxiliary_records=sum(x==y for a,b in zip(win,mac) for x,y in zip(a['extras'],b['extras'])),
                            auxiliary_records=12,windows_bands=dict(Counter(hex(b[3]) for p in win for b in p['bands'])),
                            mac_bands=dict(Counter(hex(b[3]) for p in mac for b in p['bands'])))
    output.write_text(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    args=argparse.ArgumentParser()
    for name in ('source','long','short','output'):args.add_argument(name,type=Path)
    a=args.parse_args()
    for edge,row in compare(a.source,a.long,a.short,a.output).items():
        print(edge,row['mac_raster_marks'],row['mac_bands'])
