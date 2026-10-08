"""Real retrieval and worker packing; frozen score replay, no new BGE/LLM calls."""
import asyncio
import argparse
import json
from pathlib import Path

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.rerank_worker import score_request
from app.storage.store import MemoryStore
from evaluate_chinese_recall import sha,rank

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/chinese-recall-service-parity-v1.json'


async def main(output=OUT,glossary=False):
    global OUT
    OUT=output
    import torch
    from transformers import AutoTokenizer
    torch.set_num_threads(4)
    if OUT.exists():raise FileExistsError('Preserve result')
    hashes={'chinese-recall-v3.json':None,
            'evidence-controls-input-v1.json':'f9007b87fa6a5c6635d566434bdf6cf05eeb4c3f84fe7cb824d88c9b48418d20',
            'length-batching-full-v1.json':'37a41ac070e0238a513644e4c7b2049223f5d2df23e9df854bcf7917f277ca06',
            'evidence-ties-replay-v1.json':'e59d59d51d489935a88a4e9d4503a30722c23c5286ef621c2095e76685357e7f'}
    data=[]
    for name,digest in hashes.items():
        path=ROOT/'eval/private'/name
        if digest:assert sha(path)==digest
        data.append(json.loads(path.read_bytes()))
    baseline,frozen,scores,expected=data
    cases={(r['corpus'],r['id']):r for r in frozen['cases']}
    score_rows={(r['corpus'],r['id']):r for r in scores['rows']}
    expected_rows={(r['corpus'],r['id']):r for r in expected['rows']}
    tokenizer=AutoTokenizer.from_pretrained(str(ROOT/'models/bge-reranker-v2-m3'),local_files_only=True)
    report=dict(complete=False,new_bge_calls=0,api_calls=0,fallback_glossary=glossary,rows=[],summary={})
    for name,specification in baseline['corpora'].items():
        spec=specification['source_hashes']
        for prefix in ['public','private']:assert sha(Path(spec[prefix+'_path']))==spec[prefix+'_sha256']
        path=ROOT/f'data/private/chinese-recall-v2-{name}.json'
        assert sha(path)==specification['manifest_sha256']
        cfg=MuseumSettings(_env_file=None,museum_corpus=Path(spec['public_path']),museum_private_corpus=Path(spec['private_path']),
            museum_embedding='local',museum_text_rerank=True,museum_chinese_recall=True,museum_search_fields=path,
            museum_search_allow_drafts=True,museum_rerank_sort_by_length=True,museum_fallback_glossary=glossary,deepseek_api_key='')
        index=MuseumIndex(cfg,MemoryStore());await index.start()
        for row in specification['rows']:
            key=(name,row['id']);case=cases[key];saved=score_rows[key];target=expected_rows[key]
            class ReplayService:
                state='ready';busy=False;called=False
                async def rank(self,query,candidates,sources):
                    self.called=True
                    assert query==case['query']
                    assert [c['source_id'] for c in candidates]==[c['source_id'] for c in case['candidates']],key
                    class Model:
                        def __init__(self):self.tokenizer=tokenizer
                        def score(self,q,passages,**kwargs):
                            assert passages==[c['passage'] for c in case['candidates']],(key,'passage drift')
                            return {k:saved[k] for k in ['scores','token_lengths','truncated']}
                    reply=score_request(Model(),dict(query=query,candidates=candidates,sources=sources),sort_by_length=True,evidence_controls=True)
                    assert reply['ids']==target['arms']['length']['ids'],(key,'rank drift')
                    return reply['ids'],dict(status='cached_score_parity')
            svc=ReplayService();index.reranker=svc
            result,trace=await index.search_for_answer(row['query'])
            svc.busy=True
            fallback,fbtrace=await index.search_for_answer(row['query'])
            old=await index.search(row['query'])
            cfg.museum_text_rerank=False
            disabled,_=await index.search_for_answer(row['query'])
            cfg.museum_text_rerank=True
            assert [r['_id'] for r in disabled]==[r['_id'] for r in fallback]
            item=dict(corpus=name,id=row['id'],gold=row['gold'],kind=row['kind'],
                worker_checked=svc.called,status=trace['status'],fallback_status=fbtrace['status'],
                rank=rank([r['_id'] for r in result],row['gold']),fallback_ids=[r['_id'] for r in fallback],
                fallback_rank=rank([r['_id'] for r in fallback],row['gold']),original_rank=rank([r['_id'] for r in old],row['gold']))
            report['rows'].append(item)
        rows=[r for r in report['rows'] if r['corpus']==name and r['gold'] and r['kind']!='extra']
        report['summary'][name]=dict(n=len(rows),normal_top5=sum(r['rank'] is not None for r in rows),
            original_fallback_top5=sum(r['original_rank'] is not None for r in rows),fallback_top5=sum(r['fallback_rank'] is not None for r in rows),
            fallback_regressions=[r['id'] for r in rows if r['original_rank'] is not None and r['fallback_rank'] is None])
        print(json.dumps(dict(corpus=name,summary=report['summary'][name])),flush=True)
    report['complete']=True
    with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(worker_checked=sum(r['worker_checked'] for r in report['rows']),bypassed=sum(not r['worker_checked'] for r in report['rows']),sha256=sha(OUT))))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=OUT);p.add_argument('--fallback-glossary',action='store_true')
    args=p.parse_args();asyncio.run(main(args.output,args.fallback_glossary))
