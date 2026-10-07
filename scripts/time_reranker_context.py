"""Same-process AB/BA timing probe; never substitutes for quality regression."""
import json
import time

from evaluate_reranker_context import INPUT,OLD_INPUT,ROOT,MODEL,MODEL_REVISION,LocalPairReranker,ranked_ids,sha,save_new


def main():
    import torch
    output=ROOT/'eval/private/reranker-context-timing-v2.json'
    journal=output.with_suffix('.jsonl')
    if output.exists() or journal.exists():raise FileExistsError('Preserve timing run')
    new=json.loads(INPUT.read_bytes());old=json.loads(OLD_INPUT.read_bytes())
    assert new['previous_input_sha256']==sha(OLD_INPUT) and new['settings']==old['settings']
    assert sha(MODEL/'download-manifest.json')==new['model_manifest_sha256']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION
    for name,meta in manifest['files'].items():assert sha(MODEL/name)==meta['sha256']
    settings=new['settings'];torch.set_num_threads(settings['threads'])
    model=LocalPairReranker(MODEL,max_length=settings['max_length'],batch_size=settings['batch_size'])
    sanity=model.score('what is panda?',['hi','The giant panda is a bear species endemic to China.'])
    assert sanity['scores'][1]>sanity['scores'][0]
    sources={'v1':old,'v2':new}
    report=dict(complete=False,input_hashes={v:sha(p) for v,p in [('v1',OLD_INPUT),('v2',INPUT)]},
                settings=settings,model_revision=MODEL_REVISION,torch_version=torch.__version__,sanity=sanity,
                code_sha256=sha(ROOT/'scripts/time_reranker_context.py'),rows=[])
    with journal.open('x',encoding='utf-8') as log:
        log.write(json.dumps({k:v for k,v in report.items() if k!='rows'})+'\n');log.flush()
        for case_id in ['old-F07','old-G01']:
            cases={v:next(c for c in data['cases'] if c['corpus']=='scale300' and c['id']==case_id) for v,data in sources.items()}
            assert cases['v1']['query']==cases['v2']['query']
            assert [c['source_id'] for c in cases['v1']['candidates']]==[c['source_id'] for c in cases['v2']['candidates']]
            for cycle,order in enumerate([['v1','v2'],['v2','v1']],1):
                for version in order:
                    case=cases[version];start=time.perf_counter()
                    result=model.score(case['query'],[c['passage'] for c in case['candidates']])
                    elapsed=(time.perf_counter()-start)*1000
                    ids=ranked_ids(case['candidates'],result['scores'])
                    row=dict(id=case_id,version=version,cycle=cycle,ms=elapsed,**result,ids=ids,
                        rank=next((i for i,s in enumerate(ids,1) if s in case['gold']),None))
                    report['rows'].append(row);log.write(json.dumps(row)+'\n');log.flush()
                    print(json.dumps({k:row[k] for k in ['id','version','cycle','ms','rank']}),flush=True)
    report['complete']=True;save_new(output,report)


if __name__=='__main__':main()
