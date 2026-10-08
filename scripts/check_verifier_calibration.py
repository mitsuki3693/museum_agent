"""Freeze 14 development review cases, then compare two reviewers (28 calls).

Uses two previously imported public-source records. Original text, generated
responses and labels remain private. Labels are assistant-authored, not human
ground truth. No retrieval, generation, transport retry, or production writes.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from app.museum.config import MuseumSettings
from app.museum.engine import Draft, MuseumEngine, evidence_issues
from app.museum.verification import verify_claims
from app.storage.store import MemoryStore
from check_origin_followups import SelectedIndex, save_new

FIXTURE = Path('eval/private/verifier-calibration-input-v1.json')
PRIOR_FIXTURE = Path('eval/private/verifier-prior-drafts-input-v1.json')


def freeze():
    path = Path('eval/private/fact-selection-input-v1.json')
    previous = json.loads(path.read_text(encoding='utf-8'))
    sources = {c['name']:c['sources'][0] for c in previous['cases']}
    jug, bottle = sources['jug-premise'], sources['bottle-premise']
    def description(source):
        return next(line.removeprefix('briefDescription: ') for line in source['content'].splitlines()
                    if line.startswith('briefDescription: '))
    jq, bq = description(jug), description(bottle)
    cases = []
    def add(name, source, quote, question, texts, expected, rationale, boundary=''):
        claims = [{'text':t,'source_id':source['_id'],'quote':quote} for t in texts]
        assert not evidence_issues(Draft(abstain=False,claims=claims), [source])
        cases.append({'name':name,'sources':[source],'question':question,'claims':claims,
                      'identity_boundary':boundary,'expected_pass':expected,
                      'label_rationale':rationale,'human_review':'pending'})
    add('jug-place',jug,jq,'这件执壶在哪里制作？',
        ['馆方记载这件执壶在代尔夫特制作。'],True,'Original explicitly states manufacture in Delft.')
    add('jug-style-distinction',jug,jq,'中国风纹样说明它在中国制作吗？',
        ['这里的中国风说的是装饰纹样；馆方记载的制作地是代尔夫特。'],True,
        'Separates two explicit attributes without adding geography.')
    add('jug-premise-correction',jug,jq,'既然在中国制作，为什么又写代尔夫特？',
        ['需要先澄清：原文把代尔夫特列为制作地，而中国风描述的是人物和郁金香等装饰纹样。'],True,
        'Corrects a mistaken premise using explicit manufacture and style relations.')
    add('bottle-two-stages',bottle,bq,'它在中国还是代尔夫特制作？',
        ['要区分瓶身与后加的装饰：瓷瓶来自中国，约1700至1725年；柿右卫门风格珐琅装饰在代尔夫特添加，约1720至1725年。'],True,
        'Preserves the two stages and their approximate dates.')
    add('bottle-style-distinction',bottle,bq,'柿右卫门风格说明它在日本制作吗？',
        ['这里的柿右卫门指装饰风格，不等于日本制作；原文记载瓷瓶来自中国，装饰在代尔夫特添加。'],True,
        'Style does not establish manufacture; alternative recorded stages are supplied.')
    add('wrong-place',jug,jq,'制作地在哪里？',
        ['这件执壶在中国制作。'],False,'Contradicts explicit production relation.')
    add('wrong-date',jug,jq,'制作年代是什么？',
        ['这件执壶制作于1800年。'],False,'Contradicts the recorded date range.')
    add('stage-inversion',bottle,bq,'瓶身和装饰分别来自哪里？',
        ['瓶身在代尔夫特制作，装饰后来在中国添加。'],False,'Reverses manufacture and decoration.')
    add('unquoted-geography',jug,'Place: Delft','制作地在什么国家？',
        ['它在荷兰制作。'],False,'Country and manufacture are not entailed by this isolated place label.')
    add('universal-absence',jug,jq,'这件壶是否曾到过中国？',
        ['这件执壶从未到过中国，所有馆方档案也都没有这种记载。'],False,'Absence in one excerpt cannot support universal absence.')
    add('contradiction',jug,jq,'制作地在哪里？',
        ['这件执壶在代尔夫特制作。','这件执壶不是在代尔夫特制作。'],False,'One supported claim cannot cancel another contradictory claim.')
    add('irrelevant-fact',jug,jq,'这件执壶在哪里制作？',
        ['执壶的装饰纹样包括人物与郁金香。'],False,'True quoted detail does not answer place question.')
    add('whole-object-stage-collapse',bottle,bq,'瓶身和装饰都是在代尔夫特完成的吗？',
        ['是的，整件瓶子及其全部制作工序都在代尔夫特完成。'],False,'Wrongly expands decoration location to all production stages.')
    add('unconfirmed-photo',jug,jq,'我拍的就是这件吗？',
        ['你照片中的作品就是这件代尔夫特制作的执壶。'],False,
        'Reference object text cannot establish uploaded-photo identity.',
        '游客只选择查看相似馆藏，上传照片的作品身份尚未确认。当前资料仅属于所选馆藏，不能确认照片身份。')
    save_new(FIXTURE, {'model':previous['model'],'corpus_hash':previous['corpus_hash'],
        'parent_fixture_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'labels':'assistant-authored-development-only; human review pending', 'cases':cases})
    print('Frozen',len(cases),'cases;',hashlib.sha256(FIXTURE.read_bytes()).hexdigest())


def freeze_prior_drafts():
    path = Path('eval/private/answer-recomposition-v1.json')
    previous = json.loads(path.read_text(encoding='utf-8'))
    parent = json.loads(Path('eval/private/fact-selection-input-v1.json').read_text(encoding='utf-8'))
    original = next(c for c in parent['cases'] if c['name']=='jug-premise')
    cases = []
    for i, row in enumerate(previous['rows'][:2]):
        attempt = row['trace']['attempts'][-1]
        cases.append({'name':f'prior-rejected-draft-{i}', 'question':original['query'],
            'sources':original['sources'],'claims':attempt['draft']['claims'],
            'identity_boundary':'','expected_pass':None,'human_review':'pending',
            'label_rationale':'Unchanged real draft; disputed inferences and translated factory name require human review.',
            'previous_verdict':attempt['verdict']})
    save_new(PRIOR_FIXTURE, {'model':parent['model'],'corpus_hash':parent['corpus_hash'],
        'parent_report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'labels':'unlabeled-real-failure-replay; not part of development-label counts','cases':cases})
    print('Frozen prior drafts;',hashlib.sha256(PRIOR_FIXTURE.read_bytes()).hexdigest())


async def run(output, prior_drafts=False):
    fixture_path = PRIOR_FIXTURE if prior_drafts else FIXTURE
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    config = MuseumSettings()
    if not config.deepseek_api_key or config.deepseek_model != fixture['model']:
        raise ValueError('Frozen model and configured credentials required')
    if output.exists() or not output.resolve().is_relative_to(Path('eval/private').resolve()):
        raise ValueError('Use a new private report')
    if len(fixture['cases']) != (2 if prior_drafts else 14):
        raise ValueError('Unexpected frozen suite size')
    rows = []
    report = {'scope':'verifier-only-paired-development-calibration',
              'fixture_sha256':hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
              'suite':'unlabeled-prior-drafts' if prior_drafts else 'development-labels',
              'model':fixture['model'],'temperature':0,'thinking':'disabled',
              'timeout_seconds':25,'max_output_tokens':1800,
              'human_review':'pending','rows':rows}
    with output.open('x',encoding='utf-8') as stream:
        for i, case in enumerate(fixture['cases']):
            assert not evidence_issues(Draft(abstain=False,claims=case['claims']),case['sources'])
            for policy in (['legacy','entailment_v1'] if i%2 == 0 else ['entailment_v1','legacy']):
                engine = MuseumEngine(config,MemoryStore(),SelectedIndex({**fixture,'sources':case['sources']}))
                client = engine._client()
                original = client.complete_json
                row = {'case':case['name'],'policy':policy,'expected_pass':case['expected_pass']}
                async def measured(messages):
                    row['system_sha256'] = hashlib.sha256(messages[0]['content'].encode()).hexdigest()
                    row['response'] = await original(messages)
                    return row['response']
                client.complete_json = measured
                started = time.perf_counter()
                try:
                    verdict, details = await verify_claims(client,case['question'],case['identity_boundary'],case['claims'],policy)
                    row.update(verdict=verdict.model_dump(),structured_review=details,
                               agrees_with_development_label=(verdict.passed==case['expected_pass']
                                                              if case['expected_pass'] is not None else None))
                except Exception as exc:
                    row['error'] = type(exc).__name__
                row['ms'] = round((time.perf_counter()-started)*1000)
                row['usage'] = client.usage_records
                rows.append(row)
                stream.seek(0)
                json.dump(report,stream,ensure_ascii=False,indent=2)
                stream.truncate()
                stream.flush()
                print(json.dumps({k:row[k] for k in ('case','policy','ms','error','agrees_with_development_label') if k in row}),flush=True)
    print('sha256',hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--freeze',action='store_true')
    action.add_argument('--output',type=Path)
    parser.add_argument('--prior-drafts',action='store_true',help='Separate two-draft unmodified failure replay; four calls')
    args = parser.parse_args()
    if args.freeze:
        freeze_prior_drafts() if args.prior_drafts else freeze()
    else:
        asyncio.run(run(args.output,args.prior_drafts))
