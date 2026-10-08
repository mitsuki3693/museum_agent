"""Bounded 300/1000 development acceptance using real local retrieval models.

No paid API, no original photos sent out, no live config changes. Existing
questions are reused regression tests, not a blind accuracy estimate. Each run
is checkpointed and refuses to overwrite evidence. Run each size separately.
"""
import argparse
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex, canonical_accession
from app.museum.recall_fields import extend_manifest
from app.museum.visual_index import MuseumVisualIndex, DinoEncoder, MODEL_REVISION
from app.museum.vision import prepare_image
from app.storage.store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'data/private'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rank(ids, gold):
    return next((i for i, sid in enumerate(ids, 1) if sid in gold), None)


def percentiles(values):
    values = sorted(values)
    return {f'p{p}': values[max(0, math.ceil(len(values)*p/100)-1)]
            for p in (50, 95)} if values else {}


async def main(size, output):
    import torch
    torch.set_num_threads(4)
    if output.exists() or not output.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Use a new private report path')
    corpus = PRIVATE / f'va-pilot-{size}-v1-corpus.json'
    gallery = PRIVATE / f'va-pilot-{size}-v1-references.json'
    public = ROOT/'data/corpus.json'
    raw_records = json.loads(corpus.read_bytes()) + json.loads(public.read_bytes())
    records = {r['_id']:r for r in raw_records}
    assert len(records) == len(raw_records)
    assert len(json.loads(corpus.read_bytes())) == size
    old_records = json.loads((PRIVATE/'va-pilot-300-v1-corpus.json').read_bytes())
    assert all(records[r['_id']]['source_hash'] == r['source_hash'] for r in old_records)
    for r in raw_records:
        # V&A importer hashes rendered text. The existing AIC importer hashes
        # the JSON API record; its frozen public corpus is hashed as a whole.
        if r['_id'].startswith('va-'):
            assert hashlib.sha256(r['content'].encode()).hexdigest() == r['source_hash'], r['_id']
        snapshot = r.get('source_snapshot')
        if snapshot:
            path = snapshot.get('path', snapshot.get('page_path'))
            digest = snapshot.get('sha256', snapshot.get('page_sha256'))
            assert path and digest and sha(PRIVATE/path) == digest
    fields = PRIVATE/'chinese-recall-v2-scale300.json'
    if size != 300:
        original = json.loads(fields.read_bytes())
        annotated = {e['source_id'] for e in original['records']}
        extra = extend_manifest(dict(schema_version=1, version='scale-extra',records=[]),
                                {k:v for k,v in records.items() if k not in annotated})
        extended = {**original, 'version':f'scale-{size}-preserved-fields-v1',
                    'records':original['records'] + extra['records']}
        fields = PRIVATE/f'chinese-recall-scale-{size}-v1.json'
        if fields.exists():
            assert json.loads(fields.read_bytes()) == extended
        else:
            with fields.open('x',encoding='utf-8') as f:
                json.dump(extended,f,ensure_ascii=False,indent=2)
    frozen_path = ROOT/'eval/private/chinese-recall-v3.json'
    cases = json.loads(frozen_path.read_bytes())['corpora']['scale300']['rows']
    labelled = [c for c in cases if c['gold'] and c['kind']!='extra']
    # Select once from case IDs, independent of outcomes and corpus size.
    normal_ids = {c['id'] for c in sorted(labelled,key=lambda c:hashlib.sha256(c['id'].encode()).hexdigest())[:12]}
    cfg = MuseumSettings(_env_file=None, museum_corpus=public, museum_private_corpus=corpus,
        museum_storage='memory', deepseek_api_key='', museum_embedding='local',
        museum_text_rerank=True, museum_rerank_sort_by_length=True, museum_chinese_recall=True,
        museum_fallback_glossary=True, museum_search_fields=fields, museum_search_allow_drafts=True)
    report = dict(size=size, complete=False, api_calls=0,
        scope='local development regression; not end-to-end identity or answer accuracy',
        corpus_sha256=sha(corpus), public_corpus_sha256=sha(public), gallery_sha256=sha(gallery), fields_sha256=sha(fields),
        frozen_questions_sha256=sha(frozen_path), normal_case_ids=sorted(normal_ids),
        embedding_model=cfg.museum_embedding_model, visual_revision=MODEL_REVISION,
        text=[], normal=[], accessions=[], photos=[], new_snippet_queries=[], integrity='passed')
    with output.open('x',encoding='utf-8') as stream:
        def checkpoint(stage):
            report['stage'] = stage
            stream.seek(0); json.dump(report,stream,ensure_ascii=False,indent=2)
            stream.truncate(); stream.flush()
            print(json.dumps(dict(size=size,stage=stage)),flush=True)
        checkpoint('integrity_checked')
        index = MuseumIndex(cfg,MemoryStore())
        start = time.perf_counter(); await index.start()
        report.update(records=len(index.records),corpus_hash=index.corpus_hash,
                      text_build_ms=round((time.perf_counter()-start)*1000),
                      type_counts=dict(Counter(r['title'] for r in raw_records)),
                      annotated_fields=index.search_fields_meta)
        checkpoint('text_index_ready')
        for sid,r in index.records.items():
            if not sid.startswith('va-'): continue
            number = r.get('fields',{}).get('accession_number','')
            key = canonical_accession(number)
            if not key:
                report['accessions'].append(dict(id=sid,status='missing_or_unsupported'))
                continue
            expected = index.accessions[key]
            forms = [number, number.lower()]
            outcomes=[]
            for form in forms:
                hits = await index.search(form)
                outcomes.append([h['_id'] for h in hits] == expected)
            # Test only exact routing for invalid forms; fuzzy retrieval may
            # legitimately propose similar records and is not an exact match.
            wrong = number + '-INVALID999999'
            assert not index.exact_accession_ids(wrong)
            assert sid in expected and all(outcomes)
            report['accessions'].append(dict(id=sid,status='pass',ambiguous=len(expected)>1,forms=len(forms)))
        checkpoint('accessions_checked')
        for c in cases:
            start = time.perf_counter(); hits, trace = await index.search_for_answer(c['query'])
            ids = [h['_id'] for h in hits]
            report['text'].append(dict(id=c['id'],query=c['query'],gold=c['gold'],kind=c['kind'],
                ids=ids,rank=rank(ids,c['gold']),ms=round((time.perf_counter()-start)*1000,2),status=trace['status']))
        checkpoint('fallback_regression_checked')
        if size > 300:
            old_ids={r['_id'] for r in old_records}
            new = sorted((r for r in raw_records if r['_id'].startswith('va-') and r['_id'] not in old_ids),
                         key=lambda r:hashlib.sha256(r['_id'].encode()).hexdigest())[:20]
            frozen = []
            for r in new:
                line=next((line.split(':',1)[1].strip() for line in r['content'].splitlines()
                           if line.startswith('briefDescription:')),r['title'])
                frozen.append(dict(id=r['_id'],query=line[:160],source_hash=r['source_hash']))
            # Freeze before retrieval. These copied source snippets test wiring,
            # not unseen human phrasing, so report them separately.
            report['new_snippet_manifest']=frozen;checkpoint('new_snippets_frozen')
            for c in frozen:
                hits,_=await index.search_for_answer(c['query']);ids=[h['_id'] for h in hits]
                report['new_snippet_queries'].append(dict(**c,ids=ids,rank=rank(ids,[c['id']])))
        await index.start_reranker()
        report['reranker_start_state']=index.reranker.state
        for c in cases:
            if c['id'] not in normal_ids:continue
            start=time.perf_counter();hits,trace=await index.search_for_answer(c['query']);ids=[h['_id'] for h in hits]
            report['normal'].append(dict(id=c['id'],query=c['query'],gold=c['gold'],ids=ids,
                rank=rank(ids,c['gold']),ms=round((time.perf_counter()-start)*1000,2),trace=trace))
            checkpoint('real_reranker_'+c['id'])
        await index.close()
        visual = MuseumVisualIndex(gallery,cfg.museum_visual_model,index.records,
            encoder=DinoEncoder(cfg.museum_visual_model),cache_dir=cfg.museum_visual_cache,cache_namespace=MODEL_REVISION)
        start=time.perf_counter();await visual.start()
        report['visual']=dict(works=len(visual.references_by_source),images=len(visual.images),
            index_hash=visual.index_hash,build_ms=round((time.perf_counter()-start)*1000),cache=visual.cache_stats)
        checkpoint('visual_index_ready')
        oldpack=PRIVATE/'MUSE-test-pack-12-20261006';oldmanifest=oldpack/'manifest.json'
        newmanifest=PRIVATE/'photo-stage-30-v1/manifest.json'
        photos=[dict(id=f'legacy-{n:02}',path=str((oldpack/c['file']).relative_to(ROOT)),sha256=c['sha256'],
            expected=c['expected_source_id'],group='legacy',reference_overlap=c['in_reference_library'])
            for n,c in enumerate(json.loads(oldmanifest.read_bytes()),1)]
        photos += [dict(id=c['id'],path=c['path'],sha256=c['sha256'],expected=c['expected_source_id'],
                        group=c['group'],reference_overlap=False) for c in json.loads(newmanifest.read_bytes())['cases']]
        ref_hashes={r['sha256'] for r in json.loads(gallery.read_bytes())['references']}
        report['photo_manifest_hashes']=[sha(oldmanifest),sha(newmanifest)]
        for c in photos:
            path=ROOT/c['path'];assert sha(path)==c['sha256']
            image=prepare_image(path.read_bytes());start=time.perf_counter()
            hits=await visual.search(image,top_k=10);ids=[h['source_id'] for h in hits]
            report['photos'].append(dict(**c,exact_reference_overlap=c['sha256'] in ref_hashes,
                in_corpus=c['expected'] in records,rank=rank(ids,[c['expected']]),hits=hits,
                ms=round((time.perf_counter()-start)*1000,2)))
        scored=[r for r in report['text'] if r['gold'] and r['kind']!='extra']
        report['summary']=dict(fallback_n=len(scored),fallback_top1=sum(r['rank']==1 for r in scored),
            fallback_top5=sum(r['rank'] is not None and r['rank']<=5 for r in scored),
            fallback_latency_ms=percentiles([r['ms'] for r in scored]),
            normal_n=len(report['normal']),normal_top5=sum(r['rank'] is not None and r['rank']<=5 for r in report['normal']),
            normal_statuses=dict(Counter(r['trace']['status'] for r in report['normal'])),
            normal_latency_ms=percentiles([r['ms'] for r in report['normal']]),
            accession_statuses=dict(Counter(r['status'] for r in report['accessions'])),
            new_snippet_top5=sum(r['rank'] is not None for r in report['new_snippet_queries']))
        report['complete']=True;checkpoint('complete')
        print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--size',type=int,choices=[300,1000],required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();asyncio.run(main(args.size,args.output))
