import json
from unittest.mock import Mock

import pytest
from PIL import Image

from model_json import extract_object, recover_object
import source_analysis as analysis


@pytest.mark.parametrize('text,expected', [
    ('\ufeff{"note":"a {brace}, } and \\\"quote\\\""}', {'note':'a {brace}, } and "quote"'}),
    ('Answer:\n```json\n{"note":"line one\nline two","colors":["red",],}\n```\nDone.', {'note':'line one\nline two','colors':['red']}),
    ('{"note":"literal comma, } stays"} Explanation.', {'note':'literal comma, } stays'}),
    ([{'type':'text','text':'{"ok":'}, {'type':'text','text':'true}'}], {'ok':True}),
])
def test_safe_json_wrappers_preserve_values(text, expected):
    assert extract_object(text) == expected


@pytest.mark.parametrize('text', [
    '{"note":"incomplete', '{"one":{"nested":true}',
    '{"one":1} {"two":2}', '[{"one":1}]',
    '```json\n{"one":1}\n```\n```json\n{"two":2}\n```',
    '{"value":NaN}', '{"value":Infinity}',
])
def test_no_fabricated_completion_or_ambiguous_json(text):
    with pytest.raises(json.JSONDecodeError):
        extract_object(text)


def reply(text, reason='stop'):
    return {'choices':[{'finish_reason':reason,'message':{'content':text}}]}


BAD_QUOTING = '{"narrative_style":"A "quoted" sentence"}'
FIXED_QUOTING = json.dumps({'narrative_style':'A "quoted" sentence'})


def invoke(tmp_path, image=None, validate=None, progress=None):
    return analysis.cached_query(tmp_path,'elysium','not-a-real-key','gpt-5.4','source fixture',image,validate=validate,progress=progress)


def test_one_text_only_repair_then_checkpoint_no_second_image_call(monkeypatch,tmp_path):
    image=tmp_path/'source.jpg';Image.new('RGB',(20,20)).save(image)
    post=Mock(side_effect=[reply(BAD_QUOTING),reply(FIXED_QUOTING)])
    monkeypatch.setattr(analysis,'_http',post)
    messages=[]
    assert invoke(tmp_path,image,analysis._narrative,messages.append)['narrative_style']=='A "quoted" sentence'
    assert post.call_count==2
    first,second=[call.kwargs['json'] for call in post.call_args_list]
    assert first['response_format']=={'type':'json_object'}
    assert len(first['messages'][1]['content'])==2
    assert len(second['messages'][1]['content'])==1
    assert 'Repair only the syntax' in second['messages'][1]['content'][0]['text']
    assert messages and 'без повторной отправки кадров' in messages[0]
    assert invoke(tmp_path,image,analysis._narrative)['narrative_style']=='A "quoted" sentence'
    assert post.call_count==2
    assert 'not-a-real-key' not in ''.join(p.read_text() for p in (tmp_path/'responses').glob('*.json'))


def test_complete_raw_reply_survives_crash_before_validated_checkpoint(monkeypatch,tmp_path):
    image=tmp_path/'source.jpg';Image.new('RGB',(20,20)).save(image)
    post=Mock(return_value=reply('{"narrative_style":"A"}'))
    monkeypatch.setattr(analysis,'_http',post)
    def crashed_validation(value):
        raise RuntimeError('simulated process failure after paid response')
    with pytest.raises(RuntimeError):
        invoke(tmp_path,image,crashed_validation)
    assert post.call_count==1
    assert invoke(tmp_path,image,analysis._narrative)=={'narrative_style':'A'}
    assert post.call_count==1


def test_failed_repair_resumes_from_original_text_not_images(monkeypatch,tmp_path):
    image=tmp_path/'source.jpg';Image.new('RGB',(20,20)).save(image)
    post=Mock(side_effect=[reply(BAD_QUOTING),analysis.SourceAnalysisError('temporary',503),reply(FIXED_QUOTING)])
    monkeypatch.setattr(analysis,'_http',post)
    with pytest.raises(analysis.SourceAnalysisError,match='temporary'):
        invoke(tmp_path,image,analysis._narrative)
    assert invoke(tmp_path,image,analysis._narrative)['narrative_style']=='A "quoted" sentence'
    contents=[call.kwargs['json']['messages'][1]['content'] for call in post.call_args_list]
    assert [len(content) for content in contents]==[2,1,1]


