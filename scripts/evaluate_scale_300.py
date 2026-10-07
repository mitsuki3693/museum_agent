"""Offline 100-vs-300 corpus check: text, photos and new accession lookup.

Requires local private snapshots; never activates them, calls VLM or changes Mongo.
Results are development retrieval evidence, not end-to-end identity/answer scores.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.visual_index import MuseumVisualIndex, DinoEncoder, MODEL_REVISION
from app.museum.vision import prepare_image
from app.storage.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'data/private'
OUT=ROOT/'eval/private/scale-300-v1.json'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def save(data):
    temp=OUT.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    # Sync/indexing software can briefly lock a Windows destination. Retry only
    # the atomic local rename, never model calls or a selected evaluation case.
    for attempt in range(6):
        try:
            temp.replace(OUT)
            return
        except PermissionError:
            if attempt == 5: raise
            time.sleep(.1*(attempt+1))


def position(ids, gold):
    return next((n for n,sid in enumerate(ids,1) if sid in gold),None)


async def main():
    import torch
    torch.set_num_threads(4)
    if OUT.exists(): raise FileExistsError('Preserve completed or partial evidence')
    manifest=ROOT/'eval/private/scale-300-text-v1.json'
    questions=json.loads(manifest.read_bytes());assert questions['frozen']
    assert questions['base_corpus_sha256']==sha(PRIVATE/'va-pilot-100-v1-corpus.json')
    cfg=MuseumSettings().model_copy(update={'deepseek_api_key':'','museum_storage':'memory'})
    assert cfg.museum_embedding=='local', 'Compare real multilingual vectors, not lexical fallback'
    indexes={};visuals={};report=dict(scope='Offline development retrieval, not VLM verification or live A/B',
        text_manifest_sha256=sha(manifest),embedding_model=cfg.museum_embedding_model,
        visual_revision=MODEL_REVISION,versions={},text=[],new_text=[],photos=[],new_accessions=[],complete=False)
    encoder=DinoEncoder(cfg.museum_visual_model)
    for size in [100,300]:
        corpus=PRIVATE/f'va-pilot-{size}-v1-corpus.json';gallery=PRIVATE/f'va-pilot-{size}-v1-references.json'
        settings=cfg.model_copy(update={'museum_private_corpus':corpus,'museum_visual_manifest':gallery})
        started=time.perf_counter();idx=MuseumIndex(settings,MemoryStore());await idx.start()
        text_ms=round((time.perf_counter()-started)*1000,2)
        for case in questions['cases']:
            if case['gold']: assert idx.records[case['gold'][0]]['source_hash']==case['source_hash']
        started=time.perf_counter();vis=MuseumVisualIndex(gallery,cfg.museum_visual_model,idx.records,
            encoder=encoder,cache_dir=cfg.museum_visual_cache,cache_namespace=MODEL_REVISION);await vis.start()
        report['versions'][str(size)]=dict(corpus_sha256=sha(corpus),gallery_sha256=sha(gallery),
            corpus_hash=idx.corpus_hash,index_hash=vis.index_hash,records=len(idx.records),visual_works=len(vis.references_by_source),
            reference_images=len(vis.images),text_start_ms=text_ms,visual_start_ms=round((time.perf_counter()-started)*1000,2),cache=vis.cache_stats)
        indexes[size]=idx;visuals[size]=vis;save(report);print('Loaded',size,flush=True)
    assert set(indexes[100].records)<=set(indexes[300].records)
    assert all(indexes[100].records[k]['source_hash']==indexes[300].records[k]['source_hash'] for k in indexes[100].records)
    for case in questions['cases']:
        row=dict(id=case['id'],query=case['query'],category=case['category'],gold=case['gold'],results={})
        for size in [100,300]:
            t=time.perf_counter();hits=await indexes[size].search(case['query']);ids=[r['_id'] for r in hits]
            row['results'][str(size)]=dict(ids=ids,rank=position(ids,case['gold']),ms=round((time.perf_counter()-t)*1000,2))
        report['text'].append(row);save(report)
    new_manifest=ROOT/'eval/private/scale-300-new-text-v1.json'
    new_questions=json.loads(new_manifest.read_bytes());assert new_questions['frozen']
    report['new_text_manifest_sha256']=sha(new_manifest)
    for case in new_questions['cases']:
        assert sha(ROOT/case['snapshot'])==case['snapshot_sha256']
        assert case['gold'][0] in indexes[300].records and case['gold'][0] not in indexes[100].records
        t=time.perf_counter();hits=await indexes[300].search(case['query']);ids=[h['_id'] for h in hits]
        report['new_text'].append(dict(id=case['id'],query=case['query'],category=case['category'],gold=case['gold'],
            ids=ids,rank=position(ids,case['gold']),ms=round((time.perf_counter()-t)*1000,2)))
    # New records: deterministic identifier wiring check, not natural-language comprehension.
    for sid in sorted(set(indexes[300].records)-set(indexes[100].records)):
        r=indexes[300].records[sid];accession=r.get('fields',{}).get('accession_number','')
        if not accession:
            report['new_accessions'].append(dict(id=sid,status='missing_accession'));continue
        gold=[k for k,v in indexes[300].records.items() if v.get('fields',{}).get('accession_number')==accession]
        hits=await indexes[300].search(accession);ids=[h['_id'] for h in hits]
        report['new_accessions'].append(dict(id=sid,query=accession,gold=gold,ids=ids,rank=position(ids,gold)))
    save(report);print('Text queries and new accession checks complete',flush=True)
    oldpack=PRIVATE/'MUSE-test-pack-12-20261006';oldmanifest=oldpack/'manifest.json'
    newmanifest=PRIVATE/'photo-stage-30-v1/manifest.json'
    cases=[dict(id=f'legacy-{n:02}',path=str((oldpack/c['file']).relative_to(ROOT)),sha256=c['sha256'],expected=c['expected_source_id'],group='legacy',reference_overlap=c['in_reference_library'])
        for n,c in enumerate(json.loads(oldmanifest.read_bytes()),1)]
    cases += [dict(id=c['id'],path=c['path'],sha256=c['sha256'],expected=c['expected_source_id'],group=c['group'],reference_overlap=False)
        for c in json.loads(newmanifest.read_bytes())['cases']]
    report['photo_manifests']={str(p.relative_to(ROOT)):sha(p) for p in [oldmanifest,newmanifest]}
    for c in cases:
        path=ROOT/c['path'];assert sha(path)==c['sha256'];clean=prepare_image(path.read_bytes())
        row={**c,'results':{}}
        for size in [100,300]:
            t=time.perf_counter();hits=await visuals[size].search(clean,top_k=10);ids=[h['source_id'] for h in hits]
            row['results'][str(size)]=dict(rank=position(ids,[c['expected']]),hits=hits,ms=round((time.perf_counter()-t)*1000,2))
        report['photos'].append(row);save(report)
    warm=MuseumVisualIndex(PRIVATE/'va-pilot-300-v1-references.json',cfg.museum_visual_model,indexes[300].records,
        encoder=encoder,cache_dir=cfg.museum_visual_cache,cache_namespace=MODEL_REVISION)
    t=time.perf_counter();await warm.start();report['warm300']=dict(ms=round((time.perf_counter()-t)*1000,2),cache=warm.cache_stats)
    assert warm.index_hash==visuals[300].index_hash
    report['complete']=True;save(report);print('Completed text + 42 photo retrieval comparison',flush=True)


if __name__=='__main__': asyncio.run(main())
