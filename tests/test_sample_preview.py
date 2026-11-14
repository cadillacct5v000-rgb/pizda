import copy
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import Mock

from PIL import Image
import pytest

import app
import pipeline
import sample_preview
import source_analysis
import storyboard as sb
import studio
import youtube_source
from test_storyboard import profile, audio, words, board, duration


@pytest.fixture(autouse=True)
def isolate(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(pipeline,'LOG_PATH',str(tmp_path/'log.txt'))
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False,'sample_board_id':None,'log':[]})
    pipeline.CANCEL.clear()
    yield
    pipeline.CANCEL.clear()


def cfg():
    return {'fastgen_api_key':'fixture','llm_engine':'fastgen','stt_engine':'fastgen',
            'rewrite_engine':'fastgen','source_analysis_engine':'fastgen','tts_service':'edge'}


def options():
    return {'providers':['fastgen'],'mode':'images','tts':'edge','render_fps':60,'subtitles':True,'hd':True,
            'rewrite':{'engine':'fastgen','url':'https://www.youtube.com/watch?v=dQw4w9WgXcQ'},
            'sample':{'start':45,'seconds':30},'studio':{'enhancement_level':'director'}}


@pytest.mark.parametrize('start,seconds',[(-1,30),(float('nan'),30),(float('inf'),30),(0,31),(0,0),(True,30)])
def test_api_invalid_ranges_do_not_launch_any_work(tmp_path,monkeypatch,start,seconds):
    monkeypatch.setattr(app,'load_cfg',cfg)
    work=Mock();monkeypatch.setattr(app.threading,'Thread',work)
    params=options();params['sample']={'start':start,'seconds':seconds}
    assert not app.Api().youtube_sample(params)['ok']
    work.assert_not_called()
    assert not app.STATE['running']


def test_preflight_denial_stops_before_paid_source_work(monkeypatch):
    monkeypatch.setattr(app,'load_cfg',cfg)
    monkeypatch.setattr(app,'production_check',lambda opts:{'blockers':['video.generate=false']})
    work=Mock();monkeypatch.setattr(app.threading,'Thread',work)
    result=app.Api().youtube_sample({**options(),'mode':'video'})
    assert not result['ok'] and 'video.generate=false' in result['error']
    work.assert_not_called()


def test_api_preserves_current_production_settings_and_separate_scope(monkeypatch):
    monkeypatch.setattr(app,'load_cfg',cfg)
    monkeypatch.setattr(app,'production_check',lambda opts:{'blockers':[]})
    worker=Mock();monkeypatch.setattr(app.threading,'Thread',worker)
    result=app.Api().youtube_sample(options())
    assert result['ok'],result
    passed,snapshot,start,seconds=worker.call_args.kwargs['args']
    assert (start,seconds)==(45,30) and passed['render_fps']==60
    assert passed['hd'] and passed['subtitles'] and passed['storyboard_id'] is None
    assert snapshot['enhancement_level']=='director' and app.STATE['is_preview']
    worker.return_value.start.assert_called_once()


def av(path,seconds=4):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('FFmpeg missing')
    subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i',f'color=c=tan:s=320x180:r=25:d={seconds}',
                    '-f','lavfi','-i',f'sine=frequency=440:duration={seconds}','-c:v','libx264','-c:a','aac','-shortest',str(path)],check=True)


def test_prepare_bills_only_bounded_audio_and_analysis_not_whole_source(tmp_path,monkeypatch,profile):
    clip=tmp_path/'clip.mp4';av(clip)
    monkeypatch.setattr(youtube_source,'segment',Mock(return_value=str(clip)))
    fullvideo=Mock(side_effect=AssertionError('Whole source must not be fetched'));monkeypatch.setattr(youtube_source,'video',fullvideo)
    fulltext=Mock(side_effect=AssertionError('Whole transcript must not be fetched'));monkeypatch.setattr(youtube_source,'transcript',fulltext)
    monkeypatch.setattr(pipeline,'make_text_engine',lambda c:('fixture',None,None,None))
    narration='The spaceship arrives. The cat waits.'
    def transcribe(engine,client,path,directory,progress,seconds):
        assert duration(path)<4.2 and seconds<4.2
        assert Path(directory).is_dir()
        return {'text':narration}
    monkeypatch.setattr(pipeline,'transcribe',transcribe)
    analyse=Mock(return_value=copy.deepcopy(profile));monkeypatch.setattr(source_analysis,'analyse',analyse)
    def reply(system,prompt,**kwargs):
        data=json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        if 'source' in data:return {'script':data['source']}
        return {'plans':[{'index':u['index'],'source_shot':0,'confidence':'high','prompt':'Ink scene',
                         'shot_size':'wide','editorial_role':'explain'} for u in data['passages']]}
    client=Mock();client.chat_json.side_effect=reply;monkeypatch.setattr(studio,'text_client',lambda *a:client)
    created,target=sample_preview.prepare(cfg(),options()['rewrite'],tmp_path,start=45,seconds=30)
    assert target<4.2 and created['script']==narration
    assert created['profile']['sample']['start']==45
    assert analyse.call_args.args[0]==str(clip) and analyse.call_args.args[1]==narration
    fullvideo.assert_not_called();fulltext.assert_not_called()


