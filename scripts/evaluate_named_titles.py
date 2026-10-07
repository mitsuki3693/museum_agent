"""Check the narrow title shortcut against frozen base-search candidates.

Does not evaluate BGE, discovery, generation or visual recognition.
"""
import json
from pathlib import Path
from app.museum.recall_fields import named_title_ids,order_named_ids
from app.museum.search_fields import load_search_fields
from evaluate_chinese_recall import sha,rank

ROOT=Path(__file__).resolve().parents[1]


def main():
    baseline_path=ROOT/'eval/private/chinese-recall-v3.json'
    baseline=json.loads(baseline_path.read_bytes())
    output=ROOT/'eval/private/named-title-routing-v2.json'
    if output.exists():raise FileExistsError('Preserve acceptance evidence')
    report={'baseline_sha256':sha(baseline_path),'corpora':{}}
    for name,corpus in baseline['corpora'].items():
        spec=corpus['source_hashes'];records={}
        for prefix in ['public','private']:
            path=Path(spec[prefix+'_path']);assert sha(path)==spec[prefix+'_sha256']
            records.update({r['_id']:r for r in json.loads(path.read_bytes())})
        manifest=ROOT/f'data/private/chinese-recall-v2-{name}.json'
        assert sha(manifest)==corpus['manifest_sha256']
        fields,meta=load_search_fields(manifest,records,allow_drafts=True)
        rows=[]
        for case in corpus['rows']:
            named=named_title_ids(case['query'],fields)
            before=case['arms']['current']['fallback_ids']
            named=order_named_ids(named,before);after=named or before
            rows.append(dict(id=case['id'],query=case['query'],gold=case['gold'],extra=case['kind']=='extra',
                             named_ids=named,before=before,after=after,
                             before_rank=rank(before[:5],case['gold']),after_rank=rank(after[:5],case['gold'])))
        labelled=[r for r in rows if r['gold'] and not r['extra']]
        summary=dict(n=len(labelled),changed=[r['id'] for r in rows if r['before']!=r['after']],
                     fixed=[r['id'] for r in labelled if r['before_rank'] is None and r['after_rank'] is not None],
                     regressed=[r['id'] for r in labelled if r['before_rank'] is not None and r['after_rank'] is None],
                     before_top5=sum(r['before_rank'] is not None for r in labelled),
                     after_top5=sum(r['after_rank'] is not None for r in labelled))
        summary.update(top1_regressed=[r['id'] for r in labelled if r['before_rank']==1 and r['after_rank']!=1])
        for row in rows:
            if row['id']=='browser-samson':assert row['after_rank']==1
            if row['id']=='absent-name':assert not row['named_ids']
        assert not summary['regressed'],(name,summary)
        assert not summary['top1_regressed'],(name,summary)
        report['corpora'][name]=dict(summary=summary,manifest=meta,rows=rows)
        print(json.dumps(dict(corpus=name,summary=summary)))
    with output.open('x',encoding='utf-8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print('report_sha256='+sha(output))


if __name__=='__main__':main()
