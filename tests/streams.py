"""Structural QPDL and actual dot-map comparison for HP 111–117."""
import re,struct
from collections import Counter
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageChops, ImageOps
def read(data):
    start=re.search(rb'@PJL ENTER LANGUAGE\s*=\s*QPDL\r?\n',data)
    assert start and data.startswith(b'\x1b%-12345X') and data.endswith(b'\x09\x1b%-12345X')
    pjl={k.decode():v.decode() for k,v in re.findall(rb'@PJL SET ([A-Z0-9]+)\s*=\s*([^\r\n]*)',data[:start.end()])}
    pos=start.end();pages=[]
    while data[pos]!=9:
        h=data[pos:pos+17];assert h[0]==0 and h[14]==3
        p=dict(dpi=(h[16]*100,h[1]*100),code=h[4],geometry=struct.unpack('>HH',h[5:9]),header=h,bands=[],extras=[])
        pos+=17
        while data[pos]==12:
            b=data[pos:pos+11];n=int.from_bytes(b[7:11],'big');payload=data[pos+11:pos+11+n]
            assert len(payload)==n and int.from_bytes(payload[-4:],'big')==sum(payload[:-4])
            p['bands'].append((b[1],*struct.unpack('>HH',b[2:6]),b[6],payload[4:-4]))
            pos+=11+n
        while data[pos]==20:
            n=data[pos+3]+4;p['extras'].append(data[pos:pos+n]);pos+=n
        assert data[pos:pos+3]==b'\x01\x00\x01';pos+=3;pages.append(p)
    assert pos==len(data)-10
    return pjl,pages

def decoded(band):
    _,width,height,algo,payload=band
    destsize=width//8*height
    if algo==18:
        out=decode_lzw(payload,destsize)
    elif algo==17:
        out=decode_backrefs(payload,destsize)
    else:
        raise ValueError(f'Unsupported compression {algo:#x}')
    # QPDL stores groups of eight horizontal pixels in column-major order.
    plane=bytearray(destsize)
    for y in range(height):plane[y*width//8:(y+1)*width//8]=out[y::height]
    return Image.frombytes('1',(width,height),bytes(plane))

def decode_lzw(payload,size):
    encoded=payload[4:]
    assert len(encoded)==int.from_bytes(payload[:4],'little') and len(encoded)%3==0
    table=[bytes([i]) for i in range(256)]
    out=bytearray()
    previous=b''
    for i in range(0,len(encoded),3):
        a,b,c=encoded[i:i+3]
        for j,code in enumerate(((a<<4)|(b>>4),((b&15)<<8)|c)):
            if len(out)==size:
                assert code==0 and j==1 and i+3==len(encoded)
                continue
            assert code<len(table) or (code==len(table) and previous)
            entry=table[code] if code<len(table) else previous+previous[:1]
            if previous and len(table)<4096:table.append(previous+entry[:1])
            out.extend(entry)
            assert len(out)<=size
            previous=entry
    assert len(out)==size
    return out

def decode_backrefs(payload,destsize):
    literal=struct.unpack_from('<I',payload)[0]; offsets=struct.unpack_from('<64H',payload,4)
    pos=132+literal; out=bytearray(payload[132:pos])
    while pos<len(payload):
        code=payload[pos];pos+=1
        if code<128:
            n=code+1;out.extend(payload[pos:pos+n]);pos+=n
        else:
            key=payload[pos];pos+=1;n=((key & 192)<<1)+(code & 127)+3
            distance=offsets[key&63]
            assert distance>0 and distance<=len(out)
            start=len(out)-distance
            seq=out[start:start+min(n,distance)];out.extend((seq*((n+len(seq)-1)//len(seq)))[:n])
        assert len(out)<=destsize
    assert len(out)==destsize,(len(out),destsize)
    return out

def image(page,partial=False):
    w,h=page['geometry'];dpi=page['dpi'][0];w=round(w*dpi/300);h=round(h*dpi/300)
    out=Image.new('1',(w,h),1)
    for row,width,height,algo,payload in page['bands']:
        if algo not in (17,18) and partial:continue
        out.paste(decoded((row,width,height,algo,payload)),(0,row*height))
    return out
