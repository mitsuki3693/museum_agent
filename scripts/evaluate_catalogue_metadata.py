"""Paired production-path replay plus independent exact-field contracts.

Uses the preceding glossary release's frozen MiniLM candidates. New contract
cases test field equality with lexical fallback, not encoder generalization.
No models/API, source edits, new labels for old questions, or automatic retries.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.museum.config import ROOT
from app.museum.retrieval import MuseumIndex
from app.storage.store import MemoryStore
from evaluate_bilingual_retrieval import settings, versions, rank, FrozenEmbedding, FrozenVectors, private_path


CONTRACTS = [
    ('bologna-en','Find works by Giovanni Bologna','author','Bologna, Giovanni'),
    ('bologna-zh','找乔瓦尼·博洛尼亚的作品','author','Bologna, Giovanni'),
    ('author-order','Find works by Bologna, Giovanni','author','Bologna, Giovanni'),
    ('different-giovanni','Find works by Giovanni Bastianini','author','Giovanni Bastianini'),
    ('author-absent','Find works by Nonexistent Person','author','Nonexistent Person'),
    ('1930-en','Find works dated 1930','date','1930'),
    ('1930-zh','找年代标注为1930的作品','date','1930'),
    ('1930-short','找1930年的作品','date','1930'),
    ('approx-en','Find works dated circa 1930','date','ca. 1930'),
    ('approx-zh','找年代标注为约1930年的作品','date','ca. 1930'),
    ('range','Find works dated 1930-1959','date','1930-1959'),
    ('range-zh','找年代标注为1750–1775的作品','date','1750-1775'),
    ('date-absent','Find works dated 2029','date','2029'),
]


async def main(output):
    frozen_path=ROOT/'eval/private/bilingual-v1-frozen.json'
    previous_path=ROOT/'eval/private/bilingual-v2-replay.json'
    frozen=json.loads(frozen_path.read_bytes())
    previous={r['id']:r for r in json.loads(previous_path.read_bytes())['rows']}
    cfg=settings();cfg.museum_catalogue_glossary=True;cfg.museum_metadata_routing=False
    assert versions(cfg)==frozen['versions'] and frozen['complete']
    assert cfg.museum_embedding_model==frozen['model']
    index=MuseumIndex(cfg,MemoryStore());await index.start()
    index.embedding=FrozenEmbedding(frozen['rows']);index.vectors=FrozenVectors(frozen['rows'])
    index.hybrid.vector_store=index.vectors
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    report=dict(complete=False,scope='Offline development regression, not visitor accuracy',
        versions=versions(cfg),frozen_sha256=sha(frozen_path),previous_sha256=sha(previous_path),
        algorithm_version='catalogue-author-date-v1',rows=[],contracts=[])
    for c in frozen['rows']:
        cfg.museum_metadata_routing=False
        before,_=await index.search_for_answer(c['query']);before_ids=[r['_id'] for r in before]
        assert before_ids==previous[c['id']]['after_ids'],('Previous release drift',c['id'])
        cfg.museum_metadata_routing=True
        after,trace=await index.search_for_answer(c['query']);after_ids=[r['_id'] for r in after]
        report['rows'].append(dict(id=c['id'],group=c['group'],query=c['query'],gold=c['gold'],
            before_ids=before_ids,after_ids=after_ids,before_rank=rank(before_ids,c['gold']),
            after_rank=rank(after_ids,c['gold']),trace=trace))
    # Independent exact-string labels, not computed by the new normalizer.
    # Approximate/range cases stay separate; full original field lines qualify.
    index.embedding=None
    labels={'author':{'Maker','作者 Artist'},'date':{'Date','年代 Date'}}
    for ident,query,kind,value in CONTRACTS:
        expected=[sid for sid,r in index.records.items() if any(
            line.partition(':')[0].strip() in labels[kind] and line.partition(':')[2].strip()==value
            for line in r['content'].splitlines())]
        hits,trace=await index.search_for_answer(query);ids=[r['_id'] for r in hits]
        passed=(trace['status']=='metadata' and set(ids)<=set(expected)
                and len(ids)==min(5,len(expected)) and trace['metadata']['matched_count']==len(expected))
        report['contracts'].append(dict(id=ident,query=query,kind=kind,exact_field_value=value,
            expected=expected,ids=ids,trace=trace,passed=passed))
    report['summary']={}
    for group in sorted({r['group'] for r in report['rows']}):
        rows=[r for r in report['rows'] if r['group']==group and r['gold']]
        if not rows:continue
        report['summary'][group]=dict(n=len(rows),
            before_top1=sum(r['before_rank']==1 for r in rows),after_top1=sum(r['after_rank']==1 for r in rows),
            before_top5=sum(r['before_rank'] is not None for r in rows),after_top5=sum(r['after_rank'] is not None for r in rows),
            worse_rank=[r['id'] for r in rows if r['before_rank'] and (r['after_rank'] or 99)>r['before_rank']],
            changed=[r['id'] for r in rows if r['before_ids']!=r['after_ids']])
    report['contract_passed']=sum(c['passed'] for c in report['contracts'])
    report['complete']=True
    with private_path(output).open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(summary=report['summary'],contract_passed=report['contract_passed'],
                         contract_total=len(CONTRACTS)),ensure_ascii=False,indent=2))
    assert all(c['passed'] for c in report['contracts'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if private_path(args.output).exists():raise FileExistsError('Preserve existing evidence')
    asyncio.run(main(args.output))
