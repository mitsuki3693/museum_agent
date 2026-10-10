"""Keep observed OCR, candidate retrieval, and identity outcomes separate."""
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.museum.vision import observation_messages, prepare_image
from .test_museum_visual_retrieval import image_bytes

spec = importlib.util.spec_from_file_location('photo_ocr_probe',
    Path(__file__).resolve().parents[2]/'scripts/evaluate_photo_ocr_live.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.asyncio
async def test_observed_ocr_reaches_real_field_router_with_no_identity_claim():
    from .test_photo_accession_rescue import make_index
    index, _ = await make_index()
    client = SimpleNamespace(complete_json=AsyncMock(return_value=dict(
        usable=True, visible_text='C.169-1910', visual_description='蓝白盘子')), usage_records=[])
    case = dict(id='synthetic', expected='pitcher', number='C.169-1910', exact=True)
    clean = prepare_image(image_bytes())
    row = await probe.observe_once(client, clean, case, index)
    assert row['candidate_rank'] == 1 and row['number_line_match']
    assert row['exact_ids'] == ['pitcher'] and row['failure_stage'] is None
    assert row['identity_tested'] is False
    messages = client.complete_json.call_args.args[0]
    assert messages == observation_messages(clean)
    encoded = messages[1]['content'][1]['image_url']['url'].split(',', 1)[1]
    assert base64.b64decode(encoded) == clean
    assert 'pitcher' not in json.dumps(messages)  # no oracle/target name sent
    client.complete_json.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('value,stage', [
    (dict(usable=True, visible_text='C.16g-1910', visual_description='蓝白盘子'), 'number_transcription_or_line_format'),
    (dict(usable=False, visible_text='', visual_description=''), 'unusable_observation'),
    (dict(usable='true', visible_text='C.169-1910'), 'schema'),
])
async def test_failure_stage_stays_distinct_from_retrieval(value, stage):
    index = SimpleNamespace(search_photo_observation=AsyncMock(return_value=([], dict(exact_ids=[], route='semantic_observation'))))
    client = SimpleNamespace(complete_json=AsyncMock(return_value=value), usage_records=[])
    row = await probe.observe_once(client, b'prepared', dict(id='x', expected='pitcher', number='C.169-1910', exact=True), index)
    assert row['failure_stage'] == stage and row['candidate_rank'] is None
    assert row['identity_tested'] is False
    client.complete_json.assert_awaited_once()
    if stage in {'schema', 'unusable_observation'}:
        index.search_photo_observation.assert_not_called()


@pytest.mark.asyncio
async def test_provider_error_is_not_retried_or_published_as_raw_error():
    client = SimpleNamespace(complete_json=AsyncMock(side_effect=RuntimeError('private supplier body')), usage_records=[])
    index = SimpleNamespace(search_photo_observation=AsyncMock())
    row = await probe.observe_once(client, b'', dict(id='x', expected='y', number='C.1-1900', exact=True), index)
    assert row['failure_stage'] == 'observation_service'
    assert 'private supplier body' not in json.dumps(row)
    client.complete_json.assert_awaited_once()
    index.search_photo_observation.assert_not_called()
    summary = probe.summarize([row])
    assert summary['usage_known_calls'] == 0 and summary['cost'] is None
    assert summary['candidate_top5'] == 0


@pytest.mark.parametrize('text,matched', [
    ('VB\n1695\nC.929-1922', True), ('Museum number: Ｃ．９２９－１９２２', True),
    ('C.929-19223', False), ('C.929-1923', False), ('C.92g-1922', False),
    ('maybe C.929-1922', False), ('C.929', False), ('C.929&A-1922', False),
])
def test_transcription_measure_does_not_correct_or_complete_numbers(text, matched):
    assert probe.number_on_line(text, 'C.929-1922') is matched


def test_private_output_and_exclusive_evidence(tmp_path):
    with pytest.raises(ValueError):
        probe.local_folder(tmp_path)
    path = tmp_path/'started.json'
    probe.write_new(path, {'reserved': True})
    with pytest.raises(FileExistsError):
        probe.write_new(path, {'reserved': False})
    assert json.loads(path.read_bytes()) == {'reserved': True}
