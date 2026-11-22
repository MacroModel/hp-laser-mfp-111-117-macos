#!/usr/bin/python3
"""Install the user-session printer and atomically repoint the existing HP queue."""
import argparse,datetime,json,os,plistlib,re,shlex,shutil,subprocess,sys,tempfile,time
from urllib.parse import unquote
from pathlib import Path
ROOT=Path(__file__).resolve().parent
BASE=Path.home()/'Library/Application Support/HP115nw Studio'
APP_NAME = 'HP Laser MFP 111-117 Studio'
APP_ID = 'com.macromodel.hp115nw.control'
APP = Path.home() / 'Applications' / f'{APP_NAME}.app'
LEGACY_APP = APP.with_name('HP 115nw Studio.app')
LABEL='com.macromodel.hp115nw.studio'
AGENT=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')
PYTHON=Path('/opt/homebrew/bin/python3.13')
PORT=18615;URI='ipp://127.0.0.1:18615/ipp/print'
def run(*args,**kwargs):return subprocess.run([str(a) for a in args],check=True,text=True,capture_output=True,**kwargs).stdout
def put(path,data,mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:f.write(data)
    os.chmod(f.name,mode);os.replace(f.name,path)
def save(path,data):put(path,json.dumps(data,ensure_ascii=False,indent=2).encode())
def load(path):return json.loads(path.read_text())
def dest(queue):return run('/usr/bin/lpstat','-v',queue).strip().split(': ',1)[1]
def idle(queue):
    active=run('/usr/bin/lpstat','-W','not-completed','-o',queue)
    if active.strip():raise ValueError('队列仍有活动或保留作业；为保留这些作业，切换已停止。')
def installed():
    return dict(re.findall(r'^device for ([^:]+): (.+)$',run('/usr/bin/lpstat','-v'),re.M))
def studio_name(uri):
    service=unquote(uri.split('://',1)[1].split('._ipp',1)[0])
    return re.sub(r'[^A-Za-z0-9]+','_',service)

def find_device():
    known=installed()
    saved=BASE/'backup/queue.json'
    device=BASE/'device.json'
    if saved.is_file() and device.is_file():
        previous=load(saved)
        if known.get(previous['queue'])==previous['uri']:
            return previous['queue'],load(device)['host']
    choices=[(q,uri) for q,uri in known.items() if uri.startswith('dnssd://')
             and '115nw' in unquote(uri).lower() and '._ipp._tcp' in uri]
    if len(choices)!=1:
        raise ValueError('请在系统设置中添加 HP 115nw 的 AirPrint 队列。如果有多台，请按 README 指定队列和主机名。')
    name,uri=choices[0]
    service=unquote(uri.split('://',1)[1].split('._ipp',1)[0])
    matches=run('/usr/bin/ippfind','-T','3','_ipp._tcp.local.','--name','^'+re.escape(service)+'$',
                '--exec','/bin/echo','{service_hostname}',';').splitlines()
    if len(set(matches))!=1:raise ValueError('无法确定打印机主机名，请按 README 指定地址。')
    return name,matches[0].rstrip('.')
def menu_queue(before,after):
    cfg=Path.home()/'.config/manual-duplex/config'
    if cfg.is_file() and ('PRINTER='+before+'\n') in cfg.read_text():
        txt=cfg.read_text().replace('PRINTER='+before+'\n','PRINTER='+after+'\n')
        put(cfg,txt.encode(),0o600)
def ppd_for(queue):return Path('/etc/cups/ppd')/(queue+'.ppd')
def check_local():
    log=run('/usr/bin/ipptool','-tv','-T','2',URI,'/usr/share/cups/ipptool/get-printer-attributes.test')
    if 'HP Laser MFP 115nw SPL3 for Mac' not in log or '600dpi,1200dpi' not in log:raise ValueError('本机打印服务能力检查失败。')
def switch(queue,uri,ppd,label):
    run('/usr/sbin/lpadmin','-p',queue,'-D',label,'-v',uri,'-P',ppd,'-o','printer-is-shared=false','-o','printer-error-policy=abort-job')
    if dest(queue)!=uri:raise ValueError('队列验证失败。')
def sync_old_menu(queue):
    engine=Path.home()/'.local/bin/manual-duplex'
    if not engine.is_file():return
    old=engine.read_bytes()
    if b'HP115NW_STUDIO_ROUTE' in old:
        old=(BASE/'backup/manual-duplex').read_bytes()
    if b'run_duplex' not in old or b'manual-duplex' not in old:return
    backup=BASE/'backup/manual-duplex'
    if not backup.exists():put(backup,old,0o700)
    lines=old.splitlines(keepends=True)
    # Keep this installed PDF Service launcher (it owns the print-panel sandbox hand-off).
    # Verify its configured queue and the actual URI; any other queue stays on its original path.
    snippet='''
# HP115NW_STUDIO_ROUTE
if [[ "$#" -ge 3 && -f "$3" && -x "$HOME/Library/Application Support/HP115nw Studio/current/run" &&
      -r "$HOME/.config/manual-duplex/config" ]] &&
   /usr/bin/grep -Fqx 'PRINTER=QUEUE' "$HOME/.config/manual-duplex/config" &&
   /usr/bin/lpstat -v 'QUEUE' 2>/dev/null | /usr/bin/grep -Fq 'URI'; then
  "$HOME/Library/Application Support/HP115nw Studio/current/run" pdf-service "$1" "$2" "$3"
  result=$?
  case "$3" in "$HOME/.config/manual-duplex/jobs/"*) /bin/rm -f "$3" ;; esac
  exit "$result"
fi
'''.replace('QUEUE',queue).replace('URI',URI)
    put(engine,lines[0]+snippet.encode()+b''.join(lines[1:]),0o755)

def is_studio_app(path):
    if path.is_symlink():
        return False
    try:
        info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
    except (OSError, ValueError):
        return False
    return isinstance(info, dict) and info.get('CFBundleIdentifier') == APP_ID

def install_studio_app(launcher):
    APP.parent.mkdir(parents=True, exist_ok=True)
    info = dict(
        CFBundleIdentifier=APP_ID,
        CFBundleName=APP_NAME,
        CFBundleDisplayName=APP_NAME,
        CFBundleExecutable='HP115nw',
        CFBundlePackageType='APPL',
        CFBundleShortVersionString='1.1.1',
        CFBundleVersion='3',
        LSUIElement=True,
    )
    with tempfile.TemporaryDirectory(prefix='.hp-studio-', dir=APP.parent) as directory:
        staged = Path(directory) / APP.name
        put(staged / 'Contents/MacOS/HP115nw', launcher.read_bytes(), 0o755)
        put(staged / 'Contents/Info.plist', plistlib.dumps(info), 0o644)
        run('/usr/bin/codesign', '--force', '--sign', '-', staged)
        run('/usr/bin/codesign', '--verify', '--strict', staged)

        previous = Path(directory) / 'previous'
        if APP.exists():
            APP.rename(previous)
        try:
            staged.rename(APP)
        except OSError:
            if previous.exists():
                previous.rename(APP)
            raise

def install(args):
    if not PYTHON.exists():raise ValueError('此 Apple Silicon 版本需要 Homebrew Python 3.13。')
    if os.path.lexists(APP) and not is_studio_app(APP):
        raise ValueError(f'应用程序目录已有名为 {APP.name} 的其他项目。')
    queue=args.queue
    if not re.fullmatch(r'[a-zA-Z0-9._-]+',queue) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*',args.host):raise ValueError('队列名或主机名无效。')
    known=installed()
    existing=BASE/'backup/queue.json'
    if queue in known:
        original=known[queue];idle(queue)
    elif existing.exists() and load(existing)['queue']==queue and known.get(studio_name(load(existing)['uri']))==URI:
        original=load(existing)['uri']
    else:raise ValueError('找不到指定的打印队列。')
    if original!=URI and not (original.startswith('dnssd://') and '115nw' in original.lower() and '._ipp' in original.lower()):raise ValueError('目标队列与 115nw AirPrint 不匹配。')
    backup=BASE/'backup';backup.mkdir(mode=0o700,parents=True,exist_ok=True)
    meta=backup/'queue.json'
    if not meta.exists():
        if original==URI:raise ValueError('已有本机队列缺少原配置备份。')
        details=run('/usr/bin/lpstat','-l','-p',queue)
        name=re.search(r'^\s*Description: (.*)$',details,re.M)
        state=run('/usr/bin/lpoptions','-p',queue)
        save(meta,dict(queue=queue,uri=original,label=name[1] if name else 'HP Laser MFP 115nw',shared='printer-is-shared=true' in state,
                       policy=(re.search(r'printer-error-policy=(\S+)',state) or [None,'retry-job'])[1]))
        shutil.copyfile(ppd_for(queue),backup/'airprint.ppd')
    elif load(meta)['queue']!=queue:raise ValueError('备份属于另一台打印机。')
    original_queue=queue
    identity=load(meta)
    queue=studio_name(identity['uri'])
    current=installed().get(queue)
    if current and current not in (identity['uri'],URI):
        raise ValueError('同名队列使用了另一台设备。')
    if current:idle(queue)
    if current==URI:
        # CUPS can finish delivering a job while the signed-in user's printer
        # service is still waiting for the physical reload or an open dialog.
        proc=subprocess.run(['/usr/bin/ipptool','-tv','-T','2',URI,
                             '/usr/share/cups/ipptool/get-printer-attributes.test'],text=True,capture_output=True)
        count=re.search(r'queued-job-count \(integer\) = (\d+)',proc.stdout)
        if count and int(count[1]):raise ValueError('打印服务还有进行中的作业，请在出纸结束后更新。')
    ver=BASE/'versions'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')
    ver.mkdir(parents=True,mode=0o700);(ver/'build').mkdir()
    shutil.copytree(ROOT/'src',ver/'src',ignore=shutil.ignore_patterns('__pycache__'))
    for n in ('Panel','Launcher','rastertoqpdl','ipp-bridge','queue.ppd','renderer.ppd','media.json'):
        shutil.copy2(ROOT/'build'/n,ver/'build'/n)
    shutil.copy2(ROOT/'run',ver/'run')
    if (ROOT/'build/lib').is_dir():
        shutil.copytree(ROOT/'build/lib',ver/'build/lib',ignore=shutil.ignore_patterns('__pycache__'))
    else:
        run(PYTHON,'-m','pip','install','--no-index','--no-compile','--find-links',ROOT/'wheels','--target',ver/'build/lib','PyMuPDF==1.27.2.3','pypdf==6.21.4','Pillow==12.2.0')
    prev=BASE/'current';old=prev.resolve() if prev.exists() else None
    link=BASE/'link.new';link.unlink(missing_ok=True);link.symlink_to(ver);os.replace(link,prev)
    save(BASE/'device.json',dict(host=args.host,queue=queue))
    if not (BASE/'preferences.json').exists():
        defaults={};cal=Path.home()/'.config/manual-duplex/config'
        if cal.is_file():
            values=dict(re.findall(r'^(BIND_EDGE|OUTPUT_FACE|RELOAD_FACE|OUTPUT_HEAD)=(\w+)$',cal.read_text(),re.M))
            for key,value in [('out','OUTPUT_FACE'),('reload','RELOAD_FACE'),('head','OUTPUT_HEAD')]:
                if value in values:defaults[key]=values[value]
            save(BASE/'duplex-calibration.json',{'binding':values.get('BIND_EDGE','long')})
        save(BASE/'preferences.json',defaults)
    uid=os.getuid();gui=f'gui/{uid}';service=gui+'/'+LABEL
    subprocess.run(['/bin/launchctl','bootout',service],capture_output=True)
    spool=BASE/'spool';spool.mkdir(exist_ok=True,mode=0o700)
    pl=dict(Label=LABEL,ProgramArguments=[str(prev/'build/ipp-bridge'),'-spool',str(spool),'-worker',str(prev/'run'),'-queue',queue,'-media',str(prev/'build/media.json')],RunAtLoad=True,KeepAlive=True,LimitLoadToSessionType='Aqua',Umask=0o077,StandardErrorPath=str(BASE/'service.log'),ProcessType='Interactive')
    put(AGENT,plistlib.dumps(pl))
    run('/bin/launchctl','bootstrap',gui,AGENT)
    try:
        for i in range(24):
            try:check_local();break
            except Exception:
                if i==23:raise
                time.sleep(.25)
        install_studio_app(ver/'build/Launcher')
        if queue in installed():idle(queue)
        switch(queue,URI,ver/'build/queue.ppd','HP Laser MFP 111-117')
        options=run('/usr/bin/lpoptions','-p',queue,'-l')
        if 'DuplexTumble' not in options or 'High' not in options:raise ValueError('新队列的双面或质量选项缺失。')
        run('/usr/sbin/cupsaccept',queue);run('/usr/sbin/cupsenable',queue)
        run('/usr/sbin/lpadmin','-d',queue);run('/usr/bin/lpoptions','-d',queue)
        menu_queue(original_queue,queue)
        sync_old_menu(queue)
        if original_queue!=queue:
            live=installed().get(original_queue)
            if live not in (None,identity['uri'],URI):raise ValueError('原队列已指向其他设备。')
            if live!=identity['uri']:
                if live:idle(original_queue)
                switch(original_queue,identity['uri'],backup/'airprint.ppd',identity['label'])
                run('/usr/sbin/lpadmin','-p',original_queue,'-o','printer-is-shared='+str(identity['shared']).lower(),'-o','printer-error-policy='+identity['policy'])
                run('/usr/sbin/cupsaccept',original_queue);run('/usr/sbin/cupsenable',original_queue)
        selection=backup/'last-printer.json'
        if not selection.exists():
            pref=subprocess.run(['/usr/bin/defaults','read','org.cups.PrintingPrefs','UseLastPrinter'],text=True,capture_output=True)
            save(selection,{'value':pref.stdout.strip() if pref.returncode==0 else None})
        run('/usr/bin/defaults','write','org.cups.PrintingPrefs','UseLastPrinter','-bool','false')
        run('/usr/sbin/lpadmin','-d',queue);run('/usr/bin/lpoptions','-d',queue)
    except BaseException:
        if installed().get(queue)==URI:restore_queue()
        if old:link.symlink_to(old);os.replace(link,prev)
        raise
    if is_studio_app(LEGACY_APP):
        archive = Path(tempfile.mkdtemp(prefix='legacy-studio-', dir=backup))
        LEGACY_APP.rename(archive / 'launcher')
    print('已安装。',queue,URI,APP)

def legacy_menu(retire):
    home=Path.home()
    paths=[home/'Library/PDF Services/Print Two-Sided',home/'Applications/Two-Sided Printing.app',
           home/'.local/bin/manual-duplex',home/'.local/bin/manual-duplex-plan']
    backup=BASE/'backup/Print Two-Sided'
    if retire:
        device=load(BASE/'device.json')
        if dest(device['queue'])!=URI:raise ValueError('请先启用 HP Laser MFP 111-117 队列。')
        idle(device['queue'])
        if paths[0].exists() and b'PDF Service entry point for manual-duplex' not in paths[0].read_bytes():
            raise ValueError('发现了同名的其他 PDF 菜单，已保留。')
        if paths[1].exists() and plistlib.loads((paths[1]/'Contents/Info.plist').read_bytes()).get('CFBundleIdentifier')!='com.manualduplex.twosided':
            raise ValueError('发现了同名的其他应用，已保留。')
        backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    elif not backup.is_dir():
        return
    moves=[]
    for live in paths:
        saved=backup/live.name
        source,target=(live,saved) if retire else (saved,live)
        if not source.exists():continue
        if target.exists():raise ValueError(f'请先处理同名文件 {target}。')
        moves.append((source,target))
    for source,target in moves:
        target.parent.mkdir(parents=True,exist_ok=True)
        source.rename(target)
    print('已移除旧 PDF 菜单和助手。' if retire else '已恢复旧 PDF 菜单和助手。')

def restore_queue():
    b=BASE/'backup';m=load(b/'queue.json');canonical=studio_name(m['uri'])
    if canonical in installed():idle(canonical)
    if m['queue'] in installed():idle(m['queue'])
    switch(m['queue'],m['uri'],b/'airprint.ppd',m['label'])
    run('/usr/sbin/lpadmin','-p',m['queue'],'-o','printer-is-shared='+str(m['shared']).lower(),'-o','printer-error-policy='+m['policy'])
    run('/usr/sbin/cupsaccept',m['queue']);run('/usr/sbin/cupsenable',m['queue'])
    run('/usr/sbin/lpadmin','-d',m['queue']);run('/usr/bin/lpoptions','-d',m['queue'])
    menu_queue(canonical,m['queue'])
    if canonical!=m['queue'] and installed().get(canonical)==URI:
        run('/usr/sbin/lpadmin','-x',canonical)
def restore():
    legacy_menu(False)
    restore_queue()
    last=BASE/'backup/last-printer.json'
    if last.exists():
        val=load(last)['value']
        if val is None:subprocess.run(['/usr/bin/defaults','delete','org.cups.PrintingPrefs','UseLastPrinter'],capture_output=True)
        else:run('/usr/bin/defaults','write','org.cups.PrintingPrefs','UseLastPrinter','-bool','true' if val in ('1','true','YES') else 'false')
    backup=BASE/'backup/manual-duplex';eng=Path.home()/'.local/bin/manual-duplex'
    if backup.exists() and eng.is_file() and b'HP115NW_STUDIO_ROUTE' in eng.read_bytes():put(eng,backup.read_bytes(),0o755)
    subprocess.run(['/bin/launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],capture_output=True)
    AGENT.unlink(missing_ok=True);print('已恢复原队列与双面打印入口。')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--queue');p.add_argument('--host')
    op=p.add_mutually_exclusive_group()
    op.add_argument('--install',action='store_true')
    op.add_argument('--restore',action='store_true')
    op.add_argument('--remove-old-menu',action='store_true')
    op.add_argument('--restore-old-menu',action='store_true')
    args=p.parse_args()
    try:
        if args.restore:restore()
        elif args.remove_old_menu:legacy_menu(True)
        elif args.restore_old_menu:legacy_menu(False)
        elif args.install:
            args.queue,args.host=find_device()
            install(args)
        elif args.host and args.queue:install(args)
        else:p.error('需要 --host 和 --queue，或 --restore')
    except subprocess.CalledProcessError as e:sys.exit(e.stderr or str(e))
    except Exception as e:sys.exit(str(e))
