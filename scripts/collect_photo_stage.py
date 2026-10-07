"""Download a private review pool, never activate it as gallery or evaluation gold.

Only public museum endpoints; no uploads or model calls. Cached responses are
reused. Individual failures remain in the collection log; no automatic retries.
"""
import hashlib
import io
import json
from pathlib import Path
import time
import httpx
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/private/photo-stage-30-v1'
UA = 'MUSEResearch/0.1 (https://github.com/mitsuki3693/museum_agent; local reference research)'


def fetch(client, url, path, params=None):
    if not path.exists():
        response = client.get(url, params=params)
        response.raise_for_status()
        path.write_bytes(response.content)
        time.sleep(.2)
    return path.read_bytes()


def image_record(raw, path):
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        rgb = ImageOps.exif_transpose(im).convert('RGB')
        return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(raw).hexdigest(),
                    pixel_sha256=hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest(), size=list(rgb.size))


def sheet(rows, name):
    canvas = Image.new('RGB', (1200, ((len(rows)+4)//5)*235), 'white')
    draw = ImageDraw.Draw(canvas)
    for n, row in enumerate(rows):
        with Image.open(ROOT/row['path']) as im:
            thumb = ImageOps.contain(im.convert('RGB'), (230, 195))
        x, y = n%5*240, n//5*235
        canvas.paste(thumb, (x+(240-thumb.width)//2, y))
        draw.text((x+5, y+198), row['id'], fill='black')
        draw.text((x+5, y+214), (row.get('source_id') or row.get('accession',''))[:35], fill='black')
    canvas.save(OUT/name)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'manifest.json').exists():
        raise FileExistsError('This selection is frozen; preserve the source pool')
    p = ROOT/'data/private'
    plan = json.loads((ROOT/'eval/private/photo-scale-v1.collection-plan.json').read_bytes())
    refs = json.loads((p/'va-pilot-100-v1-references.json').read_bytes())['references']
    reference_assets = {Path(r['path']).stem for r in refs}
    reference_hashes = {r.get('sha256') for r in refs}
    work_ids = [r['target_source_id'] for r in plan['cases']
                if r['category']=='clear' and r['split']=='development' and r['target_source_id'].startswith('va-o')]
    rows, errors = [], []
    with httpx.Client(timeout=40, follow_redirects=True, headers={'User-Agent': UA}) as client:
        for sid in work_ids:
            oid = sid[3:].upper()
            record_path = p/'va-pilot-100-v1/records'/f'{oid}.json'
            record = json.loads(record_path.read_bytes())['record']
            for asset in record['images']:
                if asset in reference_assets: continue
                url = f'https://framemark.vam.ac.uk/collections/{asset}/full/!1000,1000/0/default.jpg'
                path = OUT/f'va-{asset}.jpg'
                try:
                    info = image_record(fetch(client, url, path), path)
                    if info['sha256'] in reference_hashes: raise ValueError('Exact gallery duplicate')
                    rows.append(dict(id=f'va-{asset}', source_id=sid, accession=record['accessionNumber'],
                        title=record['objectType'], source_url=f'https://collections.vam.ac.uk/item/{oid}/',
                        image_url=url, image_id=asset, record_snapshot=str(record_path.relative_to(ROOT)),
                        author='Victoria and Albert Museum', license='V&A copyright; local study only',
                        terms_url='https://www.vam.ac.uk/info/va-websites-terms-conditions',
                        identity_reviewed=False, human_reviewed=False, **info))
                    print('VA', asset, flush=True)
                except Exception as exc:
                    errors.append(dict(id=asset, error=type(exc).__name__))
        for query in ('Delft', 'bust', 'blue white vase'):
            snapshot = OUT/('cleveland-'+query.replace(' ','-')+'.json')
            try:
                response = json.loads(fetch(client, 'https://openaccess-api.clevelandart.org/api/artworks/', snapshot,
                    {'q':query, 'has_image':1, 'cc0':1, 'limit':12}))
                for record in response['data']:
                    rid = 'cma-'+str(record['id'])
                    if any(r['id']==rid for r in rows) or record.get('accession_number')=='2024.27': continue
                    image_url = record.get('images',{}).get('web',{}).get('url')
                    if not image_url: continue
                    info = image_record(fetch(client,image_url,OUT/(rid+'.jpg')),OUT/(rid+'.jpg'))
                    rows.append(dict(id=rid, source_id=None, external_id=record['id'],
                        accession=record.get('accession_number',''), title=record['title'],
                        source_url=record.get('url') or 'https://www.clevelandart.org/art/'+record.get('accession_number',''),
                        image_url=image_url, author='Cleveland Museum of Art', license=record.get('share_license_status'),
                        record_snapshot=str(snapshot.relative_to(ROOT)), query=query,
                        identity_reviewed=False, human_reviewed=False, **info))
                    print('CMA', record['id'], flush=True)
            except Exception as exc:
                errors.append(dict(id=query, error=type(exc).__name__))
    (OUT/'pool.json').write_text(json.dumps(dict(status='unreviewed_pool',rows=rows,errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
    for start in range(0,len(rows),20): sheet(rows[start:start+20],f'pool-{start//20+1}.jpg')
    print(json.dumps(dict(downloaded=len(rows),failed=len(errors),gold_ready=False)))


if __name__=='__main__': main()
