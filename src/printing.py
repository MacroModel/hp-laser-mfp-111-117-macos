"""Prepare one complete SPL3 job; the 111–117 firmware controls the reload."""
import os,re,socket,subprocess,tempfile,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, ExitStack
from hp_profile import PAPERS, dimensions
from settings import BINDING_CHOICES, BUILD, panel
from make_ppd import ppd

def prefs_for_job(opts,defaults):
    s=defaults.copy()
    if 'print-quality' in opts: s['dpi']='1200' if opts['print-quality'] in ('5','high') else '600'
    if str(opts.get('printer-resolution','')).startswith('1200'): s['dpi']='1200'
    for name,data in PAPERS.items():
        if opts.get('media')==data[-1]:s['paper']=name
    if 'x-dimension' in opts and 'y-dimension' in opts:
        from hp_profile import paper_default
        w,h=float(opts['x-dimension'])/100,float(opts['y-dimension'])/100
        s.update(paper=paper_default(w,h),width=str(w),height=str(h))
    types={'stationery':'NORMAL','stationery-heavyweight':'THICK','stationery-lightweight':'THIN','stationery-bond':'BOND','stationery-colored':'COLOR','cardstock':'CARD','labels':'LABEL','envelope':'ENV','stationery-preprinted':'USED','stationery-recycled':'RECYCLED'}
    if opts.get('media-type'): s['media']=types.get(opts['media-type'],opts['media-type'].upper() if opts['media-type'].upper() in ('NORMAL','OFF','THICK','THIN','BOND','COLOR','CARD','LABEL','ENV','USED','RECYCLED') else 'OFF')
    if 'media-source' in opts: s['tray']='Upper' if opts['media-source'] in ('main','upper','tray-1') else 'Auto'
    if 'sides' in opts:s['binding']={'two-sided-long-edge':'long','two-sided-short-edge':'short','one-sided':'none'}[opts['sides']]
    if 'copies' in opts:s['copies']=opts['copies']
    if 'multiple-document-handling' in opts:s['collate']='no' if 'uncollated' in opts['multiple-document-handling'] else 'yes'
    if 'page-ranges' in opts:s['pages']=opts['page-ranges'].strip('[]')
    if opts.get('number-up') in ('2','4','6','9','16'):s.update(layout='nup',nup=opts['number-up'])
    if 'Duplex' in opts:
        s['binding']={'None':'none','DuplexNoTumble':'long','DuplexTumble':'short'}.get(opts['Duplex'],s['binding'])
    explicit_binding = opts.get('HPDuplexMode')
    if explicit_binding in dict(BINDING_CHOICES):
        s['binding'] = explicit_binding
    if opts.get('cupsPrintQuality') in ('Normal','High'):s['dpi']='1200' if opts['cupsPrintQuality']=='High' else '600'
    if opts.get('Resolution','').startswith('1200'):s['dpi']='1200'
    if opts.get('PageSize') in PAPERS:s['paper']=opts['PageSize']
    if opts.get('MediaType') in ('OFF','NORMAL','THICK','THIN','BOND','COLOR','CARD','LABEL','ENV','USED','RECYCLED'):s['media']=opts['MediaType']
    if opts.get('InputSlot') in ('Upper','Auto'):s['tray']=opts['InputSlot']
    exposed={'HPOut':'out','HPReload':'reload','HPHead':'head','HPEcon':'econ','HPEdge':'edge','HPText':'text','HPBlack':'black','HPLayout':'layout','HPNup':'nup','HPBorder':'border','HPSubset':'subset','HPReverse':'reverse','HPSkip':'skip'}
    for k,target in exposed.items():
        if k in opts:s[target]=opts[k]
    return s

def encode(doc,s,directory,number,manual=None):
    pdf=directory/f'pass-{number}.pdf';wire=directory/f'pass-{number}.spl'
    doc.save(pdf,garbage=2,deflate=True)
    profile=directory/'renderer.ppd'
    description=ppd(True,dimensions(s))
    if manual is not None:
        sheets, second = manual.counts
        if sheets != second or (manual.pages_per_copy + 1)//2 != sheets:
            raise ValueError('两趟的进纸数与作业页数不一致。')
        binding={'long':'LONGEDGE','short':'SHORTEDGE'}[manual.binding]
        description += (f'*HPManualBinding: "{binding}"\n'
                        f'*HPFirstPassSheets: "{sheets}"\n'
                        f'*HPPagesPerCopy: "{manual.pages_per_copy}"\n')
    profile.write_text(description)
    env=dict(os.environ,PPD=str(profile),CONTENT_TYPE='application/pdf',FINAL_CONTENT_TYPE='application/vnd.cups-raster')
    page='UserSheet' if s['paper']=='Custom' else s['paper']
    opts=[f'PageSize={page}',f'cupsPrintQuality={"High" if s["dpi"]=="1200" else "Normal"}',f'MediaType={s["media"]}',
          f'InputSlot={s["tray"]}',f'EconoMode={"ON" if s["econ"]=="yes" else "OFF"}',f'EdgeEnhance={s["edge"]}',f'Altitude={s["altitude"]}','Duplex=None','sides=one-sided','print-scaling=none','copies=1','ColorModel=Gray']
    args=['/usr/sbin/cupsfilter','-p',str(profile),'-m','application/vnd.cups-raster']
    for item in opts: args.extend(['-o',item])
    with tempfile.TemporaryFile() as errors, wire.open('wb') as target:
        with subprocess.Popen(args+[str(pdf)],env=env,stdout=subprocess.PIPE,stderr=errors) as raster:
            try:
                native=subprocess.run([str(BUILD/'rastertoqpdl'),'1','local','HP115nw','1',' '.join(opts)],env=env,stdin=raster.stdout,stdout=target,stderr=errors)
            finally: raster.stdout.close()
            if raster.wait() or native.returncode:
                errors.seek(0);raise RuntimeError('生成 SPL3 数据失败。'+errors.read()[-850:].decode(errors='replace'))
    # A truncated CUPS stream can reach an otherwise successful downstream filter.
    with wire.open('rb') as f:
        head=f.read(16);f.seek(-10,2);tail=f.read()
    if not head.startswith(b'\x1b%-12345X') or tail!=b'\x09\x1b%-12345X': raise ValueError('打印数据不完整。')
    return wire


