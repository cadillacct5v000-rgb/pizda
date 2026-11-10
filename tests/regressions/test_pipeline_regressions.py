import concurrent.futures
import json
from pathlib import Path
import shutil
import subprocess
import threading
from unittest.mock import Mock

import pytest
import requests
from PIL import Image

import app
import fastgen_api
import montage
import pipeline
import source_analysis as sa


def response(status=200, data=None):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(data or {}).encode()
    r._content_consumed = True
    return r


def test_fastgen_cancellation_endpoint():
    client = fastgen_api.FastGen('test-key')
    client.s.post = Mock(return_value=response())
    client.cancel('generation-123')
    assert client.s.post.call_args.args[0].endswith('/api/v6/operations/cancel')
    assert client.s.post.call_args.kwargs['json'] == {'operation_ids':['generation-123']}

@pytest.mark.parametrize('status', ['canceled','cancelled'])
def test_fastgen_terminal_cancellation(status):
    client = fastgen_api.FastGen('test-key')
    client.status = Mock(return_value={'status': status})
    assert client.wait('123')['status'] == status
    assert client.status.call_count == 1


def test_cancel_propagates_through_quality_review():
    client = Mock()
    client.see_json.side_effect = pipeline.Cancelled('Stopped')
    with pytest.raises(pipeline.Cancelled):
        pipeline.qa_check(client, {}, 'file.png')


def test_background_tasks_drained_before_failure():
    finished = threading.Event()
    def work():
        try:
            while True:
                pipeline.check_cancel()
                finished.wait(.01)
        finally:
            finished.set()
    with pytest.raises(ValueError):
        with pipeline.managed_pools() as pools:
            pool = concurrent.futures.ThreadPoolExecutor(1)
            pools.append(pool)
            pool.submit(work)
            raise ValueError('original failure')
    assert finished.is_set()


def gate():
    api = Mock()
    api.usage.return_value = {'account_limits': {'img_gen_per_hour_limit': 100, 'img_generation_threads_allowed': 1}}
    return api, pipeline.RateGate(api)


def test_upstream_account_outage_shared_between_scenes(tmp_path):
    api, limiter = gate()
    api.run.return_value = {'status':'failed','error_code':'auth.accounts_exhausted','error':'No accounts'}
    for i in range(2):
        assert pipeline.gen_fastgen_image(api, limiter, {'index':i,'prompt':'test'}, str(tmp_path/'img.png'), lambda *a:None, [], [('flower_image_generate',3)]) is None
    assert api.run.call_count == 1


def test_auth_failure_never_switches_model(tmp_path):
    api, limiter = gate()
    api.run.side_effect = requests.HTTPError('401 Unauthorized')
    assert pipeline.gen_fastgen_image(api, limiter, {'index':0,'prompt':'test'}, str(tmp_path/'img.png'), lambda *a:None, [], [('one',3),('two',3)]) is None
    assert api.run.call_count == 1


def test_safety_rejection_does_not_fallback(tmp_path):
    api, limiter = gate()
    api.run.return_value = {'status':'failed','error_code':'generation.content_policy','error':'safety filters'}
    with pytest.raises(pipeline.SceneRejected):
        pipeline.gen_fastgen_image(api, limiter, {'index':0,'prompt':'test'}, str(tmp_path/'img.png'), lambda *a:None, [], [('one',3),('two',3)])
    assert api.run.call_count == 1


def test_style_changes_keep_separate_paid_artifacts(tmp_path):
    a,_ = pipeline.visual_job_dir(str(tmp_path), 'oil', 'speed', False, False)
    (Path(a)/'images').mkdir()
    (Path(a)/'images/0000.png').write_bytes(b'paid image')
    b,_ = pipeline.visual_job_dir(str(tmp_path), 'noir', 'speed', False, False)
    assert a != b
    assert (Path(a)/'images/0000.png').read_bytes() == b'paid image'
    assert not (Path(b)/'images/0000.png').exists()


