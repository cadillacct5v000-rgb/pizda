import json
from unittest.mock import Mock

import pytest
import requests

import netutil
import pipeline
import storyboard as sb
import model_reply
from test_chat_stream import StreamResponse,event


def http(body, status=200, content_type='application/json'):
    response=requests.Response();response.status_code=status
    response._content=body.encode('utf-8');response._content_consumed=True
    response.headers['Content-Type']=content_type
    return response


def client(monkeypatch, responses):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr(pipeline,'clear_wait',lambda *args:None)
    api=Mock();api.s.headers={'X-API-Key':'fixture-private-key'}
    api.s.post=Mock(side_effect=responses)
    c=pipeline.LLM('fastgen',api,'fixture-model')
    c.retry_network_errors=False;c.stream_responses=True
    return c,api.s.post


def test_bad_model_json_is_repaired_once_then_resumed_without_original_prompt(monkeypatch,tmp_path):
    bad='{"plans": [ {"index":192 "prompt":"Original visual"} ]}'
    good={'plans':[{'index':192,'prompt':'Original visual'}]}
    c,post=client(monkeypatch,[StreamResponse(event(bad,finish='stop')),StreamResponse(event(json.dumps(good),finish='stop'))])
    def query(prompt):return c.chat_json('Return JSON',prompt)
    path=tmp_path/'batch.reply.json';messages=[]
    for _ in range(2):
        assert model_reply.query(query,'large source-shot references',path,'signature',sb.atomic,messages.append)==good
    assert post.call_count==2
    assert 'large source-shot references' in post.call_args_list[0].kwargs['json']['messages'][1]['content']
    assert 'Repair only JSON syntax' in post.call_args_list[1].kwargs['json']['messages'][1]['content']
    assert 'large source-shot references' not in post.call_args_list[1].kwargs['json']['messages'][1]['content']
    assert 'fixture-private-key' not in ''.join(p.read_text() for p in tmp_path.glob('*.json'))
    assert len(messages)==1


def test_api_html_is_not_sent_for_model_repair(monkeypatch,tmp_path):
    c,post=client(monkeypatch,[http('<html>upstream gateway failed fixture-private-key</html>',content_type='text/html')])
    path=tmp_path/'batch.reply.json'
    with pytest.raises(model_reply.APIReplyError,match='HTTP 200, HTML'):
        model_reply.query(lambda prompt:c.chat_json('JSON',prompt),'source',path,'signature',sb.atomic)
    assert post.call_count==1
    receipt=json.loads(path.read_text())
    assert receipt['kind']=='api' and receipt['complete'] is False
    assert 'fixture-private-key' not in path.read_text()


def test_truncation_does_not_fabricate_completion_or_repeat_post(monkeypatch,tmp_path):
    c,post=client(monkeypatch,[StreamResponse(event('{"plans":[]}',finish='length'))])
    path=tmp_path/'batch.reply.json'
    with pytest.raises(model_reply.IncompleteReply):
        model_reply.query(lambda prompt:c.chat_json('JSON',prompt),'source',path,'signature',sb.atomic)
    assert post.call_count==1
    assert json.loads(path.read_text())['complete'] is False


def test_stream_wrong_mime_can_be_decoded_without_another_request(monkeypatch,tmp_path):
    body='\n'.join(event('{"plans":[]}',finish='stop'))+'\n'
    c,post=client(monkeypatch,[http(body)])
    result=model_reply.query(lambda prompt:c.chat_json('JSON',prompt),'source',tmp_path/'batch.reply.json','sig',sb.atomic)
    assert result=={'plans':[]} and post.call_count==1


