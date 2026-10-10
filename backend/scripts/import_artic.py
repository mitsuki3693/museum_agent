"""Refresh a small official Art Institute corpus. CC0 metadata / CC-BY descriptions."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from types import SimpleNamespace
from app.museum.collection_images import image_availability

ROOT=Path(__file__).resolve().parents[2]
IDS=[27992,28560,111628,6565,80607,14620,20684,8624,16487,81558,117266,16568]
FIELDS={'title':'名称 Title','id':'藏品编号 Object ID','artist_display':'作者 Artist','date_display':'年代 Date',
        'medium_display':'材质 Medium','dimensions':'尺寸 Dimensions','place_of_origin':'创作地 Origin',
        'credit_line':'入藏信息 Credit line','main_reference_number':'馆藏编号 Accession number',
        'classification_title':'分类 Classification','department_title':'馆藏部门 Department','description':'馆方介绍 Description'}

def convert(payload):
    license_text=payload.get('info',{}).get('license_text','')
    if 'CC0' not in license_text or 'description' not in license_text:
        raise ValueError('License metadata changed; review official response before importing')
    result=[]
    for d in payload['data']:
        raw=json.dumps(d,ensure_ascii=False,sort_keys=True,indent=2)
        content='\n'.join(f'{label}: {BeautifulSoup(str(d[k]),"html.parser").get_text(" ",strip=True)}' for k,label in FIELDS.items() if d.get(k))
        result.append({'_id':f'artic-{d["id"]}','title':d['title'],'content':content,'fields':d,
            'source_url':f'https://www.artic.edu/artworks/{d["id"]}', 'api_url':f'https://api.artic.edu/api/v1/artworks/{d["id"]}',
            'license':'Metadata: CC0-1.0; description: CC-BY-4.0','status':'active',
            'attribution':'Art Institute of Chicago. Description converted from HTML to plain text; metadata labels added in Chinese.',
            'license_url':'https://creativecommons.org/licenses/by/4.0/',
            'fetched_at':datetime.now(timezone.utc).isoformat(),'source_updated_at':d.get('timestamp'),
            'source_hash':hashlib.sha256(raw.encode()).hexdigest()})
    if {r['fields']['id'] for r in result}!=set(IDS):
        raise ValueError('Incomplete response; existing corpus was preserved')
    return result

def main():
    with httpx.Client(timeout=45,follow_redirects=True) as client:
        response=client.get('https://api.artic.edu/api/v1/artworks',params={
            'ids':','.join(map(str,IDS)),'fields':','.join([*FIELDS,'timestamp','image_id','is_public_domain'])})
        response.raise_for_status()
        payload=response.json()
    rows=convert(payload)
    folder=ROOT/'data'
    (folder/'raw').mkdir(parents=True,exist_ok=True)
    (folder/'artic-download.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in rows:
        (folder/'raw'/f'{row["_id"]}.json').write_text(json.dumps(row['fields'],ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
    pending=folder/'corpus.pending.json'
    pending.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    pending.replace(folder/'corpus.json')
    # Text import is not image acquisition. Audit local display availability
    # explicitly; metadata access never implies permission to copy an image.
    images=[]
    for d in payload['data']:
        item={k:d.get(k) for k in ['id','title','image_id','is_public_domain']}
        sid=f"artic-{d['id']}"
        availability=image_availability(SimpleNamespace(museum_private_corpus=None),{'_id':sid},{sid:item})
        item.update(image_status=availability['image_status'],image_reason=availability['image_reason'])
        images.append(item)
    manifest=dict(data=images,checked_at=datetime.now(timezone.utc).isoformat(),source='https://api.artic.edu/api/v1/artworks')
    image_pending=folder/'sample-images.pending.json'
    image_pending.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    image_pending.replace(folder/'sample-images.json')
    print(f'Imported {len(rows)} text records. Existing evaluation must be re-reviewed after corpus updates.')

if __name__=='__main__':
    main()
