"""Rejudge every saved answer with a frozen rubric; never rerun the application."""
import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path

from app.museum.config import ROOT, MuseumSettings
from run_task_benchmark import (Budget, JUDGE_PROMPT, JUDGE_VERSION, judge_input,
    mechanical_grade, summary, validate_dataset, validate_judge)


async def run(a):
    cfg=MuseumSettings()
    raw=cfg.museum_corpus.read_bytes(); private=cfg.museum_private_corpus.read_bytes()
    records={r['_id']:r for r in json.loads(private)+json.loads(raw)}
    dataset=json.loads(a.dataset.read_text(encoding='utf-8'))
    validate_dataset(dataset,records,hashlib.sha256(raw+b'\n'+private).hexdigest())
    old=json.loads(a.report.read_text(encoding='utf-8'))
    assert old['status']=='completed' and old['dataset_hash']==dataset['dataset_hash']
    assert len(old['results'])==len(dataset['cases'])
    if not a.output.resolve().is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Private output required')
    with a.output.open('x',encoding='utf-8'):pass
    budget=Budget(min(20,140-len(old['calls'])))
    budget.known_tokens=old['known_tokens']
    report=copy.deepcopy(old)
    report.update(status='running',judge_version=JUDGE_VERSION,judge_prompt=JUDGE_PROMPT,
        original_report_sha256=hashlib.sha256(a.report.read_bytes()).hexdigest(),
        adjudication_note='All saved outputs rejudged. Reference candidates expanded from frozen corpus. Unknown IDs may clarify. Application outputs and task gold unchanged. Original scores preserved separately.')
    for row in report['results']:
        row['original_judge']=row.pop('judge',None)
        row['original_judge_error']=row.pop('judge_error',None)
        row['original_mechanical_pass']=row['mechanical_pass']
    def save():
        report['calls']=old['calls']+budget.rows
        report['known_tokens']=budget.known_tokens
        report['summary']=summary(report['results'])
        a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for case,row in zip(dataset['cases'],report['results']):
        assert case['id']==row['case_id']
        row['mechanical_pass']=len(row['steps'])==len(case['steps']) and all(
            mechanical_grade(s,r['result'],records)['behavior_pass'] for s,r in zip(case['steps'],row['steps']))
        if len(row['steps'])!=len(case['steps']):
            row['judge_error']='Blocked task';save();continue
        payload=judge_input(case,row['steps'],records)
        try:
            value=await budget.client(cfg,'judge_v2').complete_json([
                {'role':'system','content':JUDGE_PROMPT},
                {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],max_tokens=1800)
            row['judge_raw']=value
            row['judge']=validate_judge(value,payload['requirements'])
        except Exception as exc:
            row['judge_error']=type(exc).__name__
        save()
        print(json.dumps({'case':case['id'],'judged':bool(row.get('judge')),'cumulative_calls':len(report['calls']),'known_tokens':report['known_tokens']}),flush=True)
    report['status']='completed';save()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('dataset','report','output'):p.add_argument('--'+name,type=Path,required=True)
    asyncio.run(run(p.parse_args()))
