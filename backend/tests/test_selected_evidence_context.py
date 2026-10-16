"""Do not present disjoint catalogue spans as a continuous quotation."""
import pytest
from app.museum.fact_selection import FactSelection, selected_sources


@pytest.mark.parametrize('gap', ['\n', '\nUnselected: other record detail.\n'])
def test_context_preserves_real_contiguity(gap):
    first, last = 'Maker: Example factory', 'Date: 1750'
    source = {'_id': 's', 'content': first + gap + last}
    selection = FactSelection(facts=[
        dict(aspect='maker', scope='production', source_id='s', value='Example factory', quote=first),
        dict(aspect='date', scope='production', source_id='s', value='1750', quote=last),
    ])
    context = selected_sources(selection, [source])[0]
    if gap.strip():
        assert first + '\n' + last not in context['content']
        assert context['evidence_spans'] == [first, last]
        assert 'Unselected' not in context['content']
    else:
        assert context['evidence_spans'] == [source['content']]


def test_context_orders_and_merges_overlap_without_rewriting_source():
    source = {'_id': 's', 'content': 'Material: bronze. Date: 1750. Other: excluded.'}
    selection = FactSelection(facts=[
        dict(aspect='date', scope='production', source_id='s', value='1750', quote='bronze. Date: 1750.'),
        dict(aspect='material', scope='production', source_id='s', value='bronze', quote='Material: bronze.'),
    ])
    context = selected_sources(selection, [source])[0]
    assert context['evidence_spans'] == ['Material: bronze. Date: 1750.']
    assert 'excluded' not in context['content']
