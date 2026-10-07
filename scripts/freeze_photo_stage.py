"""Freeze the agent-reviewed 30-image development subset before algorithm runs.

This does not mark human review complete and does not fill the sealed holdout.
Selection is based on source metadata and visual inspection, not model scores.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageOps

ROOT=Path(__file__).resolve().parents[1]
VA={
    '2006BC6346': ('different_angle','Opposite view of the same decorated jug'),
    '2020MP1936': ('uncovered_detail','Underside with accession; no comparable underside reference'),
    '2017KD5955': ('different_angle','Rear torso view'),
    '2013GA5372': ('limited_angle','Stone sculpture rotated slightly; same photo campaign'),
    '2013GA5373': ('different_angle','Opposite face of stone sculpture'),
    '2013GA5374': ('different_angle','Another face of stone sculpture'),
    '2013GA5376': ('detail','Texture and joint detail photographed separately'),
    '2007BM5078': ('uncovered_detail','Plate underside; reference shows decorated front'),
    '2014HJ2789': ('different_angle','Side of glass sculpture'),
    '2014HJ2791': ('different_angle','Frontal circular face of glass sculpture'),
    '2014HJ2792': ('different_angle','Side and support of glass sculpture'),
    '2014HJ2793': ('different_angle','Opposite face of glass sculpture'),
    '2017KD6167': ('different_angle','Bust from side'),
    '2017KD6168': ('different_angle','Bust from rear'),
    '2017KD6169': ('different_angle','Bust from opposite side'),
    '2017KD5962': ('different_angle','Portrait bust with shifted angle and frame'),
    '2017KD6170': ('different_angle','Portrait bust profile'),
    '2017KD6171': ('uncovered_detail','Back of portrait bust; front reference lacks this surface'),
    '2013GP1500': ('different_angle','Figure on motorcycle from front, reference is side view'),
    '2017KD5965': ('different_angle','Portrait head profile, reference is frontal'),
}
CMA=['670310','154403','84460','125737','140244','299431','157139','110239','170811','147018']


def pixel_sha(path):
    with Image.open(path) as im: rgb=ImageOps.exif_transpose(im).convert('RGB')
    return hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()


def main():
    p=ROOT/'data/private';folder=p/'photo-stage-30-v1';out=folder/'manifest.json'
    if out.exists(): raise FileExistsError('Selection is immutable; preserve completed evidence')
    rows={r['id']:r for r in json.loads((folder/'pool.json').read_bytes())['rows']}
    gallery=p/'va-pilot-100-v1-references.json';refs=json.loads(gallery.read_bytes())['references']
    reference_pixels={pixel_sha(p/r['path']) for r in refs}
    corpus={r['_id']:r for r in json.loads((p/'va-pilot-100-v1-corpus.json').read_bytes())}
    plan=json.loads((ROOT/'eval/private/photo-scale-v1.collection-plan.json').read_bytes())
    held={c['target_source_id'] for c in plan['cases'] if c['split']=='holdout' and c.get('target_source_id')}
    cases=[];pixels=set()
    for key in ['va-'+i for i in VA]+['cma-'+i for i in CMA]:
        row=rows[key];actual=hashlib.sha256((ROOT/row['path']).read_bytes()).hexdigest()
        assert actual==row['sha256']
        pix=pixel_sha(ROOT/row['path']);assert pix not in reference_pixels and pix not in pixels;pixels.add(pix)
        expected=row.get('source_id')
        if expected:
            assert expected in corpus and expected not in held
            original=json.loads((ROOT/row['record_snapshot']).read_bytes())['record']
            assert row['image_id'] in original['images']
            assert original['accessionNumber']==corpus[expected]['fields']['accession_number']
            group,note=VA[row['image_id']]
        else:
            assert row['license']=='CC0'
            assert not any('clevelandart.org' in c.get('source_url','') for c in corpus.values())
            group,note='outside_lookalike','Different physical museum object documented by Cleveland accession; verify again after corpus changes'
        cases.append({**row,'expected_source_id':expected,'group':group,'review_note':note,
            'agent_reviewed':True,'human_reviewed':False,'reference_exact_duplicate':False})
    manifest=dict(version='photo-stage-30-v1',selection_frozen=True,human_reviewed=False,
        scope='Official independent assets and views; same photographic campaigns possible; development only, not blind or field evaluation',
        excluded_near_duplicates=['va-2019MC7501','va-2007BM1740','va-2013GP1498'],
        excluded_archive_pages=['va-2017KE1882'],
        gallery_sha256=hashlib.sha256(gallery.read_bytes()).hexdigest(),cases=cases)
    with out.open('x',encoding='utf-8') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(cases=len(cases),in_gallery=sum(bool(c['expected_source_id']) for c in cases),
        works=len({c['expected_source_id'] for c in cases if c['expected_source_id']}),groups=dict(Counter(c['group'] for c in cases)),human_reviewed=False)))


if __name__=='__main__':main()
