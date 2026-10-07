"""Offline patch correspondence diagnostics on the existing private 12-case pack.

No API calls or visitor uploads are persisted. Diagram files contain private source
images; all outputs stay in eval/private and must not be committed.
"""
import asyncio
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
from PIL import Image, ImageDraw
from app.museum.config import MuseumSettings
from app.museum.visual_index import MuseumVisualIndex
from app.museum.local_correspondence import LocalCorrespondence, VERSION
from app.museum.vision import prepare_image

ROOT=Path(__file__).resolve().parents[1]

def diagram(raw, reference, region, output):
    canvas=Image.new("RGB",(1000,660),"white");draw=ImageDraw.Draw(canvas)
    for x,data,label in [(0,raw,"Query"),(500,reference,"Selected reference")]:
        with Image.open(io.BytesIO(data)) as image:
            image=image.convert("RGB").resize((480,600))
            canvas.paste(image,(x+10,40))
        draw.text((x+10,12),label,fill="black")
    for i,p in enumerate(region["pairs"][::max(1,len(region["pairs"])//25)]):
        a=(10+int(p["query"][0]*480),40+int(p["query"][1]*600))
        b=(510+int(p["reference"][0]*480),40+int(p["reference"][1]*600))
        color=["red","cyan","yellow","lime"][i%4]
        draw.line([a,b],fill=color,width=1)
        for point in [a,b]:draw.ellipse((point[0]-3,point[1]-3,point[0]+3,point[1]+3),outline=color,width=2)
    canvas.save(output)

async def main(args):
    cfg=MuseumSettings();p=ROOT/'data/private'
    out=ROOT/'eval/private'/args.output
    if not out.resolve().is_relative_to((ROOT/'eval/private').resolve()):raise ValueError('Private output only')
    out.mkdir(parents=True,exist_ok=True)
    report_path=out/'offline.json'
    if report_path.exists():raise ValueError('Do not overwrite completed evidence')
    records={r['_id']:r for path in [cfg.museum_corpus,cfg.museum_private_corpus] for r in json.loads(path.read_bytes())}
    visual=MuseumVisualIndex(cfg.museum_visual_manifest,cfg.museum_visual_model,records,cache_dir=cfg.museum_visual_cache)
    await visual.start();local=LocalCorrespondence(visual,cfg.museum_visual_cache)
    pack=p/'MUSE-test-pack-12-20261006';cases=json.loads((pack/'manifest.json').read_bytes())
    report=dict(version=VERSION,index_hash=visual.index_hash,scope='Known developer images; whole image coverage is not foreground coverage or identity confidence',cases=[])
    rois=json.loads(Path(args.rois).read_bytes()) if args.rois else {}
    for number,case in enumerate(cases,1):
        if rois and str(number) not in rois:continue
        raw=(pack/case['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==case['sha256']
        clean=prepare_image(raw);hits=await visual.search(clean)
        roi=rois.get(str(number))
        selected,regions=await local.select(clean,hits,roi)
        report['cases'].append(dict(case=number,sha256=case['sha256'],manual_roi=roi,expected=case['expected_source_id'],hits=hits,selected=selected,regions=regions))
        for rank,(hit,region) in enumerate(zip(selected,regions),1):
            diagram(clean,visual.reference_image(hit),region,out/f'{number:02d}-candidate-{rank}.png')
        print(number,[(r['source_id'],r['match_count'],r['coverage'],r['reference_id']) for r in regions],flush=True)
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    report['cache']=local.cache_stats;report['complete']=True
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Finished offline diagnostics; reference-only cache:',local.cache_stats)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='partial-regions-v1')
    parser.add_argument('--rois',help='Private JSON mapping case number to manually reviewed normalized rectangle')
    sys.stdout.reconfigure(encoding='utf-8');asyncio.run(main(parser.parse_args()))
