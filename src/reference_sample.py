"""Four vector pages with asymmetric registration and line-weight targets."""
from pathlib import Path
import sys
import pymupdf as pdf
def sample(path, count=4):
    doc=pdf.open()
    for i in range(1,count+1):
        p=doc.new_page(width=210*72/25.4,height=297*72/25.4)
        p.insert_text((46,55),'TOP  /  HP 115nw',fontsize=15,fontname='hebo')
        p.insert_text((46,186),f'PAGE {i:02}',fontsize=69,fontname='hebo')
        p.draw_line((46,210),(545,210),width=.8)
        for j,lw in enumerate((.12,.24,.5,1)):
            y=271+j*31
            p.draw_line((150,y),(485,y),width=lw)
            p.insert_text((46,y+3),f'{lw:g} pt',fontsize=11)
        for j,v in enumerate((0,.2,.4,.6,.8,.95)):
            p.draw_rect(pdf.Rect(46+70*j,436,99+70*j,473),color=None,fill=(v,))
        for bit in range(6):
            if i&(1<<bit):
                p.draw_rect(pdf.Rect(46+32*bit,554,65+32*bit,580),color=None,fill=(0,))
        p.draw_rect(pdf.Rect(520,60,544,84),fill=(0,),color=None)
        p.draw_circle((56,745),9,color=(0,),width=1)
        p.insert_text((82,750),f'BOTTOM LEFT   /   {i} of {count}',fontsize=12)
    doc.save(path,deflate=True)
if __name__=='__main__': sample(Path(sys.argv[1]))