def encode_job(plan, settings, directory):
    if len(plan.passes) == 1:
        return encode(plan.passes[0], settings, directory, 'job')
    import pymupdf
    with pymupdf.open() as document:
        for side in plan.passes:
            document.insert_pdf(side)
        return encode(document, settings, directory, 'job', manual=plan)

def status(host):
    proc=subprocess.run(['/usr/bin/ipptool','-tv','-T','3',f'ipp://{host}/ipp/print','/usr/share/cups/ipptool/get-printer-attributes.test'],capture_output=True,text=True,timeout=6)
    def value(key):
        match=re.search(r'^\s*'+key+r' \([^\n]*?\) = (.*)$',proc.stdout,re.M)
        return match[1] if match else ''
    return dict(state=value('printer-state'),reason=value('printer-state-reasons'),toner=value('marker-levels'),model=value('printer-make-and-model'))

def counter(host):
    try:
        res=subprocess.run(['/usr/bin/snmpget','-m','','-v','1','-c','public','-r','0','-t','1','-Oqv',host,'.1.3.6.1.2.1.43.10.2.1.4.1.1'],capture_output=True,text=True,timeout=3)
        return int(res.stdout.strip()) if res.returncode==0 else None
    except (ValueError,subprocess.TimeoutExpired):return None

def send_spl(path,host,port=9100):
    # No automatic replay after a partial transfer.
    with socket.create_connection((host,port),timeout=10) as dest, path.open('rb') as src:
        # Large jobs can fill the TCP window while the user reloads paper.
        dest.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        dest.settimeout(None)
        while chunk:=src.read(256*1024): dest.sendall(chunk)
        dest.shutdown(socket.SHUT_WR)

@contextmanager
def progress(message,detail):
    import json
    proc=subprocess.Popen([str(BUILD/'Panel')],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL)
    proc.stdin.write(json.dumps(dict(title=message,detail=detail,buttons=['知道了'])).encode());proc.stdin.close()
    try: yield
    finally:
        if proc.poll() is None:proc.terminate()
        proc.wait()

class Cancelled(Exception):pass


def reload_tip(s, sheets):
    move = ('整叠原样移到下方进纸盘，保持纸边方向。' if s['out'] == s['reload']
            else '整叠左右翻面，保持朝机身的纸边，放入下方进纸盘。')
    face = '已印面朝下。' if s['reload'] == 'down' else '已印面朝上。'
    return (f'等打印机停下来，从上方取齐 {sheets} 张，包括空白张。'
            + move + face + '按打印机上的继续键，开始第二趟。')


def dispatch(wire, counts, s, send, report, confirm_output, meter, device_state,
             activity=progress, sleep=time.sleep, clock=time.monotonic):
    """Submit once. Status dialogs are informational; only the device releases pass two."""
    duplex = len(counts) == 2
    total = sum(counts)
    tip = reload_tip(s, counts[0]) if duplex else ''
    initial = meter()
    observed = 0
    stage = 0
    last_change = clock()
    with ThreadPoolExecutor(max_workers=1) as pool, ExitStack() as views:
        views.enter_context(activity('正在打印第一趟' if duplex else '正在打印',
                                     f'本趟 {counts[0]} 张。' + tip))
        transfer = pool.submit(send, wire)
        while True:
            delivered = transfer.done()
            if delivered:
                transfer.result()  # A partial/failed transmission is never retried.
            current = meter()
            trackable = current is not None and initial is not None and current >= initial
            delta = max(0, current - initial) if trackable else 0
            if delta > observed:
                observed = delta
                last_change = clock()
            if delivered and delta >= total:
                break
            if duplex and stage < 2:
                if delta > counts[0]:
                    views.close()
                    views.enter_context(activity('正在打印第二趟', f'第二趟 {counts[1]} 张。'))
                    stage = 2
                elif stage == 0:
                    reason = device_state() if delta < counts[0] else ''
                    if delta >= counts[0] or 'manual-duplex' in reason or 'duplex-manual' in reason:
                        views.close()
                        views.enter_context(activity('等待在打印机上换纸', tip))
                        stage = 1
                        last_change = clock()
            # Keep the hardware job active even without reliable sheet telemetry.
            # The fallback acknowledges an observed outcome, never submits another job.
            if delivered and (not trackable or clock() - last_change >= 120):
                views.close()
                detail = tip + '请在全部成品出齐后确认。如需中止，请先在打印机上取消。'
                if not confirm_output('确认出纸结果', detail):
                    raise Cancelled()
                break
            sleep(1)
    result = (f'共用 {counts[0]} 张纸，每趟 {counts[0]} 张。' if duplex
              else f'共 {counts[0]} 张。')
    report('打印完成', result)
