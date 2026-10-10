"""Run after import: record missing local display assets without fetching them."""
import argparse
from collections import Counter
import json
from pathlib import Path

from app.museum.config import MuseumSettings,ROOT
from app.museum.collection_images import image_availability


def main(output):
    if not output.resolve().is_relative_to((ROOT/'eval/private').resolve()):raise ValueError('Private output required')
    cfg=MuseumSettings()
    records=json.loads(cfg.museum_corpus.read_bytes())
    if cfg.museum_private_corpus:records+=json.loads(cfg.museum_private_corpus.read_bytes())
    path=cfg.museum_corpus.parent/'sample-images.json'
    manifest={f"artic-{r['id']}":r for r in json.loads(path.read_text(encoding='utf-8-sig'))['data']} if path.exists() else {}
    rows=[dict(id=r['_id'],**image_availability(cfg,r,manifest)) for r in records if r.get('status')=='active']
    report=dict(rows=rows,counts=dict(Counter(r['image_reason'] or r['image_status'] for r in rows)))
    with output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['counts']))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
