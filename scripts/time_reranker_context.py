"""Same-process AB/BA timing probe; never substitutes for quality regression."""
import json
import time
import argparse

from evaluate_reranker_context import INPUT,OLD_INPUT,ROOT,MODEL,MODEL_REVISION,LocalPairReranker,ranked_ids,sha,save_new


def main(compact=False):
    import torch
    output=ROOT/('eval/private/reranker-context-timing-v3.json' if compact else 'eval/private/reranker-context-timing-v2.json')
    journal=output.with_suffix('.jsonl')
    if output.exists() or journal.exists():raise FileExistsError('Preserve timing run')
    paths={'v2':INPUT,'v3':ROOT/'eval/private/reranker-input-v3.json'} if compact else {'v1':OLD_INPUT,'v2':INPUT}
    versions=list(paths)
    old=json.loads(paths[versions[0]].read_bytes());new=json.loads(paths[versions[1]].read_bytes())
    assert new['settings']==old['settings']
    if compact:assert new['previous_input_sha256']==old['previous_input_sha256']==sha(OLD_INPUT)
    else:assert new['previous_input_sha256']==sha(OLD_INPUT)
    assert sha(MODEL/'download-manifest.json')==new['model_manifest_sha256']
    manifest=json.loads((MODEL/'download-manifest.json').read_bytes())
    assert manifest['revision']==MODEL_REVISION
    for name,meta in manifest['files'].items():assert sha(MODEL/name)==meta['sha256']
    settings=new['settings'];torch.set_num_threads(settings['threads'])
    model=LocalPairReranker(MODEL,max_length=settings['max_length'],batch_size=settings['batch_size'])
    sanity=model.score('what is panda?',['hi','The giant panda is a bear species endemic to China.'])
    assert sanity['scores'][1]>sanity['scores'][0]
    sources=dict(zip(versions,[old,new]))
    report=dict(complete=False,input_hashes={v:sha(p) for v,p in paths.items()},
                settings=settings,model_revision=MODEL_REVISION,torch_version=torch.__version__,sanity=sanity,
                code_sha256=sha(ROOT/'scripts/time_reranker_context.py'),rows=[])
    with journal.open('x',encoding='utf-8') as log:
        log.write(json.dumps({k:v for k,v in report.items() if k!='rows'})+'\n');log.flush()
        for case_id in ['old-F07','old-G01']:
            cases={v:next(c for c in data['cases'] if c['corpus']=='scale300' and c['id']==case_id) for v,data in sources.items()}
            a,b=[cases[v] for v in versions]
            assert a['query']==b['query']
            assert [c['source_id'] for c in a['candidates']]==[c['source_id'] for c in b['candidates']]
            for cycle,order in enumerate([versions,versions[::-1]],1):
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


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--compact',action='store_true')
    main(parser.parse_args().compact)
