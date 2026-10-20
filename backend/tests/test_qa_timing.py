import pytest
from app.museum.qa_timing import AnswerTiming, public_timing


@pytest.mark.asyncio
async def test_failed_and_repeated_calls_are_timed_without_payload():
    clock=AnswerTiming()
    async def error():raise ValueError('private prompt')
    with pytest.raises(ValueError):await clock.run('generation',error())
    async def success():return 'ok'
    assert await clock.run('generation',success())=='ok'
    data=clock.snapshot()
    assert data['calls']['generation']==2 and data['calls']['verification']==0
    assert data['events'][0]['error_type']=='ValueError'
    assert 'private prompt' not in str(data) and data['totals_ms']['generation']>=0


def test_timing_export_does_not_include_unknown_fields_or_text():
    data=dict(version='answer-phases-v1',events=[{'query':'secret'}],totals_ms={'generation':12,'query':'secret'},
        calls={'rewrite':1,'secret':10,'generation':float('inf')})
    result=public_timing(data)
    assert result==dict(version='answer-phases-v1',totals_ms={'generation':12},calls={'rewrite':1})


@pytest.mark.asyncio
async def test_transient_retry_preserves_first_failure_without_message():
    import httpx
    import time
    from app.llm.client import LLMError
    clock = AnswerTiming()
    calls = 0
    async def call():
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                raise httpx.ConnectError('private URL and secret')
            except httpx.ConnectError as cause:
                raise LLMError('private provider error') from cause
        return 'ok'
    assert await clock.run_with_retry('fact_selection', call, time.perf_counter()+30) == 'ok'
    result = clock.snapshot()
    assert calls == 2 and result['retry_used']
    assert result['events'][0]['error_kind'] == 'network'
    assert result['events'][0]['retry_scheduled']
    assert result['retry_delay_ms'] > 0
    assert 'private' not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['schema', 'unauthorized', 'unknown', 'no_budget'])
async def test_no_retry_for_invalid_evidence_permanent_error_or_expired_budget(kind):
    import httpx
    import time
    clock = AnswerTiming()
    calls = 0
    async def call():
        nonlocal calls
        calls += 1
        if kind == 'schema':
            raise ValueError('invalid schema')
        if kind == 'unknown':
            raise RuntimeError('unknown')
        if kind == 'unauthorized':
            response = httpx.Response(401, request=httpx.Request('POST', 'https://example.org'))
            raise httpx.HTTPStatusError('unauthorized', request=response.request, response=response)
        raise httpx.ConnectError('network')
    with pytest.raises(Exception):
        await clock.run_with_retry('fact_selection', call, time.perf_counter()+(2 if kind=='no_budget' else 30))
    assert calls == 1 and not clock.retry_used