def test_failed_repair_retains_original_reply_for_manual_resume(monkeypatch,tmp_path):
    good={'plans':[]}
    c,post=client(monkeypatch,[StreamResponse(event('{"plans": [] "bad":false}',finish='stop')),requests.ReadTimeout('lost repair'),StreamResponse(event(json.dumps(good),finish='stop'))])
    query=lambda prompt:c.chat_json('JSON',prompt)
    path=tmp_path/'batch.reply.json'
    with pytest.raises(RuntimeError,match='ReadTimeout'):
        model_reply.query(query,'original source references',path,'sig',sb.atomic)
    assert model_reply.query(query,'original source references',path,'sig',sb.atomic)==good
    assert post.call_count==3
    assert 'original source references' not in post.call_args_list[-1].kwargs['json']['messages'][1]['content']


def test_empty_api_reply_is_separate_from_model_json_error(monkeypatch,tmp_path):
    c,post=client(monkeypatch,[http('')])
    with pytest.raises(model_reply.APIReplyError,match='пустой ответ'):
        model_reply.query(lambda prompt:c.chat_json('JSON',prompt),'source',tmp_path/'batch.reply.json','sig',sb.atomic)
    assert post.call_count==1


def test_197_passages_keep_192_and_repair_only_last_received_batch(monkeypatch,tmp_path):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    script=' '.join(f'Subject number {i} does something.' for i in range(197))
    profile={'montage':{'shots':[{'index':0,'duration':3,'description':'subject action','redesign_prompt':'original scene'}]}}
    calls=[]
    def query(prompt):
        calls.append(prompt)
        if 'Repair only JSON syntax' in prompt:
            units=range(192,197)
        else:
            units=[u['index'] for u in json.loads(prompt.split('Reference JSON:\n')[1])['passages']]
        result={'plans':[{'index':i,'source_shot':0,'confidence':'high','reason':'subject','prompt':'Original action','motion':'static'} for i in units]}
        raw=json.dumps(result)
        if units==list(range(192,197)):
            raw=raw.replace('"plans":', '"plans"',1)
        model_reply.emit('model',raw,True)
        return model_reply.object_from_text(raw)
    board=sb.create(script,profile,query,tmp_path)
    assert len(board['rows'])==197 and len(calls)==26
    assert 'Repair only JSON syntax' in calls[-1]
    assert sb.create(script,profile,query,tmp_path)==board
    assert len(calls)==26


def test_switching_provider_reuses_192_validated_passages_without_queries(monkeypatch,tmp_path):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    script=' '.join(f'Subject number {i} does something.' for i in range(197))
    profile={'montage':{'shots':[{'index':0,'duration':3,'description':'subject action','redesign_prompt':'original scene'}]}}
    def result(units):
        return {'plans':[{'index':i,'source_shot':0,'prompt':'Original action'} for i in units]}
    def old_query(prompt):
        units=[u['index'] for u in json.loads(prompt.split('Reference JSON:\n')[1])['passages']]
        if units[0]==192: raise RuntimeError('old provider failed')
        return result(units)
    old=tmp_path/'old-provider';new=tmp_path/'new-provider'
    with pytest.raises(RuntimeError):sb.create(script,profile,old_query,old)
    new_calls=[]
    def new_query(prompt):
        units=[u['index'] for u in json.loads(prompt.split('Reference JSON:\n')[1])['passages']]
        new_calls.append(units)
        return result(units)
    board=sb.create(script,profile,new_query,new,reuse_from=[old])
    assert len(board['rows'])==197 and new_calls==[list(range(192,197))]
    assert sb.create(script,profile,new_query,new)==board
    assert len(new_calls)==1


def test_foreign_cache_wrong_signature_or_plan_ids_is_not_reused(monkeypatch,tmp_path):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    profile={'montage':{'shots':[{'index':0,'duration':3,'description':'subject','redesign_prompt':'new'}]}}
    query=lambda p:{'plans':[{'index':0,'source_shot':0,'prompt':'original'}]}
    old=tmp_path/'old';sb.create('Short original narration.',profile,query,old)
    for cache in old.glob('*/batch_*.json'):
        value=json.loads(cache.read_text());value['result']['plans'][0]['index']=True;sb.atomic(cache,value)
    calls=Mock(side_effect=query)
    sb.create('Short original narration.',profile,calls,tmp_path/'new',reuse_from=[old])
    assert calls.call_count==1
