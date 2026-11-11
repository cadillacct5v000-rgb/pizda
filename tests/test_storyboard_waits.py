import json
from unittest.mock import Mock

import pytest
import requests

import app
import netutil
import pipeline
import storyboard as sb
from openai_api import OpenAIClient,OpenAIError


def test_storyboard_fastgen_quota_is_bounded_and_visible(monkeypatch):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr(pipeline,'log',lambda *args:None)
    monkeypatch.setattr(pipeline,'set_wait',lambda *args:None)
    cleared=[]
    monkeypatch.setattr(pipeline,'clear_wait',cleared.append)
    monkeypatch.setattr(pipeline.time,'sleep',lambda seconds:None)
    api=Mock();api.s.post.return_value=Mock(status_code=429,text="rate limit")
    client=pipeline.LLM('fastgen',api,'fixture-model');client.max_limit_waits=2
    messages=[];client.progress=lambda stage,done,total,message:messages.append(message)
    with pytest.raises(RuntimeError,match='Готовые пачки сохранены'):
        client.chat_json('system','user')
    assert api.s.post.call_count==3 and len(messages)==2
    assert 'лимит' in messages[0] and 'llm' in cleared
    assert pipeline.LLM('fastgen',api).max_limit_waits==70


def test_197_passage_final_batch_failure_resumes_192_saved_passages(tmp_path,monkeypatch):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    script=' '.join(f'Subject number {i} does something.' for i in range(197))
    profile={'montage':{'shots':[{'index':0,'duration':3,'description':'subject action','redesign_prompt':'original scene'}]}}
    batches=[];fail=[True]
    def query(prompt):
        units=json.loads(prompt.split('Reference JSON:\n')[1])['passages']
        batches.append([unit['index'] for unit in units])
        if units[0]['index']==192 and fail[0]:
            raise RuntimeError('last batch quota')
        return {'plans':[{'index':unit['index'],'source_shot':0,'confidence':'high','reason':'subject','prompt':'Original action','motion':'static'} for unit in units]}
    with pytest.raises(RuntimeError,match='last batch quota'):
        sb.create(script,profile,query,tmp_path)
    assert len(batches)==25 and batches[-1]==list(range(192,197))
    fail[0]=False;progress=[]
    board=sb.create(script,profile,query,tmp_path,progress.append)
    assert len(board['rows'])==197 and len(batches)==26
    assert progress[-1]=='раскадровка: сохранено 197/197 фраз'


def test_storyboard_stream_request_is_not_retried_after_read_timeout(monkeypatch):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    client=OpenAIClient('fixture-private',provider='elysium',stream=True,progress=Mock())
    client.s.post=Mock(side_effect=requests.ReadTimeout('fixture-private'))
    with pytest.raises(OpenAIError,match='ReadTimeout') as caught:
        client.chat_json('JSON','user',model='gpt-5.4')
    assert client.s.post.call_count==1
    assert 'fixture-private' not in str(caught.value)
    assert client.s.post.call_args.kwargs['stream'] is True


def test_storyboard_stream_finishes_without_polling_unknown_board(monkeypatch):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    from test_chat_stream import StreamResponse,event
    response=StreamResponse(event('{"plans":[]}',finish='stop')+[requests.ReadTimeout('unread tail')])
    client=OpenAIClient('fixture',provider='elysium',stream=True)
    client.s.post=Mock(return_value=response)
    assert client.chat_json('JSON','user',model='gpt-5.4')=={'plans':[]}
    assert response.closed and client.s.post.call_count==1


def test_no_saved_board_is_actionable_not_invalid_identifier(monkeypatch):
    monkeypatch.setattr(app,'load_cfg',lambda:{})
    monkeypatch.setitem(app.STATE,'storyboard_id',None)
    monkeypatch.setitem(app.STATE,'preparing',False)
    result=app.Api().get_storyboard()
    assert not result['ok'] and 'Сохранённой раскадровки пока нет' in result['error']
    monkeypatch.setitem(app.STATE,'preparing',True)
    assert app.Api().get_storyboard()['error']=='Раскадровка ещё создаётся.'


def test_storyboard_fastgen_network_timeout_is_not_replayed(monkeypatch):
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr(pipeline,'clear_wait',lambda *args:None)
    api=Mock();api.s.post.side_effect=requests.ReadTimeout('response lost')
    client=pipeline.LLM('fastgen',api);client.retry_network_errors=False;client.request_timeout=(15,180)
    with pytest.raises(RuntimeError,match='ReadTimeout.*Готовые пачки сохранены'):
        client.chat_json('system','user')
    assert api.s.post.call_count==1
    assert api.s.post.call_args.kwargs['timeout']==(15,180)
