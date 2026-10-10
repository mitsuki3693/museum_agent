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
