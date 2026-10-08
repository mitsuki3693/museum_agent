from app.museum.fallback_glossary import expand_query
from app.museum.config import MuseumSettings


def test_expansion_is_retrieval_text_with_explicit_provenance():
    expanded,audit=expand_query('黑色石膏胸像')
    assert all(word in expanded.split() for word in ['black','plaster','bust'])
    assert audit['matched']=={'黑色':'black','石膏':'plaster','胸像':'bust'}
    assert 'va-' not in expanded and 'bust' in expanded


def test_no_matching_terms_and_overlapping_aliases():
    expanded,audit=expand_query('一个陌生作品')
    assert audit['matched']=={} and '陌生' in expanded
    expanded,_=expand_query('半身像和胸像')
    assert expanded.split().count('bust')==0
    assert MuseumSettings(_env_file=None).museum_fallback_glossary is False


def test_single_broad_cue_does_not_replace_precise_chinese_retrieval():
    expanded,audit=expand_query('画着蓝色郁金香和人物、手柄上有孔的壶')
    assert not audit['applied'] and 'blue' not in expanded.split()
    expanded,audit=expand_query('找带镀金装饰的作品')
    assert not audit['applied'] and 'gilt' not in expanded.split()