def test_no_silent_copies_when_one_image_fails(tmp_path):
    def draw(sc, out, *args):
        if sc['index'] == 0:
            Image.new('RGB',(64,64)).save(out)
            return 'test'
    ctx = {'providers':['fastgen'],'bases':['fastgen'],'gates':{'fastgen':Mock(threads=1)},
           'image_gen':{'fastgen':draw},'fallback':[],'api':None}
    scenes = [{'index':0,'prompt':'test'}, {'index':1,'prompt':'test'}]
    with pytest.raises(RuntimeError, match='Копии'):
        pipeline.generate_all_images(ctx, scenes, {}, str(tmp_path), lambda *a:None, {})
    assert not (tmp_path/'images/0001.png').exists()


def test_400_diagnosis_retains_reason_masks_key(monkeypatch):
    r = response(400, {'error': {'code':'unsupported_image','message':'No image access for private-test-key'}})
    monkeypatch.setattr(sa.requests, 'request', lambda *a, **kw:r)
    with pytest.raises(sa.SourceAnalysisError) as error:
        sa._http('POST','https://api.fast-gen.ai/v1/chat/completions',headers={'X-API-Key':'private-test-key'})
    assert 'unsupported_image' in str(error.value)
    assert 'private-test-key' not in str(error.value)


