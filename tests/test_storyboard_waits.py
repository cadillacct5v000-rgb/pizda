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
    assert client.s.post.call_args.kwargs['timeout']==(300,300)
    assert 'до получения заголовков' in str(caught.value)


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


def test_slow_https_proxy_uses_connect_budget_not_model_read_budget(monkeypatch):
    """Reproduce urllib3's ReadTimeout(connect) at a real local CONNECT proxy."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    calls=[]
    class Proxy(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            calls.append(self.path)
            time.sleep(.2)
            # Stop at the proxy: never connect to any real model provider.
            try:self.send_error(502,'Local proxy fixture')
            except (OSError,ConnectionError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Proxy)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr('endpoints.base',lambda provider:'https://model.example.invalid/v1')
    client=OpenAIClient('fixture-secret',provider='elysium',stream=True)
    client.s.trust_env=False
    client.s.proxies={'https':f'http://127.0.0.1:{server.server_port}'}
    client.STREAM_READ_TIMEOUT=4
    try:
        # Scale the previous 15-second connect limit down for this local test.
        client.STREAM_CONNECT_TIMEOUT=.04
        with pytest.raises(OpenAIError,match='ReadTimeout'):
            client.chat_json('JSON','fixture',model='anthropic/claude-opus-4.6')
        client.STREAM_CONNECT_TIMEOUT=2
        # The same delayed proxy response now arrives inside the connect budget.
        # It intentionally refuses the tunnel with 502, rather than timing out.
        with pytest.raises(OpenAIError,match='ProxyError') as caught:
            client.chat_json('JSON','fixture',model='anthropic/claude-opus-4.6')
        assert 'ReadTimeout' not in str(caught.value)
        assert len(calls)==2  # one CONNECT for each explicit call, no replay
    finally:
        client.s.close();server.shutdown();server.server_close();thread.join(timeout=2)


def test_interrupted_script_stream_keeps_receipt_and_reuses_ready_sections(tmp_path,monkeypatch):
    import studio
    from test_chat_stream import StreamResponse,event
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    first=StreamResponse(event('{"script":"Ready first section."}',finish='stop'))
    interrupted=StreamResponse(event('{"script":"Partial next section')+[
        requests.exceptions.ChunkedEncodingError('Response ended prematurely')])
    second=StreamResponse(event('{"script":"Ready second section."}',finish='stop'))
    transport=OpenAIClient('fixture-secret',provider='elysium',stream=True)
    transport.s.post=Mock(side_effect=[first,interrupted,second])
    client=pipeline.LLM('elysium',transport,'anthropic/claude-opus-4.6')
    a,b=tmp_path/'first.json',tmp_path/'second.json'
    assert studio._query(client,'section one',a)=={'script':'Ready first section.'}
    with pytest.raises(OpenAIError,match='ChunkedEncodingError.*при чтении потока'):
        studio._query(client,'section two',b)
    assert transport.s.post.call_count==2 and interrupted.closed
    receipt=json.loads(b.with_suffix('.reply.json').read_text())
    assert receipt['complete'] is False and receipt['text']=='{"script":"Partial next section'
    assert not b.exists()
    # Explicitly resume: the ready section is not sent again; partial JSON is
    # never mistaken for a complete script or passed to synthesis.
    assert studio._query(client,'section one',a)=={'script':'Ready first section.'}
    assert transport.s.post.call_count==2
    assert studio._query(client,'section two',b)=={'script':'Ready second section.'}
    assert transport.s.post.call_count==3


def test_wait_reports_progress_before_headers_without_resubmitting(monkeypatch):
    import threading
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    release=threading.Event();response=Mock();calls=[];messages=[]
    def request():
        calls.append('POST')
        assert release.wait(3),'No progress while server was silent'
        return response
    def progress(message):
        messages.append(message);release.set()
    assert netutil.response_once(request,progress,interval=.01) is response
    assert calls==['POST'] and 'заголовки' in messages[0]
    response.close.assert_not_called()


def test_stop_during_header_wait_closes_late_response_and_never_replays(monkeypatch):
    import threading
    entered,release,closed=threading.Event(),threading.Event(),threading.Event()
    response=Mock();response.close.side_effect=closed.set;calls=[]
    class Cancelled(Exception):pass
    def check():
        if entered.is_set():raise Cancelled('local stop')
    def request():
        calls.append('POST');entered.set()
        assert release.wait(3)
        return response
    monkeypatch.setattr(netutil,'CANCEL_CHECK',check)
    try:
        with pytest.raises(Cancelled):netutil.response_once(request)
    finally:release.set()
    assert closed.wait(3)
    assert calls==['POST'] and response.close.call_count==1


def test_real_delayed_headers_complete_once_with_longer_budget(monkeypatch):
    """Real local HTTP request, scaled timeouts; no provider traffic or billing."""
    import threading
    import time
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            time.sleep(.2)
            try:
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}\n\n')
                self.wfile.flush()
            except (OSError,ConnectionError):pass
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    monkeypatch.setattr('endpoints.base',lambda provider:f'http://127.0.0.1:{server.server_port}/v1')
    client=OpenAIClient('local-fixture',provider='elysium',stream=True);client.s.trust_env=False
    client.STREAM_CONNECT_TIMEOUT=client.STREAM_READ_TIMEOUT=2
    try:
        assert client.chat_json('JSON','fixture',model='anthropic/claude-opus-4.6')=={'ok':True}
        assert calls==['/v1/chat/completions']
    finally:
        client.s.close();server.shutdown();server.server_close();worker.join(timeout=2)


def test_analysis_stream_has_same_tunnel_budget_and_no_timeout_replay(monkeypatch):
    import source_analysis as analysis
    monkeypatch.setattr(netutil,'CANCEL_CHECK',None)
    request=Mock(side_effect=requests.ReadTimeout('fixture-private'))
    monkeypatch.setattr(analysis.requests,'request',request)
    with pytest.raises(analysis.SourceAnalysisError):
        analysis._http('POST','https://model.example.invalid/v1/chat/completions',
                       headers={'Authorization':'Bearer fixture-private'},json={'stream':True})
    assert request.call_count==1
    assert request.call_args.kwargs['timeout']==(300,300)
