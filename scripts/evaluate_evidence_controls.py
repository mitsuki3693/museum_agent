"""Independent ablations: exact repeated-title removal and material preference.

No live switch, new retrieval, API call or gold-dependent transformation.
Freeze once; run without flags for a probe, then --full. Reports are immutable.
"""
import argparse
import copy
import json
import statistics
import time
from pathlib import Path

from app.museum.rerank_evidence_controls import remove_repeated_title, prioritize_material, VERSION
from app.museum.local_reranker import LocalPairReranker, MODEL_REVISION, ranked_ids
from evaluate_recall_rerank import request_key
from evaluate_chinese_recall import sha, rank

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT/'models/bge-reranker-v2-m3'
INPUT = ROOT/'eval/private/evidence-controls-input-v1.json'
BASE_INPUT = ROOT/'eval/private/recall-rerank-input-v1.json'
BASE_RESULTS = ROOT/'eval/private/recall-rerank-results-v1.json'
CORPORA = ROOT/'eval/private/chinese-recall-v3.json'
MODULE = ROOT/'backend/app/museum/rerank_evidence_controls.py'


def save(path, data):
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_records():
    result = {}
    for name, spec in json.loads(CORPORA.read_bytes())['corpora'].items():
        hashes = spec['source_hashes']; rows = []
        for prefix in ['public', 'private']:
            path = Path(hashes[prefix+'_path'])
            assert sha(path) == hashes[prefix+'_sha256']
            rows.extend(json.loads(path.read_bytes()))
        result[name] = {r['_id']: r for r in rows}
        assert len(result[name]) == len(rows)
    return result


def freeze():
    base = json.loads(BASE_INPUT.read_bytes()); records = load_records()
    assert base['frozen'] and sha(CORPORA) == base['baseline_sha256']
    data = copy.deepcopy(base)
    data.update(base_input_sha256=sha(BASE_INPUT), base_results_sha256=sha(BASE_RESULTS),
                version=VERSION, module_sha256=sha(MODULE))
    for case in data['cases']:
        for c in case['candidates']:
            assert records[case['corpus']][c['source_id']]['source_hash'] == c['source_hash']
            passage = remove_repeated_title(c['passage'])
            c['repeated_title_removed'] = passage != c['passage']
            c['passage'] = passage
        case['request_key'] = request_key(case)
    save(INPUT, data)
    print(json.dumps(dict(frozen=len(data['cases']), changed_pairs=sum(c['repeated_title_removed']
        for case in data['cases'] for c in case['candidates']), sha256=sha(INPUT))))


def run(full):
    import torch
    data = json.loads(INPUT.read_bytes()); records = load_records()
    assert data['frozen'] and data['module_sha256'] == sha(MODULE)
    assert data['base_input_sha256'] == sha(BASE_INPUT) and data['base_results_sha256'] == sha(BASE_RESULTS)
    assert data['baseline_sha256'] == sha(CORPORA)
    manifest = json.loads((MODEL/'download-manifest.json').read_bytes())
    assert data['model_revision'] == MODEL_REVISION == manifest['revision']
    assert sha(MODEL/'download-manifest.json') == data['model_manifest_sha256']
    for name, meta in manifest['files'].items():
        assert sha(MODEL/name) == meta['sha256']
    baseline = json.loads(BASE_RESULTS.read_bytes()); assert baseline['complete']
    assert baseline['input_sha256'] == sha(BASE_INPUT)
    previous = {(r['corpus'], r['id']): r for r in baseline['rows']}
    cache = {r['request_key']: r for r in baseline['rows']}
    probe_path = ROOT/'eval/private/evidence-controls-probe-v1.json'
    if full and probe_path.exists():
        probe = json.loads(probe_path.read_bytes())
        assert probe['complete'] and probe['input_sha256'] == sha(INPUT)
        cache.update({r['request_key']: r for r in probe['rows']})
    output = ROOT/('eval/private/evidence-controls-results-v1.json' if full else 'eval/private/evidence-controls-probe-v1.json')
    journal = output.with_suffix('.jsonl')
    if output.exists() or journal.exists():
        raise FileExistsError('Preserve prior run')
    torch.set_num_threads(data['settings']['threads'])
    model = LocalPairReranker(MODEL, max_length=data['settings']['max_length'], batch_size=data['settings']['batch_size'])
    cases = [c for c in data['cases'] if full or c['id'] in ['M05-zh', 'old-G01']]
    report = dict(complete=False, input_sha256=sha(INPUT), module_sha256=sha(MODULE),
                  api_calls=0, model_revision=MODEL_REVISION, scope='full' if full else 'probe', rows=[])
    with journal.open('x', encoding='utf-8') as log:
        for i, case in enumerate(cases, 1):
            for c in case['candidates']:
                assert records[case['corpus']][c['source_id']]['source_hash'] == c['source_hash']
            cached = cache.get(case['request_key']) if full else None
            start = time.perf_counter()
            result = ({k: cached[k] for k in ['scores', 'token_lengths', 'truncated']} if cached else
                      model.score(case['query'], [c['passage'] for c in case['candidates']]))
            ms = None if cached else round((time.perf_counter()-start)*1000)
            assert result['truncated'] == 0 and max(result['token_lengths']) <= 256
            ids = ranked_ids(case['candidates'], result['scores'])
            prev = previous[(case['corpus'], case['id'])]
            material_only, audit = prioritize_material(case['query'], prev['ids'], records[case['corpus']])
            combined, _ = prioritize_material(case['query'], ids, records[case['corpus']])
            row = {k: case[k] for k in ['corpus', 'id', 'query', 'gold', 'request_key']}
            row.update(result, cached=bool(cached), ms=ms, has_baseline=prev['has_baseline'], material=audit,
                arms={name: dict(ids=ordered, rank=rank(ordered[:5], case['gold'])) for name, ordered in
                      [('baseline', prev['ids']), ('material_only', material_only), ('title_only', ids), ('combined', combined)]})
            report['rows'].append(row); log.write(json.dumps(row, ensure_ascii=False)+'\n'); log.flush()
            print(json.dumps(dict(done=i, total=len(cases), corpus=case['corpus'], id=case['id'],
                ranks={k:v['rank'] for k,v in row['arms'].items()}, cached=bool(cached), ms=ms)), flush=True)
    report['summary'] = {}
    for corpus in ['web_config', 'scale300']:
        rows = [r for r in report['rows'] if r['corpus']==corpus and r['has_baseline'] and r['gold']]
        report['summary'][corpus] = {name: dict(n=len(rows), top1=sum(r['arms'][name]['rank']==1 for r in rows),
            top5=sum(r['arms'][name]['rank'] is not None for r in rows),
            regressions=[r['id'] for r in rows if r['arms']['baseline']['rank'] is not None
                         and (r['arms'][name]['rank'] is None or r['arms'][name]['rank']>r['arms']['baseline']['rank'])])
            for name in ['baseline', 'material_only', 'title_only', 'combined']}
    fresh = [r['ms'] for r in report['rows'] if not r['cached']]
    report.update(complete=True, fresh_calls=len(fresh), cached_calls=len(report['rows'])-len(fresh),
                  fresh_p50_ms=statistics.median(fresh) if fresh else None)
    save(output, report)
    print(json.dumps(dict(summary=report['summary'], sha256=sha(output))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze', action='store_true'); parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    freeze() if args.freeze else run(args.full)
