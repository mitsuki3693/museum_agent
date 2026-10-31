"""Export private review cards and a source-free public acceptance summary."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from app.museum.config import ROOT
from run_task_benchmark import summary


def build(dataset, report):
    assert dataset['dataset_hash'] == report['dataset_hash']
    rows = report['results']
    counts = Counter()
    cards = []
    public_rows = []
    for case in dataset['cases']:
        row = next((r for r in rows if r['case_id'] == case['id']), None)
        if row is None:
            counts['unrun'] += 1
            continue
        judged = row.get('judge')
        state = ('ai_pass' if row['mechanical_pass'] and all(s['pass'] for s in judged['steps']) else 'ai_fail') if judged else 'pending_judge'
        counts[state] += 1
        public_rows.append({'case_id': case['id'], 'category': case['category'], 'status': state,
                            'mechanical_pass': row['mechanical_pass'], 'elapsed_ms': row['elapsed_ms'], 'human_grade': None})
        cards += [case['id']+' | '+case['category']+' | '+state,
                  '初始作品：'+str(case.get('initial_object_id') or '未选择')]
        for i, step in enumerate(case['steps']):
            actual = row['steps'][i] if i < len(row['steps']) else None
            cards += [f'步骤{i+1}：'+step['query'], '必要要点：'+'；'.join(step['keypoints'])]
            if actual:
                cards += ['实际状态：'+actual['result'].get('status','unknown'),
                          '回答：'+actual['result'].get('answer',''),
                          'Trace：'+str(actual['result'].get('trace_id',''))]
                for claim in actual['result'].get('claims',[]):
                    cards += ['论断：'+claim['text'], '引用：'+claim['quote'], '来源ID：'+claim['source_id']]
            else:
                cards += ['前序未完成，该步未运行。']
            if judged:
                cards += ['AI初评：'+judged['steps'][i]['reason']]
        cards += ['人工结论：待复核。复试需另记，保留首次记录。','']
    public = {'dataset_version':dataset['version'], 'dataset_hash':dataset['dataset_hash'],
              'corpus_hash':report.get('corpus_hash'), 'status':report['status'],
              'human_reviewed':False, 'online_ab':False, 'independent_blind_test':False,
              'scope':'20 agent-authored, stratified development acceptance tasks; real in-process ASGI routes, not browser or production performance. Same-family LLM judge, pending human review.',
              'planned_tasks':len(dataset['cases']), 'counts':dict(counts),
              'summary':report['summary'], 'known_tokens':report.get('known_tokens'),
              'groups': {group: summary([r for r in rows if r['category'] == group])
                         for group in sorted({r['category'] for r in rows})},
              'provider_calls':len(report.get('calls',[])),
              'calls_without_usage':sum(not c.get('usage') for c in report.get('calls',[])),
              'results':public_rows}
    return public, '\n'.join(cards)


def database_record(run_id, report, public):
    """Store compact scores only, with AI and human evidence explicitly separate."""
    from app.museum.api import EvalRunRequest
    metrics = []
    labels = {'ai_task_pass_among_judged': 'AI 初评任务通过率（待人工）',
              'ai_citation_support': 'AI 初评引用支持率（待人工）',
              'ai_keypoint_coverage': 'AI 初评关键点覆盖率（待人工）'}
    for group, scores in [('全部执行任务', public['summary']), *public['groups'].items()]:
        for key, label in labels.items():
            score = scores[key]
            if score['denominator']:
                metrics.append(dict(group=group, variant=run_id, label=label, unit='ratio',
                    value=score['rate'], numerator=score['numerator'], denominator=score['denominator']))
    payload = dict(_id=run_id, created_at=report['created_at'], kind='text', status='completed',
        dataset_version=report['dataset_version'], dataset_hash=report['dataset_hash'],
        corpus_hash=report['corpus_hash'], model=report['model'], prompt_version=report['prompt_version'],
        human_reviewed=False, online_ab=False, summary=public['summary'],
        display=dict(track='generation', decision='pending', metrics=metrics,
            scope='本机真实 ASGI 路由与 DeepSeek；分层开发验收题。同族模型初评，尚未人工复核；修复子集不代表全量通过，不是线上 A/B 或生产延迟。'),
        results=[{**r, 'variant':run_id} for r in public['results']])
    return EvalRunRequest.model_validate(payload).model_dump(by_alias=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--mongo-run-id',help='Optionally archive compact metrics to the configured local MongoDB')
    a=p.parse_args()
    if not a.prefix.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Review cards contain private source text')
    report=json.loads(a.report.read_text(encoding='utf-8'))
    public,cards=build(json.loads(a.dataset.read_text(encoding='utf-8')),report)
    for suffix,content in [('.summary.json',json.dumps(public,ensure_ascii=False,indent=2)),('.review.txt',cards)]:
        with Path(str(a.prefix)+suffix).open('x',encoding='utf-8-sig' if suffix.endswith('txt') else 'utf-8') as f:f.write(content)
    if a.mongo_run_id:
        from app.museum.config import MuseumSettings
        from pymongo import MongoClient
        from pymongo.uri_parser import parse_uri
        cfg=MuseumSettings()
        if any(host not in {'localhost','127.0.0.1','::1'} for host,_ in parse_uri(cfg.mongodb_uri)['nodelist']):
            raise ValueError('This archive command only supports local MongoDB')
        record=database_record(a.mongo_run_id,report,public)
        with MongoClient(cfg.mongodb_uri,serverSelectionTimeoutMS=5000) as mongo:
            collection=mongo[cfg.mongodb_db].eval_runs
            # Never overwrite existing results or human grades.
            collection.insert_one(record)
            assert collection.find_one({'_id':record['_id']}) == record
        print(json.dumps({'mongo_saved_and_read_back':record['_id']}))
    print(json.dumps({'counts':public['counts'],'calls':public['provider_calls'],'known_tokens':public['known_tokens']}))
