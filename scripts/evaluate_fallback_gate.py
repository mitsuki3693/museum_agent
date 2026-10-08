"""Fixed saved fallback results plus one predeclared coverage rule. No model calls."""
import json
from pathlib import Path

from app.museum.search_fields import load_search_fields,field_documents,FIELD_WEIGHTS
from fallback_coverage_experiment import fallback_gate
from app.retrieval.bm25f import BM25FIndex
from evaluate_chinese_recall import sha

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'eval/private/fallback-gate-replay-v1.json'


def main():
    if OUT.exists():raise FileExistsError('Preserve result')
    prior=ROOT/'eval/private/chinese-recall-service-parity-v1.json'
    assert sha(prior)=='e9501f4d823cad795d57c156afa2e89f5bec9bf0df37a19412f8efe3d5034dbf'
    saved=json.loads(prior.read_bytes());assert saved['complete']
    baseline=json.loads((ROOT/'eval/private/chinese-recall-v3.json').read_bytes())
    report=dict(complete=False,new_model_calls=0,baseline_sha256=sha(prior),
                module_sha256=sha(ROOT/'scripts/fallback_coverage_experiment.py'),rows=[],summary={})
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes'];records={}
        for prefix in ['private','public']:
            path=Path(spec[prefix+'_path']);assert sha(path)==spec[prefix+'_sha256']
            records.update({r['_id']:r for r in json.loads(path.read_bytes())})
        path=ROOT/f'data/private/chinese-recall-v2-{name}.json'
        assert sha(path)==corpus['manifest_sha256']
        fields,_=load_search_fields(path,records,allow_drafts=True)
        index=BM25FIndex(FIELD_WEIGHTS);index.index(field_documents(records,fields))
        queries={r['id']:r['query'] for r in corpus['rows']}
        for old in saved['rows']:
            if old['corpus']!=name:continue
            query=queries[old['id']]
            audit=fallback_gate(query,index)
            applies=old['status']!='named_title' and any('\u4e00'<=c<='\u9fff' for c in query)
            chosen=old['original_rank'] if applies and audit['decision']=='original' else old['fallback_rank']
            report['rows'].append(dict(old,gate=audit,gate_applies=applies,gated_rank=chosen))
        rows=[r for r in report['rows'] if r['corpus']==name and r['gold'] and r['kind']!='extra']
        report['summary'][name]=dict(n=len(rows),before=sum(r['fallback_rank'] is not None for r in rows),
            after=sum(r['gated_rank'] is not None for r in rows),
            regressed=[r['id'] for r in rows if r['fallback_rank'] is not None and r['gated_rank'] is None],
            fixed=[r['id'] for r in rows if r['fallback_rank'] is None and r['gated_rank'] is not None],
            old_search_regressions=[r['id'] for r in rows if r['original_rank'] is not None and r['gated_rank'] is None])
    report['complete']=True
    with OUT.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['summary']));print(sha(OUT))


if __name__=='__main__':main()
