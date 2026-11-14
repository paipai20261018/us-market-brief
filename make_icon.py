from PIL import Image, ImageDraw
import os
NAVY=(18,58,107); BLUE=(47,111,237); GREEN=(15,157,88); RED=(224,52,42)
def make(size):
    s=size/512.0
    im=Image.new('RGB',(size,size),NAVY)
    d=ImageDraw.Draw(im)
    # 斜向浅色高光条（右上）
    d.polygon([(int(320*s),0),(size,0),(size,int(190*s))],fill=(24,72,130))
    # 白色折线（近10日走势感）
    pts=[(0.13,0.66),(0.24,0.58),(0.35,0.62),(0.45,0.42),(0.56,0.50),
         (0.67,0.36),(0.78,0.40),(0.88,0.27)]
    xy=[(int(x*size),int(y*size)) for x,y in pts]
    d.line(xy,fill=(255,255,255),width=max(3,int(16*s)),joint='curve')
    # 末端强调点
    ex,ey=xy[-1]; r=int(20*s)
    d.ellipse([ex-r,ey-r,ex+r,ey+r],fill=(255,255,255))
    # 底部三色小柱（涨/跌/情绪）
    bw=int(52*s); gap=int(16*s); y0=int(0.80*size); y1=int(0.88*size)
    x0=int(0.13*size)
    for i,c in enumerate([GREEN,BLUE,RED]):
        x=x0+i*(bw+gap)
        d.rounded_rectangle([x,y0,x+bw,y1],radius=max(2,int(6*s)),fill=c)
    return im
os.makedirs('icons',exist_ok=True)
for sz in (192,512):
    make(sz).save(f'icons/icon-{sz}.png')
    print('icon',sz)
