"""Offline recall repair ablation. No BGE/LLM, network, or live configuration.

Freeze the existing questions; compare current, existing Chinese fields and
source-anchored glossary additions. Separately score fallback Top5 and the
Top25 candidate pool before reranking. Draft fields remain draft.
"""
import asyncio
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from app.museum.config import MuseumSettings
from app.museum.retrieval import MuseumIndex
from app.museum.recall_fields import extend_manifest, lexical_query
from app.museum.search_fields import load_search_fields, field_documents, FIELD_WEIGHTS
from app.museum.work_fusion import fuse_works
from app.retrieval.bm25f import BM25FIndex
from app.storage.store import MemoryStore

ROOT=Path(__file__).resolve().parents[1]
EXTRA=[
    ('browser-samson','找参孙击杀非利士人的雕塑',['va-o14761-samson']),
    ('paraphrase-samson','参孙与非利士人在哪里',['va-o14761-samson']),
    ('paraphrase-jawbone','拿驴下颚骨的参孙',['va-o14761-samson']),
    ('paraphrase-monet','克劳德莫奈有哪些画',['artic-14620','artic-16568']),
    ('paraphrase-lilies','睡莲这张画的材质是什么',['artic-16568']),
    ('paraphrase-gilding','看看有金饰的陶器',[]),
    ('absent-name','蒙娜丽莎',[]),
    ('ambiguous-vase','蓝白花瓶',[]),
]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def ids(hits):return list(dict.fromkeys(h['source_id'] for h in hits))


def rank(values,gold):return next((i for i,s in enumerate(values,1) if s in gold),None)


def build_fields(manifest,records):
    subset=dict(manifest,records=[e for e in manifest['records'] if e['source_id'] in records])
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'fields.json';path.write_text(json.dumps(subset),encoding='utf-8')
        fields,meta=load_search_fields(path,records,allow_drafts=True)
    index=BM25FIndex(FIELD_WEIGHTS);index.index(field_documents(records,fields))
    return index,meta


async def main():
    import torch
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--filter-query',action='store_true')
    args=parser.parse_args()
    output=ROOT/('eval/private/chinese-recall-v3.json' if args.filter_query else 'eval/private/chinese-recall-v2.json')
    torch.set_num_threads(4)
    if output.exists():raise FileExistsError('Preserve completed report')
    frozen_path=ROOT/'eval/private/work-fusion-v1.json'
    frozen=json.loads(frozen_path.read_bytes())
    old_path=ROOT/'data/private/va-search-fields-v1.1.json'
    old=json.loads(old_path.read_bytes())
    report=dict(complete=False,baseline_sha256=sha(frozen_path),fields_sha256=sha(old_path),
                api_calls=0,reranker_run=False,human_reviewed=False,filter_query=args.filter_query,
                code_sha256={p:sha(ROOT/p) for p in ['scripts/evaluate_chinese_recall.py','backend/app/museum/recall_fields.py']},corpora={})
    for name,corpus in frozen['corpora'].items():
        spec=corpus['source_hashes']
        for prefix in ['public','private']:
            assert sha(Path(spec[prefix+'_path']))==spec[prefix+'_sha256']
        index=MuseumIndex(MuseumSettings(_env_file=None,museum_corpus=Path(spec['public_path']),
            museum_private_corpus=Path(spec['private_path']),museum_embedding='local',
            museum_dense_view='original',museum_text_rerank=True,deepseek_api_key=''),MemoryStore())
        await index.start()  # Does not start the BGE worker.
        applicable=dict(old,records=[e for e in old['records'] if e['source_id'] in index.records])
        extended=extend_manifest(applicable,index.records)
        manifest_path=ROOT/f'data/private/chinese-recall-v2-{name}.json'
        if manifest_path.exists():
            assert json.loads(manifest_path.read_bytes())==extended,'Manifest drift'
        else:
            with manifest_path.open('x',encoding='utf-8') as f:json.dump(extended,f,ensure_ascii=False,indent=2)
        field_indices={arm:build_fields(data,index.records)[0] for arm,data in [('existing_fields',applicable),('extended_fields',extended)]}
        cases=corpus['rows']+[dict(id=i,query=q,gold=g,kind='extra',category='diagnostic',language='zh') for i,q,g in EXTRA]
        rows=[]
        for case in cases:
            query=case['query'];start=time.perf_counter()
            vector=index.embedding._local_embed([query])[0]
            original_dense=await index.vectors.search(vector,top_k=25)
            admin_dense=await index.rerank_vectors.search(vector,top_k=25)
            lexical=index.bm25.search(query,top_k=25)
            old_pool=fuse_works(lexical,admin_dense)
            fallback=[s['_id'] for s in await index.search(query)]
            row={k:case[k] for k in ['id','query','gold','kind','category','language']}
            row['arms']={}
            for arm,fields in [('current',None),*field_indices.items()]:
                hits=fields.search(lexical_query(query) if args.filter_query else query,top_k=25) if fields and re.search('[\u4e00-\u9fff]',query) else lexical
                # English controls retain existing search and candidate order.
                changed=fields is not None and bool(re.search('[\u4e00-\u9fff]',query))
                pool=fuse_works(hits,admin_dense) if changed else old_pool
                fb=ids(fuse_works(hits,original_dense))[:5] if changed else fallback
                row['arms'][arm]=dict(pool_ids=ids(pool),pool_rank=rank(ids(pool),case['gold']),
                                      fallback_ids=fb,fallback_rank=rank(fb,case['gold']),
                                      lexical_rank=rank(ids(hits),case['gold']))
            row['ms']=round((time.perf_counter()-start)*1000,2)
            rows.append(row)
        summary={}
        for arm in ['current','existing_fields','extended_fields']:
            labelled=[r for r in rows if r['gold'] and r['kind']!='extra']
            summary[arm]=dict(n=len(labelled),pool_hits=sum(r['arms'][arm]['pool_rank'] is not None for r in labelled),
                fallback_top5=sum(r['arms'][arm]['fallback_rank'] is not None for r in labelled),
                pool_fixed=[r['id'] for r in labelled if r['arms']['current']['pool_rank'] is None and r['arms'][arm]['pool_rank'] is not None],
                pool_regressed=[r['id'] for r in labelled if r['arms']['current']['pool_rank'] is not None and r['arms'][arm]['pool_rank'] is None],
                fallback_fixed=[r['id'] for r in labelled if r['arms']['current']['fallback_rank'] is None and r['arms'][arm]['fallback_rank'] is not None],
                fallback_regressed=[r['id'] for r in labelled if r['arms']['current']['fallback_rank'] is not None and r['arms'][arm]['fallback_rank'] is None])
        report['corpora'][name]=dict(source_hashes=spec,manifest_sha256=sha(manifest_path),rows=rows,summary=summary)
        print(json.dumps(dict(corpus=name,summary=summary)),flush=True)
    report['complete']=True
    with output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print('report_sha256='+sha(output))


if __name__=='__main__':asyncio.run(main())
