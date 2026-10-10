"""Frozen two-photo OCR probe, separate from identity verification.

freeze is local-only. live requires explicit approval for the listed photos;
it reserves each of six calls before sending, never resumes, and never retries.
Raw observations remain under eval/private, never in public summaries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urlparse

from app.museum.config import ROOT, MuseumSettings
from app.museum.engine import MuseumEngine
from app.museum.retrieval import MuseumIndex, canonical_accession
from app.museum.vision import observation_messages, observe_photo, prepare_image
from app.storage.store import MemoryStore

VERSION = 'bottom-mark-ocr-v1'
CASES = [
    dict(id='va-2020MP1936', expected='va-o161638', number='C.169-1910', exact=False),
    dict(id='va-2007BM5078', expected='va-o163433', number='C.929-1922', exact=True),
]
INDEX_KEYS = ['museum_embedding', 'museum_embedding_model', 'museum_dense_view',
    'museum_chinese_recall', 'museum_fallback_glossary', 'museum_catalogue_glossary',
    'museum_metadata_routing', 'museum_search_allow_drafts']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def local_folder(value):
    folder = Path(value).resolve()
    if not folder.is_relative_to((ROOT/'eval/private').resolve()):
        raise ValueError('Evaluation artifacts must stay in eval/private')
    return folder


def prompt_hash():
    return digest(json.dumps(observation_messages(b''), ensure_ascii=False, sort_keys=True).encode())


def number_on_line(text, expected):
    """Exact visible-number transcription, not a fuzzy OCR correction score."""
    for line in unicodedata.normalize('NFKC', text).splitlines():
        bare = re.sub(r'^\s*(?:museum\s+number|accession\s+(?:number|no\.?)|object\s+number|馆藏编号|藏品编号)\s*[:：]\s*',
                      '', line, flags=re.IGNORECASE).strip()
        if canonical_accession(bare) == canonical_accession(expected):
            return True
    return False


def freeze(folder):
    cfg = MuseumSettings()
    capture_path = ROOT/'eval/private/visual-aggregation-v1/capture.json'
    capture = json.loads(capture_path.read_bytes())
    files = {}
    index_config = {key: getattr(cfg, key) for key in INDEX_KEYS}
    # Same text lane as the earlier manually transcribed replay. No BGE/vision
    # calls are needed to test whether OCR output can enter candidate retrieval.
    index_config.update(museum_text_rerank=False, museum_storage='memory')
    for key in ['museum_corpus', 'museum_private_corpus', 'museum_search_fields']:
        path = getattr(cfg, key)
        index_config[key] = str(path) if path else None
        if path:
            files[str(path)] = digest(path.read_bytes())
    assert files[str(cfg.museum_corpus)] == capture['public_corpus_sha256']
    assert files[str(cfg.museum_private_corpus)] == capture['corpus_sha256']
    cases = []
    for case in CASES:
        path = ROOT/'data/private/photo-stage-30-v1'/f"{case['id']}.jpg"
        old = next(row for row in capture['rows'] if row['id'] == case['id'])
        raw = path.read_bytes()
        assert digest(raw) == old['sha256']
        files[str(path)] = digest(raw)
        cases.append({**case, 'path': str(path), 'sha256': digest(raw),
                      'prepared_sha256': digest(prepare_image(raw))})
    plan = dict(version=VERSION, created_at=time.time(), scope='Development OCR and text candidate retrieval only',
        human_reviewed=False, online_ab=False, identity_tested=False, repeats=3, max_calls=6,
        model=cfg.deepseek_model, provider_host=urlparse(cfg.deepseek_base_url).hostname,
        prompt_sha256=prompt_hash(), capture_sha256=digest(capture_path.read_bytes()),
        index_config=index_config, files=files, cases=cases,
        source_sha256={p: digest((ROOT/p).read_bytes()) for p in [
            'backend/app/museum/vision.py', 'backend/app/museum/retrieval.py',
            'backend/app/llm/client.py', 'backend/app/museum/engine.py',
            'scripts/evaluate_photo_ocr_live.py']})
    folder.mkdir(parents=True, exist_ok=False)
    write_new(folder/'plan.json', plan)
    print(json.dumps({'frozen': len(cases), 'max_calls': 6, 'api_calls': 0, 'folder': str(folder)}))


def validate_plan(plan, cfg):
    if (plan['version'] != VERSION or plan['repeats'] != 3 or plan['max_calls'] != 6
            or [{k: c[k] for k in CASES[0]} for c in plan['cases']] != CASES):
        raise ValueError('Changed approval scope')
    if cfg.deepseek_model != plan['model'] or urlparse(cfg.deepseek_base_url).hostname != plan['provider_host']:
        raise ValueError('Provider/model changed after freeze')
    if plan['prompt_sha256'] != prompt_hash():
        raise ValueError('Observation prompt changed after freeze')
    for path, expected in plan['files'].items():
        if digest(Path(path).read_bytes()) != expected:
            raise ValueError('Frozen input changed')
    for path, expected in plan['source_sha256'].items():
        if digest((ROOT/path).read_bytes()) != expected:
            raise ValueError('Code changed after freeze')


async def observe_once(client, clean, case, index):
    started = time.perf_counter()
    row = dict(case_id=case['id'], variant='production_observation', expected=case['expected'],
               observation_ms=None, retrieval_ms=None, schema_ok=False,
               number_line_match=False, candidate_rank=None, exact_ids=[], identity_tested=False)
    try:
        observation = await observe_photo(client, clean)
        row.update(schema_ok=True, observation=observation.model_dump(),
                   number_line_match=number_on_line(observation.visible_text, case['number']))
    except Exception as exc:
        row['failure_stage'] = 'schema' if type(exc).__name__ == 'ValidationError' else 'observation_service'
        # Avoid provider response/header leakage in diagnostic output.
        row['error_type'] = type(exc).__name__
        return row
    finally:
        row['observation_ms'] = round((time.perf_counter()-started)*1000, 2)
        row['usage_records'] = list(getattr(client, 'usage_records', []))
    if not observation.usable:
        row['failure_stage'] = 'unusable_observation'
        return row
    started = time.perf_counter()
    try:
        hits, trace = await index.search_photo_observation(observation.visible_text, observation.visual_description)
        row.update(candidate_ids=[h['_id'] for h in hits], exact_ids=trace['exact_ids'], route=trace['route'])
        row['candidate_rank'] = next((i for i, h in enumerate(hits, 1) if h['_id'] == case['expected']), None)
        if not row['number_line_match']:
            row['failure_stage'] = 'number_transcription_or_line_format'
        elif case['exact'] and case['expected'] not in row['exact_ids']:
            row['failure_stage'] = 'exact_routing'
        elif row['candidate_rank'] is None:
            row['failure_stage'] = 'candidate_retrieval'
        else:
            row['failure_stage'] = None
    except Exception as exc:
        row.update(failure_stage='retrieval_service', error_type=type(exc).__name__)
    finally:
        row['retrieval_ms'] = round((time.perf_counter()-started)*1000, 2)
    return row


def summarize(rows):
    return dict(calls=len(rows), unique_images=len({r['case_id'] for r in rows}),
        schema_ok=sum(r['schema_ok'] for r in rows),
        number_line_match=sum(r['number_line_match'] for r in rows),
        candidate_top1=sum(r['candidate_rank'] == 1 for r in rows),
        candidate_top5=sum(r['candidate_rank'] is not None and r['candidate_rank'] <= 5 for r in rows),
        usage_known_calls=sum(bool(r['usage_records']) for r in rows),
        cost=None, identity_tested=False, human_reviewed=False, online_ab=False)


async def live(folder):
    import torch
    torch.set_num_threads(4)
    plan = json.loads((folder/'plan.json').read_bytes())
    cfg = MuseumSettings()
    validate_plan(plan, cfg)
    if not cfg.deepseek_api_key:
        raise ValueError('Missing provider credential')
    # Exclusive reservation forbids resuming an ambiguous/partially sent run.
    write_new(folder/'started.json', dict(started_at=time.time(), max_calls=6))
    frozen_cfg = MuseumSettings(_env_file=None, **plan['index_config'],
        deepseek_api_key=cfg.deepseek_api_key, deepseek_base_url=cfg.deepseek_base_url, deepseek_model=plan['model'])
    store = MemoryStore()
    index = MuseumIndex(frozen_cfg, store)
    rows = []
    try:
        await index.start()
        engine = MuseumEngine(frozen_cfg, store, index)
        for repeat in range(3):
            for case in plan['cases']:
                clean = prepare_image(Path(case['path']).read_bytes())
                assert digest(clean) == case['prepared_sha256']
                number = len(rows)+1
                write_new(folder/f'call-{number}.json', dict(case_id=case['id'], repeat=repeat+1, reserved_at=time.time()))
                row = await observe_once(engine._client(), clean, case, index)
                row['repeat'] = repeat+1
                write_new(folder/f'row-{number}.json', row)
                rows.append(row)
                print(json.dumps({k: row[k] for k in ['case_id', 'repeat', 'schema_ok', 'number_line_match',
                    'candidate_rank', 'failure_stage']}, ensure_ascii=False), flush=True)
        write_new(folder/'results.json', dict(complete=True, version=VERSION,
            plan_sha256=digest((folder/'plan.json').read_bytes()), corpus_hash=index.corpus_hash,
            summary=summarize(rows), rows=rows))
    finally:
        await index.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'live'])
    parser.add_argument('--folder', type=local_folder, default=ROOT/'eval/private/bottom-mark-ocr-v1')
    args = parser.parse_args()
    if args.command == 'freeze':
        freeze(args.folder)
    else:
        asyncio.run(live(args.folder))
