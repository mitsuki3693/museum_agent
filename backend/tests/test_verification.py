import hashlib
import pytest
from pydantic import ValidationError
from app.museum.verification import LEGACY_PROMPT, StructuredReview, review_verdict, verify_claims


def review():
    return {'checks':[{'index':0,'supported':True,'reason':'Evidence supports the claim.'}],
            'answers_question':True,'relevance_reason':'Answers the question.',
            'respects_identity':True,'identity_reason':'No photo identity assertion.'}


def test_legacy_prompt_is_byte_identical():
    assert hashlib.sha256(LEGACY_PROMPT.encode()).hexdigest() == 'd2665c3ffed12369724623ed695771c8babdb0c8ac84e318da1f5b2486a3780b'


@pytest.mark.parametrize('failure', [None, 'claim', 'answers_question', 'respects_identity'])
def test_python_requires_all_three_dimensions(failure):
    value = review()
    if failure == 'claim':
        value['checks'][0]['supported'] = False
    elif failure:
        value[failure] = False
    verdict = review_verdict(StructuredReview.model_validate(value), 1)
    assert verdict.passed == (failure is None)
    assert bool(verdict.issues) == (failure is not None)


@pytest.mark.parametrize('indices', [[0], [0,0], [0,2], [0,1,2]])
def test_missing_duplicate_or_extra_checks_cannot_pass(indices):
    value = review()
    value['checks'] = [{**value['checks'][0], 'index':i} for i in indices]
    with pytest.raises(ValueError):
        review_verdict(StructuredReview.model_validate(value), 2)


@pytest.mark.parametrize('field', ['supported','answers_question','respects_identity'])
def test_string_boolean_cannot_be_coerced(field):
    value = review()
    target = value['checks'][0] if field == 'supported' else value
    target[field] = 'false'
    with pytest.raises(ValidationError):
        StructuredReview.model_validate(value)


@pytest.mark.asyncio
async def test_unknown_policy_does_not_call_provider():
    class NeverCalled:
        async def complete_json(self, messages):
            pytest.fail('Unknown policy reached provider')
    with pytest.raises(ValueError):
        await verify_claims(NeverCalled(), 'question', '', [], 'unknown')


@pytest.mark.asyncio
async def test_legacy_payload_and_result_remain_compatible():
    class Client:
        async def complete_json(self, messages):
            assert messages[0]['content'] == LEGACY_PROMPT
            assert messages[1]['content'] == '{"question": "q", "identity_boundary": "", "claims": []}'
            return {'passed':False,'issues':['unsupported']}
    verdict, details = await verify_claims(Client(), 'q', '', [])
    assert not verdict.passed and details is None
