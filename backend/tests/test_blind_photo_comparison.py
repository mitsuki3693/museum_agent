import copy
import json

import pytest

from app.museum.blind_photo_comparison import blind_messages, restore_ids, REFERENCE_PREFIX


def messages():
    return [dict(role='system',content='unchanged comparison protocol'),dict(role='user',content=[
        dict(type='text',text='待识别的游客照片：'),
        dict(type='image_url',image_url={'url':'data:image/jpeg;base64,cXVlcnk='}),
        dict(type='text',text=json.dumps([dict(id='named-neptune',title='Neptune',accession_number='A.123-2000'),
                                       dict(id='named-samson',title='Samson',accession_number='B.456-1900')])),
        dict(type='text',text=REFERENCE_PREFIX+'named-neptune'),
        dict(type='image_url',image_url={'url':'data:image/jpeg;base64,cmVmZXJlbmNl'})])]


def test_blinding_preserves_prompt_pixels_order_and_original():
    original=messages(); saved=copy.deepcopy(original)
    blinded,mapping=blind_messages(original)
    assert original==saved and blinded[0]==original[0]
    assert blinded[1]['content'][:2]==original[1]['content'][:2]
    assert blinded[1]['content'][4]==original[1]['content'][4]
    assert mapping=={'C01':'named-neptune','C02':'named-samson'}
    text=' '.join(b['text'] for b in blinded[1]['content'] if b['type']=='text')
    assert all(s not in text for s in ['Neptune','Samson','named-neptune','named-samson','A.123-2000','B.456-1900'])
    assert json.loads(blinded[1]['content'][2]['text'])==[{'id':'C01'},{'id':'C02'}]


@pytest.mark.parametrize('mutation',['extra_block','unexpected_label','extra_metadata','duplicate_id','wrong_reference'])
def test_unknown_payload_shape_fails_closed(mutation):
    value=messages(); content=value[1]['content']
    if mutation=='extra_block':content.append(dict(type='text',text='Leaking title'))
    elif mutation=='unexpected_label':content[3]['text']='Free-form title: Neptune'
    elif mutation=='wrong_reference':content[3]['text']=REFERENCE_PREFIX+'not-in-candidates'
    else:
        records=json.loads(content[2]['text'])
        if mutation=='extra_metadata':records[0]['description']='Hidden identity clues'
        else:records[1]['id']=records[0]['id']
        content[2]['text']=json.dumps(records)
    with pytest.raises(ValueError):blind_messages(value)


def test_restore_only_ids_leaving_evidence_and_policy_inputs_unchanged():
    response={'comparisons':[{'candidate_id':'C01','identity':'uncertain','features':[{'relation':'different'}]}]}
    result=restore_ids(response,{'C01':'named-neptune'})
    assert response['comparisons'][0]['candidate_id']=='C01'
    assert result['comparisons'][0]==dict(response['comparisons'][0],candidate_id='named-neptune')


@pytest.mark.parametrize('rows',[[{'candidate_id':'real-source-id'}],[{'candidate_id':'C01'},{'candidate_id':'C01'}]])
def test_unknown_or_duplicate_ids_not_silently_accepted(rows):
    with pytest.raises(ValueError):restore_ids({'comparisons':rows},{'C01':'named-neptune'})