@pytest.mark.parametrize('reason,text',[('length','{"narrative_style":"partial"}'),('content_filter',''),('stop','')])
def test_incomplete_refused_and_empty_replies_are_not_repaired(monkeypatch,tmp_path,reason,text):
    post=Mock(return_value=reply(text,reason))
    monkeypatch.setattr(analysis,'_http',post)
    with pytest.raises(analysis.SourceAnalysisError):
        invoke(tmp_path,validate=analysis._narrative)
    assert post.call_count==1


def test_bad_schema_is_not_committed_or_replayed_forever(monkeypatch,tmp_path):
    post=Mock(side_effect=[reply('{"unrelated":1}'),reply('{"narrative_style":"A"}')])
    monkeypatch.setattr(analysis,'_http',post)
    with pytest.raises(analysis.SourceAnalysisError):
        invoke(tmp_path,validate=analysis._narrative)
    assert invoke(tmp_path,validate=analysis._narrative)=={'narrative_style':'A'}
    assert post.call_count==2


def test_content_blocks_normalized_before_capture(monkeypatch,tmp_path):
    post=Mock(return_value=reply([{'type':'text','text':'{"narrative_style":"A"}'}]))
    monkeypatch.setattr(analysis,'_http',post)
    assert invoke(tmp_path,validate=analysis._narrative)=={'narrative_style':'A'}
    assert post.call_count==1


@pytest.mark.parametrize('text,expected',[
    ('{"note":"A" "items":[1 2 {"x":true "y":null}]}',{'note':'A','items':[1,2,{'x':True,'y':None}]}),
    ("{'note':'Русский кит', 'items':['ro' 'es' 'pt']}",{'note':'Русский кит','items':['ro','es','pt']}),
    (r'{"note":"literal \_ and \( stay"}',{'note':r'literal \_ and \( stay'}),
    ('```json\n{"note":"line one\nline two" "items":[]}\n```',{'note':'line one\nline two','items':[]}),
])
def test_local_recovery_preserves_all_values(text,expected):
    assert recover_object(text)==expected


@pytest.mark.parametrize('text',[
    '{"note":"unfinished', '{"items":[1 2}', '{"one":{"a":1}',
    '{"note":}', '{"a":NaN "b":1}', '{"a":1 "a":2}',
    BAD_QUOTING, '{"a":1} {"b":2}', '[{"a":1}]',
    '```json\n{"a":1}\n```\n```json\n{"b":2}\n```',
])
def test_local_recovery_does_not_guess_missing_data(text):
    with pytest.raises(ValueError):recover_object(text)


def test_local_analysis_repair_uses_no_extra_paid_request(monkeypatch,tmp_path):
    post=Mock(return_value=reply('{"narrative_style":"A" "extra":[1 2]}'))
    monkeypatch.setattr(analysis,'_http',post)
    assert invoke(tmp_path,validate=analysis._narrative)=={'narrative_style':'A','extra':[1,2]}
    assert invoke(tmp_path,validate=analysis._narrative)['narrative_style']=='A'
    assert post.call_count==1


def test_bad_first_format_repair_recovers_automatically_without_resending_image(monkeypatch,tmp_path):
    image=tmp_path/'source.jpg';Image.new('RGB',(20,20)).save(image)
    post=Mock(side_effect=[reply(BAD_QUOTING),reply(BAD_QUOTING),reply(FIXED_QUOTING)])
    monkeypatch.setattr(analysis,'_http',post)
    assert invoke(tmp_path,image,analysis._narrative)['narrative_style']=='A "quoted" sentence'
    assert [len(c.kwargs['json']['messages'][1]['content']) for c in post.call_args_list]==[2,1,1]
    assert invoke(tmp_path,image,analysis._narrative)['narrative_style']=='A "quoted" sentence'
    assert post.call_count==3


def test_completed_bad_repairs_are_bounded_across_restarts(monkeypatch,tmp_path):
    post=Mock(side_effect=[reply(BAD_QUOTING),reply(BAD_QUOTING),reply(BAD_QUOTING)])
    monkeypatch.setattr(analysis,'_http',post)
    for _ in range(2):
        with pytest.raises(analysis.SourceAnalysisError,match='двух текстовых'):
            invoke(tmp_path,validate=analysis._narrative)
    assert post.call_count==3
    assert len(list((tmp_path/'responses').glob('*.reply.json')))==3
