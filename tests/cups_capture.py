"""Exercise the normal macOS queue with a file-only worker and localhost transport."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import time
import pymupdf
from streams import read

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    return subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout


def capture(sample, folder):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    queue = f'HP115nw_Offline_{os.getpid()}'
    uri = f'ipp://127.0.0.1:{port}/ipp/print'
    assert queue not in run('/usr/bin/lpstat','-v')
    env = dict(os.environ, HP115NW_CAPTURE_DIR=str(folder))
    proc = subprocess.Popen([ROOT/'build/ipp-bridge','-port',str(port),'-queue',queue,
                            '-spool',folder/'ipp-spool','-worker',ROOT/'tests/capture-worker',
                            '-media',ROOT/'build/media.json'],env=env,stdout=subprocess.DEVNULL)
    registered = False
    try:
        for _ in range(100):
            if proc.poll() is not None:raise RuntimeError('capture service terminated')
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.2):break
            except OSError:time.sleep(.05)
        else:raise TimeoutError('capture service unavailable')
        run('/usr/sbin/lpadmin','-p',queue,'-E','-D','HP 115nw offline capture','-v',uri,
            '-P',str(ROOT/'build/queue.ppd'),'-o','printer-is-shared=false')
        registered = True
        rows = []
        cases = [
            ('long', 4, 'DuplexNoTumble', 'auto', 'Normal', 600),
            ('short', 3, 'DuplexTumble', 'auto', 'High', 1200),
            ('long', 4, 'DuplexTumble', 'long', 'Normal', 600),
            ('short', 3, 'DuplexNoTumble', 'short', 'High', 1200),
        ]
        for label, size, system, manual, quality, resolution in cases:
            name = f'{label}-{manual}'
            title = f'HP115nw offline {name}'
            pdf = folder / (name+'.pdf')
            with pymupdf.open(sample) as src, pymupdf.open() as selected:
                selected.insert_pdf(src,from_page=0,to_page=size-1)
                selected.save(pdf)
            assert run('/usr/bin/lpstat','-v',queue).split(': ',1)[1].strip() == uri
            run('/usr/bin/lp','-d',queue,'-t',title,'-o','PageSize=Letter',
                '-o','Duplex='+system,'-o','HPDuplexMode='+manual,'-o','cupsPrintQuality='+quality,str(pdf))
            deadline = time.monotonic()+100
            while time.monotonic() < deadline:
                matches=[p for p in (folder/'ipp-spool').glob('*.json')
                         if json.loads(p.read_text())['Options'].get('job-name')==title]
                if matches:
                    job=json.loads(matches[0].read_text())
                    if job['State'] in (7,8):raise AssertionError(job['Reason'])
                    if job['State']==9:break
                time.sleep(.2)
            else: raise TimeoutError(label)
            outputs=list((folder/str(job['ID'])).glob('*.spl'))
            assert len(outputs)==1
            ticket,pages=read(outputs[0].read_bytes())
            assert (ticket['DUPLEX'],ticket['BINDING'],ticket['PAGESPERCOPIES'])==('MANUAL',label.upper()+'EDGE',str(size))
            assert int(ticket['RESOLUTION'])==resolution and len(pages)==4
            assert all(p['dpi']==(resolution,resolution) for p in pages)
            assert [p['header'][12] for p in pages]==[1,1,0,0]
            assert [bool(p['bands']) for p in pages]==[size==4,True,True,True]
            rows.append(dict(binding=label, system_option=system, manual_option=manual,
                             pages=size, dpi=resolution, jobs=len(outputs), sheets_per_pass=[2,2]))
        return rows
    finally:
        try:
            if registered: run('/usr/sbin/lpadmin','-x',queue)
        finally:
            proc.terminate()
            proc.wait(5)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('sample',type=Path)
    parser.add_argument('result',type=Path)
    args=parser.parse_args()
    with TemporaryDirectory() as tmp:
        rows=capture(args.sample,Path(tmp))
    args.result.write_text(json.dumps(rows,indent=2))
    print(rows)