def test_downloader_sections_and_cached_scope(tmp_path,monkeypatch):
    import yt_dlp
    calls=[]
    class FakeDownloader:
        def __init__(self,opts):self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def download(self,urls):
            ranges=list(self.opts['download_ranges']({'duration':300},None));calls.append(ranges)
            av(Path(self.opts['outtmpl'].replace('%(ext)s','mp4')),4)
    monkeypatch.setattr(yt_dlp,'YoutubeDL',FakeDownloader)
    url=options()['rewrite']['url']
    a=youtube_source.segment(url,tmp_path,'',start=40,seconds=4)
    assert calls==[[{'start_time':40.0,'end_time':44.0}]]
    assert youtube_source.segment(url,tmp_path,'',start=40,seconds=4)==a and len(calls)==1
    b=youtube_source.segment(url,tmp_path,'',start=80,seconds=4)
    assert a!=b and len(calls)==2
    assert duration(a)<=4.15 and duration(b)<=4.15


def test_downloader_rejects_unbounded_response_instead_of_paid_full_analysis(tmp_path,monkeypatch):
    import yt_dlp
    class FakeDownloader:
        def __init__(self,opts):self.opts=opts
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def download(self,urls):av(Path(self.opts['outtmpl'].replace('%(ext)s','mp4')),8)
    monkeypatch.setattr(yt_dlp,'YoutubeDL',FakeDownloader)
    with pytest.raises(youtube_source.YouTubeError,match='больше выбранного отрезка'):
        youtube_source.segment(options()['rewrite']['url'],tmp_path,'',start=40,seconds=4)
    assert not list(tmp_path.glob('*/source.mp4'))


@pytest.mark.parametrize('fps',[25,60])
def test_actual_sample_render_includes_audio_captions_and_exact_duration(tmp_path,monkeypatch,profile,audio,fps):
    original=board(profile);job=tmp_path/'voice';job.mkdir()
    pipeline.write_json(str(job/'source_profile.json'),profile)
    monkeypatch.setattr(pipeline,'W',640);monkeypatch.setattr(pipeline,'H',360)
    monkeypatch.setattr(pipeline,'load_config',lambda:{'object_continuity':False,'caption_style':'focus','on_screen_labels':True})
    monkeypatch.setattr(pipeline,'resolve_source',lambda *args:(audio,str(job),'sample'))
    monkeypatch.setattr(pipeline,'make_text_engine',lambda cfg:('fastgen',Mock(),'fastgen',Mock()))
    monkeypatch.setattr(pipeline,'make_context',lambda *args:{})
    monkeypatch.setattr(pipeline,'make_qa_client',lambda cfg:None)
    monkeypatch.setattr(pipeline,'transcribe',lambda *a:{'text':original['script'],'words':words()})
    calls=[]
    def images(ctx,scenes,style,directory,progress,refs,on_image=None):
        root=Path(directory)/'images';root.mkdir(exist_ok=True)
        for s in scenes:
            calls.append(s['prompt']);Image.new('RGB',(640,360),'#304550').save(root/f"{s['index']:04d}.png")
        return str(root)
    monkeypatch.setattr(pipeline,'generate_all_images',images)
    output=pipeline.make_video(script_text=original['script'],storyboard=original,mode='images',provider=['fastgen'],
             subtitles=True,render_fps=fps,sample_seconds=5,preview_range={'start':0.,'seconds':5.},out_dir=str(tmp_path/'out'))
    assert duration(output)==pytest.approx(5,abs=.08)
    info=json.loads(subprocess.check_output([pipeline.ffmpeg_bin('ffprobe'),'-v','error','-show_streams','-of','json',output]))['streams']
    assert {s['codec_type'] for s in info}=={'video','audio'}
    assert next(s for s in info if s['codec_type']=='video')['r_frame_rate']==f'{fps}/1'
    assert len(calls)==2 and list((tmp_path/'out/previews/subtitles').glob('*.ass'))
    assert 'Dialogue:' in next((tmp_path/'out/previews/subtitles').glob('*.ass')).read_text(encoding='utf-8-sig')


def test_worker_cancellation_releases_busy_state_without_starting_generation(monkeypatch):
    monkeypatch.setattr(sample_preview,'prepare',Mock(side_effect=pipeline.Cancelled('stop')))
    generation=Mock();monkeypatch.setattr(pipeline,'make_video',generation)
    app.STATE.update(running=True)
    app.run_youtube_sample(options(),cfg(),45,30)
    assert not app.STATE['running'] and app.STATE['cancelled']
    generation.assert_not_called()


@pytest.mark.parametrize('mode',['images','mixed','video'])
def test_sample_worker_uses_short_script_and_same_production_options(tmp_path,monkeypatch,profile,mode):
    short=board(profile);short['profile']['sample']={'start':0.,'requested_seconds':30.,'source_seconds':30.}
    short=sb.seal(short)
    monkeypatch.setattr(sample_preview,'prepare',lambda *a,**k:(short,30.))
    generate=Mock(return_value=str(tmp_path/'preview.mp4'));monkeypatch.setattr(pipeline,'make_video',generate)
    passed={**options(),'mode':mode,'script':'UNUSED ENTIRE SOURCE SCRIPT','voice':'fixture-voice','use_refs':True,'video_first':2,'motion':True}
    app.run_youtube_sample(passed,cfg(),0,30)
    args=generate.call_args.kwargs
    assert args['script_text']==short['script'] and args['mode']==mode
    assert args['hd'] and args['subtitles'] and args['use_refs'] and args['motion']
    assert args['tts_voice']=='fixture-voice' and args['render_fps']==60
    assert args['sample_seconds']==30 and args['preview_range']=={'start':0.,'seconds':30.}
    assert app.STATE['sample_board_id']==short['id'] and not app.STATE['running']


def test_model_cannot_expand_trial_into_full_script_before_tts(tmp_path):
    fake=Mock();fake.chat_json.return_value={'script':'Entire video narration. '*200}
    with pytest.raises(ValueError,match='раздула'):
        studio.improve_script('A short source excerpt.',{'sample':{'source_seconds':30}},{},tmp_path,client=fake)
    assert not list(tmp_path.glob('*/script.txt'))