def test_fastgen_analysis_matches_public_chat_schema(monkeypatch):
    sent = []
    def http(method,url,**kwargs):
        sent.append(kwargs['json'])
        return {'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}]}
    monkeypatch.setattr(sa, '_http', http)
    assert sa._query('fastgen','test','google/gemini-auto','test')['ok']
    assert set(sent[0]) == {'model','messages'}


def test_openai_analysis_uses_reasoning_model_parameters(monkeypatch):
    sent=[]
    monkeypatch.setattr(sa, '_http', lambda *a, **kw:(sent.append(kw['json']) or {'choices':[{'message':{'content':'{"ok":true}'}}]}))
    sa._query('openai','test','gpt-5.2','test')
    assert sent[0]['max_completion_tokens'] == 6000
    assert 'max_tokens' not in sent[0]

@pytest.fixture
def source_video(tmp_path):
    p = tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','color=red:s=320x180:r=25:d=1',
                    '-f','lavfi','-i','color=blue:s=320x180:r=25:d=2',
                    '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(p)],check=True)
    return p


def test_real_cut_detection(source_video):
    shots = montage.detect_shots(source_video, 3.)
    assert len(shots) == 2
    assert shots[0]['end'] == pytest.approx(1., abs=.05)
    assert shots[1]['end'] == 3.


def profile(tmp_path):
    image = tmp_path/'reference.jpg'
    Image.new('RGB',(64,64),'red').save(image)
    return {'visual_style':{'description':'test','palette':['red'],'composition':'wide','shot_types':['wide']},
            'narrative_style':'test', 'sampling':{'duration':3.},
            'montage':{'duration':3., 'shots':[
                {'index':i,'start':a,'end':b,'duration':b-a,'description':'test',
                 'redesign_prompt':'New redesigned shot','motion':'slow pan', 'reference_frame':str(image),
                 'transition_in':{'type':'cut','duration':0.}}
                for i,(a,b) in enumerate([(0.,1.),(1.,3.)])]}}


def test_montage_timeline_scales_and_splits_long_shots(tmp_path):
    pr = profile(tmp_path)
    scenes = montage.map_scenes(pr, [], 36.)
    assert scenes[0]['start'] == 0
    assert scenes[-1]['end'] == 36
    assert all(s['duration'] <= 10.04 for s in scenes)
    assert sum(s['duration'] for s in scenes) == pytest.approx(36.)
    assert any(s['start'] == 12. for s in scenes)
    assert all(a['end'] == b['start'] for a,b in zip(scenes, scenes[1:]))


def test_montage_describes_every_shot_and_resumes(source_video, tmp_path, monkeypatch):
    def respond(engine, key, model, prompt, image_path=None):
        manifest = json.loads(prompt.split('Reference manifest JSON:\n')[1])
        assert len(manifest) == 2
        return {'shots': [{'index': item['index'], 'description': 'red/blue', 'redesign_prompt': 'New design', 'motion': 'static', 'motion_confidence': 'high'} for item in reversed(manifest)]}
    query = Mock(side_effect=respond)
    monkeypatch.setattr(sa, '_query', query)
    data = montage.analyse(source_video, tmp_path, 3., 'fastgen', 'test', 'google/gemini-auto')
    assert len(data['shots']) == 2
    assert query.call_count == 1
    montage.analyse(source_video, tmp_path, 3., 'fastgen', 'test', 'google/gemini-auto')
    assert query.call_count == 1
    assert all(Path(s['reference_frame']).exists() for s in data['shots'])


def test_cancel_not_cleared_in_background_pipeline_entry(monkeypatch):
    pipeline.CANCEL.set()
    monkeypatch.setattr(pipeline, 'load_config', lambda:{})
    monkeypatch.setattr(pipeline, 'resolve_source', lambda *a, **k:pipeline.check_cancel())
    with pytest.raises(pipeline.Cancelled):
        pipeline.prepare(reset_cancel=False)
    with pytest.raises(pipeline.Cancelled):
        pipeline.make_video(reset_cancel=False)


def test_complete_montage_pipeline_encodes_video_and_audio(tmp_path, monkeypatch):
    job = tmp_path/'job'
    job.mkdir()
    pr = profile(tmp_path)
    pipeline.write_json(str(job/'source_profile.json'), pr)
    audio = tmp_path/'narration.mp3'
    clip = tmp_path/'generated.mp4'
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','sine=frequency=300:duration=3','-c:a','libmp3lame',str(audio)], check=True)
    subprocess.run(['ffmpeg','-y','-loglevel','error','-f','lavfi','-i','testsrc2=s=320x180:r=25:d=0.5','-c:v','libx264','-pix_fmt','yuv420p',str(clip)], check=True)
    tr = {'text':'One two three.', 'words':[{'text':text,'start':a,'end':b} for text,a,b in [('One',0.,.8),('two',1.,1.8),('three.',2.,2.8)]]}
    fake = Mock()
    fake.usage.return_value = {'capabilities':{'video.generate':True}, 'account_limits':{'video_generation_threads_allowed':1,'video_gen_per_hour_limit':100}}
    fake.run.return_value = {'status':'succeeded','results':[{'download_url':'unused'}]}
    fake.download_result.side_effect = lambda item,dest:shutil.copyfile(clip, dest)
    def image(sc,out,*args):
        Image.new('RGB',(320,180),'green').save(out)
        return 'test-image'
    ctx = {'api':fake, 'providers':['fastgen'], 'bases':['fastgen'], 'fallback':[],
           'gates':{'fastgen':Mock(threads=1)}, 'image_gen':{'fastgen':image}}
    monkeypatch.setattr(pipeline, 'W', 320)
    monkeypatch.setattr(pipeline, 'H', 180)
    monkeypatch.setattr(pipeline, 'resolve_source', lambda *a,**k:(str(audio), str(job), 'test'))
    monkeypatch.setattr(pipeline, 'load_config', lambda:{'quality':'speed','fastgen_api_key':'fake'})
    monkeypatch.setattr(pipeline, 'make_context', lambda *a:ctx)
    monkeypatch.setattr(pipeline, 'make_text_engine', lambda *a:('fastgen',fake,'fastgen',fake))
    monkeypatch.setattr(pipeline, 'transcribe', lambda *a,**k:tr)
    monkeypatch.setattr(pipeline, 'build_style', lambda *a:{'style_key':'test','style':'cinema','style_hint':'cinema'})
    monkeypatch.setattr(pipeline.LLM, 'chat_json', lambda *a,**k:{'scenes':[{'index':0,'prompt':'new one'},{'index':1,'prompt':'new two'}]})
    output = pipeline.make_video(str(audio), out_dir=str(tmp_path/'output'), provider='fastgen')
    r=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',output],capture_output=True,check=True)
    data=json.loads(r.stdout)
    assert {s['codec_type'] for s in data['streams']} == {'audio','video'}
    assert float(data['format']['duration']) == pytest.approx(3.,abs=.15)
    assert fake.run.call_count == 2
    assert all(x.args[0]['keyframes'] for x in fake.run.call_args_list)
    scenes = pipeline.read_json(str(Path(pipeline.LAST_JOB_DIR)/'scenes.json'))
    assert scenes[0]['duration'] == pytest.approx(1.01,abs=.05)
    assert scenes[1]['source_shot'] == 1
    assert 'New redesigned shot' in scenes[0]['prompt']
