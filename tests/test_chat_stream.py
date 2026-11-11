import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
import requests

import chat_stream
import source_analysis as analysis


def event(content=None, finish=None, **extra):
    delta = {} if content is None else {'content':content}
    delta.update(extra)
    return ['data: '+json.dumps({'choices':[{'index':0,'delta':delta,'finish_reason':finish}]},ensure_ascii=False), '']


class StreamResponse:
    status_code=200
    headers={'Content-Type':'text/event-stream; charset=utf-8'}
    def __init__(self, lines):
        self.lines=lines
        self.closed=False
    def iter_lines(self, **kwargs):
        for line in self.lines:
            if isinstance(line,Exception):
                raise line
            yield line.encode('utf-8')
    def close(self):
        self.closed=True


def test_stream_assembles_unicode_ignores_reasoning_and_finishes_without_eof():
    lines=[': keepalive','']+event(None,reasoning_content='private reasoning')+event('{"note":"')+event('Привет 🌍"}')+event(finish='stop')+[requests.ReadTimeout('tail must not be read')]
    response=StreamResponse(lines)
    result=chat_stream.read(response)
    assert result['choices'][0]['message']['content']=='{"note":"Привет 🌍"}'
    assert result['choices'][0]['finish_reason']=='stop'


def test_done_can_complete_gateway_stream_without_finish_reason():
    response=StreamResponse(event('{"ok":true}')+['data: [DONE]',''])
    assert chat_stream.read(response)['choices'][0]['message']['content']=='{"ok":true}'


def test_partial_reply_does_not_become_json_or_paid_retry(monkeypatch,tmp_path):
    response=StreamResponse(event('{"narrative_style":"partial"}')+[requests.ConnectionError('connection lost')])
    post=Mock(return_value=response)
    monkeypatch.setattr(analysis.requests,'request',post)
    with pytest.raises(analysis.SourceAnalysisError,match='ConnectionError.*списание.*неизвестны'):
        analysis.cached_query(tmp_path,'elysium','fixture-key','gpt-5.4','fixture',validate=analysis._narrative)
    assert post.call_count==1 and response.closed
    assert post.call_args.kwargs['stream'] is True
    assert post.call_args.kwargs['json']['stream'] is True
    stored=json.loads(next((tmp_path/'responses').glob('*.reply.json')).read_text())
    assert stored['complete'] is False
    assert stored['text']=='{"narrative_style":"partial"}'
    assert 'fixture-key' not in json.dumps(stored)


def test_complete_stream_is_cached_and_not_repeated(monkeypatch,tmp_path):
    response=StreamResponse(event('{"narrative_style":"complete"}',finish='stop'))
    post=Mock(return_value=response)
    monkeypatch.setattr(analysis.requests,'request',post)
    for _ in range(2):
        assert analysis.cached_query(tmp_path,'elysium','fixture','gpt-5.4','fixture',validate=analysis._narrative)=={'narrative_style':'complete'}
    assert post.call_count==1 and response.closed


@pytest.mark.parametrize('lines',[
    event('{"valid":true}'), # Balanced JSON is not proof of stream completion.
    ['data: not json',''],
    ['data: '+json.dumps({'error':{'message':'private server details'}}),''],
    event('x',finish='unknown'),
])
def test_incomplete_and_malformed_streams_are_terminal(lines):
    with pytest.raises(chat_stream.StreamError):
        chat_stream.read(StreamResponse(lines))


def test_heartbeat_progress_and_deadline(monkeypatch):
    tick=[0]
    def clock():
        tick[0]+=10
        return tick[0]
    monkeypatch.setattr(chat_stream,'time',SimpleNamespace(monotonic=clock))
    progress=[]
    with pytest.raises(chat_stream.StreamError,match='лимит потока'):
        chat_stream.read(StreamResponse([': heartbeat']*20),progress.append,deadline=40)
    assert any('сервер на связи' in item for item in progress)


def test_real_http_sse_heartbeats_keep_idle_timeout_alive():
    failures=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            try:
                for _ in range(8):
                    self.wfile.write(b': ping\n\n');self.wfile.flush();time.sleep(0.03)
                data='\n'.join(event('{"ok":true}',finish='stop'))+'\n'
                self.wfile.write(data.encode());self.wfile.flush()
            except Exception as error:
                failures.append(error)
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with requests.post(f'http://127.0.0.1:{server.server_port}/chat',json={'stream':True},stream=True,timeout=(2,0.15)) as response:
            assert chat_stream.read(response)['choices'][0]['message']['content']=='{"ok":true}'
        assert not failures
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_read_timeout_diagnostic_elapsed_type_and_secret_redaction(monkeypatch):
    post=Mock(side_effect=requests.ReadTimeout('key=private-fixture-secret; idle read'))
    monkeypatch.setattr(analysis.requests,'request',post)
    clock=iter([100,142])
    monkeypatch.setattr(analysis,'time',SimpleNamespace(monotonic=lambda:next(clock)))
    with pytest.raises(analysis.SourceAnalysisError) as caught:
        analysis._http('POST','https://elysiumai.garden/v1/chat/completions',headers={'Authorization':'Bearer private-fixture-secret'},json={'model':'gpt-5.4','stream':True})
    assert 'ReadTimeout · 42 с' in str(caught.value)
    assert 'private-fixture-secret' not in str(caught.value)
    assert post.call_count==1


def test_tiny_heartbeat_is_delivered_before_server_finishes(monkeypatch):
    received=threading.Event()
    failures=[]
    ticks=iter(range(0,900,20))
    monkeypatch.setattr(chat_stream,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length',0)))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            self.wfile.write(b': ping\n\n');self.wfile.flush()
            if not received.wait(2):
                failures.append('heartbeat buffered until completion')
            self.wfile.write(('\n'.join(event('{"ok":true}',finish='stop'))+'\n').encode());self.wfile.flush()
        def log_message(self,*args):
            pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        def progress(message):
            if 'сервер на связи' in message:
                received.set()
        with requests.post(f'http://127.0.0.1:{server.server_port}/chat',json={},stream=True,timeout=(2,3)) as response:
            assert chat_stream.read(response,progress)['choices'][0]['message']['content']=='{"ok":true}'
        assert not failures
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_cancelled_stream_saves_partial_and_is_not_retried(monkeypatch,tmp_path):
    class Cancelled(Exception):
        pass
    response=StreamResponse(event('partial')+[': next heartbeat'])
    post=Mock(return_value=response)
    monkeypatch.setattr(analysis.requests,'request',post)
    checks=[0]
    def cancel():
        checks[0]+=1
        # cached_query and _http also check cancellation before stream starts.
        if checks[0]==6:
            raise Cancelled()
    monkeypatch.setattr('netutil.CANCEL_CHECK',cancel)
    with pytest.raises(Cancelled):
        analysis.cached_query(tmp_path,'elysium','fixture','gpt-5.4','fixture')
    assert post.call_count==1 and response.closed
