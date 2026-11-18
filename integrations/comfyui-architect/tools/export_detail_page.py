"""Deterministic offline PNG layout for an already completed owned six-job bundle."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

IDS=['hero','benefits','details','scene','specs','closing']
def main():
    p=argparse.ArgumentParser();p.add_argument('--directory',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise SystemExit('Do not overwrite existing page')
    bundle=json.loads((a.directory/'bundle.json').read_text(encoding='utf-8'));journal=json.loads((a.directory/'journal.json').read_text(encoding='utf-8'))
    if journal['bundle_sha256']!=bundle['bundle_sha256'] or [e['id'] for e in journal['entries']]!=IDS or any(e['status']!='done' for e in journal['entries']):raise SystemExit('Need matching six completed tasks')
    fontpath=Path('C:/Windows/Fonts/msyh.ttc')
    if not fontpath.is_file():raise SystemExit('Specify a verified CJK renderer on this host; no silent font substitution')
    titlefont=ImageFont.truetype(str(fontpath),28);copyfont=ImageFont.truetype(str(fontpath),19)
    page=Image.new('RGB',(768,6720),'white');draw=ImageDraw.Draw(page)
    for i,ident in enumerate(IDS):
        path=a.directory/(ident+'-output.png')
        if path.stat().st_size>20*1024*1024:raise SystemExit('Output too large')
        with Image.open(path) as image:
            if image.size!=(512,640):raise SystemExit('Wrong output dimensions')
            image.load();page.paste(image.convert('RGB').resize((768,960),Image.Resampling.LANCZOS),(0,i*1120+160))
        section=bundle['sections'][i];title=bundle['product_name']+' · '+section['title']
        if draw.textlength(title,font=titlefont)>720:raise SystemExit('Title too long; shorten product name')
        draw.text((24,i*1120+8),title,font=titlefont,fill='#111827');lineY=i*1120+50
        for text in section['copy']:
            lines=[];line=''
            for char in text:
                if draw.textlength(line+char,font=copyfont)>720 and line:lines.append(line);line=''
                line+=char
            if line:lines.append(line)
            for line in lines:
                if lineY>i*1120+130:raise SystemExit('Copy overflow; reduce facts, never truncate')
                draw.text((24,lineY),line,font=copyfont,fill='#111827');lineY+=22
    page.save(a.output);print('Exported deterministic 768x6720 PNG, no AI text rendering or API calls.')
if __name__=='__main__':main()
