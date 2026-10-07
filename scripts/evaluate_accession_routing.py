"""Local exact-accession acceptance; preserves prior 300-work text baseline."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import statistics
import time
import torch
from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex, canonical_accession
from app.storage.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/accession-routing-v1.json'


async def main():
    if OUT.exists():raise FileExistsError('Preserve evaluation evidence')
    torch.set_num_threads(4)
    baseline=ROOT/'eval/private/scale-300-v1.json'
    previous=json.loads(baseline.read_bytes());assert previous['complete']
    corpus=ROOT/'data/private/va-pilot-300-v1-corpus.json'
    assert hashlib.sha256(corpus.read_bytes()).hexdigest()==previous['versions']['300']['corpus_sha256']
    cfg=MuseumSettings().model_copy(update={'museum_private_corpus':corpus,'deepseek_api_key':'','museum_storage':'memory'})
    store=MemoryStore();index=MuseumIndex(cfg,store);started=time.perf_counter();await index.start()
    report=dict(baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),scope='Identifier and fixed-query development acceptance, not live UX/answer quality',
        index_start_ms=round((time.perf_counter()-started)*1000,2),exact=[],controls=[],natural=[],complete=False)
    for case in previous['new_accessions']:
        original=case['query'];key=canonical_accession(original)
        forms=dict(original=original,spaces=re.sub(r'[^a-zA-Z0-9]+',' ',original).lower(),
            hyphens=re.sub(r'[^a-zA-Z0-9]+','-',original).lower(),compact=key)
        for style,query in forms.items():
            t=time.perf_counter();hits=await index.search(query)
            report['exact'].append(dict(id=case['id'],style=style,query=query,gold=case['gold'],ids=[h['_id'] for h in hits],ms=round((time.perf_counter()-t)*1000,4)))
        for style,query in [('wrong',key+'999999'),('partial',key[:max(1,len(key)//2)])]:
            # A prefix that happens to be someone else's full identifier is not
            # a valid negative. Record that exclusion instead of mislabelling.
            hits=index.exact_accession_ids(query)
            report['controls'].append(dict(style=style,query=query,ids=hits,excluded=style=='partial' and bool(hits)))
    all_queries=[(r['id'],r['query'],r['results']['300']['ids']) for r in previous['text']]
    all_queries += [(r['id'],r['query'],r['ids']) for r in previous['new_text']]
    for case_id,query,old_ids in all_queries:
        hits=await index.search(query);ids=[h['_id'] for h in hits]
        report['natural'].append(dict(id=case_id,query=query,before=old_ids,after=ids,unchanged=ids==old_ids))
    # Report normalization collisions across the actual full candidate corpus.
    report['collisions']={k:v for k,v in index.accessions.items() if len(v)>1}
    values=sorted(r['ms'] for r in report['exact'])
    report['summary']=dict(original_top1=sum(bool(r['ids']) and r['ids'][0] in r['gold'] for r in report['exact'] if r['style']=='original'),
        all_forms_top1=sum(bool(r['ids']) and r['ids'][0] in r['gold'] for r in report['exact']),forms=len(report['exact']),
        false_exact_controls=sum(bool(r['ids']) for r in report['controls'] if not r['excluded']),
        excluded_controls=sum(r['excluded'] for r in report['controls']),
        unchanged_natural=sum(r['unchanged'] for r in report['natural']),natural_total=len(report['natural']),
        p50_ms=statistics.median(values),p95_nearest_rank_ms=values[(95*len(values)+99)//100-1])
    report['complete']=True
    with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['summary']),flush=True)


if __name__=='__main__':asyncio.run(main())
