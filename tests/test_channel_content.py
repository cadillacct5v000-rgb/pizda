from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json

from PIL import Image
import pytest

import channel_design as d
import pipeline as p
import rewrite
import source_analysis
import source_narration
import storyboard as sb
import studio
import youtube_source
from tests.test_channel_design import value


@pytest.fixture(autouse=True)
def local(tmp_path,monkeypatch):
    p.CANCEL.clear();monkeypatch.setattr(p,'data_dir',lambda:str(tmp_path))
    yield
    p.CANCEL.clear()


def test_new_urls_use_only_transcripts_and_never_source_frames_style_cuts_or_delivery(tmp_path,monkeypatch):
    profile=d.DesignStore(tmp_path).save(value())
    cfg={'channel_design_id':profile['id'],'channel_design_revision':1,'fastgen_api_key':'fixture'}
    transcript=Mock(return_value='Source content about a car and braking.');monkeypatch.setattr(rewrite,'youtube_transcript',transcript)
    rewrite_call=Mock(return_value='New narration in the channel.');monkeypatch.setattr(rewrite,'rewrite',rewrite_call)
    blocked=Mock(side_effect=AssertionError('Must not analyse or download source visuals'))
    monkeypatch.setattr(youtube_source,'video',blocked);monkeypatch.setattr(source_analysis,'analyse',blocked)
    monkeypatch.setattr(source_narration,'attach_youtube',blocked)
    profiles=[]
    rw={'source':'youtube','engine':'fastgen','words':100,'language':'русском','analyze_video':True,'montage':True}
    for vid in ('abcdefghijk','lmnopqrstuv','abcdefghijk'):
        assert p.rewrite_script(cfg,{**rw,'url':'https://www.youtube.com/watch?v='+vid},Mock(),profiles.append)=='New narration in the channel.'
    blocked.assert_not_called();assert transcript.call_count==2
    assert all(x['content_only'] and x['montage']['shots']==[] for x in profiles)
    assert {x['channel_design_ref']['generation_key'] for x in profiles}=={d.generation_fingerprint(profile)}
    assert 'source_profile' not in rewrite_call.call_args.kwargs
    assert rewrite_call.call_args.args[4]==100


def test_channel_storyboard_creates_own_scenes_without_foreign_shot_mapping_and_reuses_font_changes(tmp_path):
    profile=d.DesignStore(tmp_path).save(value());calls=[]
    def query(prompt):
        data=json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        assert data['source_shots']==[]
        assert 'APPROVED' in prompt.upper() or 'SAVED CHANNEL' in prompt
        calls.append(data)
        return {'plans':[{'index':u['index'],'source_shot':None,'confidence':'high','reason':'Own channel scene',
                          'prompt':'Flat diagram on a white background.','motion':'static camera',
                          'editorial_role':'explain','shot_size':'diagram','overlay_text':'Stop'} for u in data['passages']]}
    text='The car slows down. The narrator explains the seat belt.'
    first=sb.create(text,d.content_profile(profile),query,tmp_path/'boards')
    assert all(r['source_shot'] is None and r['shot_size']=='diagram' for r in first['rows'])
    amount=len(calls)
    revised={**profile,'name':'Other title','font':'Intro Demo Black CAPS','revision':2}
    second=sb.create(text,d.content_profile(revised),query,tmp_path/'boards')
    assert first['id']==second['id'] and len(calls)==amount


def test_visual_profile_changes_reuse_narration_job_without_source_delivery_analysis(tmp_path,monkeypatch):
    profile=d.DesignStore(tmp_path).save(value());paths=[]
    def voice(cfg,text,service,voice,directory,progress):paths.append(directory);return str(Path(directory)/'voice.mp3')
    monkeypatch.setattr(p,'make_voiceover',voice)
    for design in (profile,{**profile,'faces':'detailed','description':'A completely different visual style'}):
        p.resolve_source({},None,'The same narration.','elevenlabs','voice-id',Mock(),source_profile=d.content_profile(design))
    assert len(paths)==2 and paths[0]==paths[1]


def test_one_time_design_learning_caches_vision_and_does_not_transcribe_or_analyse_cuts(tmp_path,monkeypatch):
    video=tmp_path/'source.mp4';video.write_bytes(b'video-fixture')
    monkeypatch.setattr(source_analysis,'_provider',lambda cfg:('fastgen','fixture-key','fixture-model'))
    monkeypatch.setattr(source_analysis,'_preflight',lambda *a:None)
    monkeypatch.setattr(source_analysis,'_duration',lambda path:120.)
    def frames(video,folder,times):
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);result=[]
        for i,t in enumerate(times):
            path=folder/f'{i:03d}.jpg'
            if not path.exists():Image.new('RGB',(64,36),(i*20,0,0)).save(path)
            result.append(str(path))
        return result
    monkeypatch.setattr(source_analysis,'extract_frames',frames)
    query=Mock(return_value={'visual_style':{'description':'Flat style','palette':['#FFFFFF'],'composition':'Minimal','shot_types':['diagram'],'character_design':'Blank faces for ordinary humans; simple eyes for the mascot.'}})
    monkeypatch.setattr(source_analysis,'_query',query)
    a=source_analysis.analyse_design(str(video),{},tmp_path/'learning')
    b=source_analysis.analyse_design(str(video),{},tmp_path/'learning')
    assert a==b and query.call_count==1
    assert len(a['reference_frames'])<=4
    assert 'Blank faces' in a['visual_style']['character_design']


def test_bounded_channel_sample_never_analyses_source_design_or_delivery(tmp_path,monkeypatch):
    import sample_preview
    import subprocess
    from tests.test_sample_preview import av
    clip=tmp_path/'clip.mp4';av(clip)
    design=d.DesignStore(tmp_path).save(value())
    monkeypatch.setattr(youtube_source,'segment',Mock(return_value=str(clip)))
    blocked=Mock(side_effect=AssertionError('Channel sample must not analyse foreign visual or voice style'))
    monkeypatch.setattr(source_analysis,'analyse',blocked);monkeypatch.setattr(source_narration,'attach',blocked)
    monkeypatch.setattr(p,'make_text_engine',lambda c:('fixture',None,None,None))
    monkeypatch.setattr(p,'transcribe',lambda *a:{'text':'A car stops. A driver waits.'})
    def reply(system,prompt,**kw):
        data=json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        if 'source' in data:return {'script':data['source']}
        return {'plans':[{'index':u['index'],'source_shot':None,'confidence':'high','prompt':'A flat channel scene',
                          'shot_size':'wide','editorial_role':'explain'} for u in data['passages']]}
    client=Mock();client.chat_json.side_effect=reply;monkeypatch.setattr(studio,'text_client',lambda *a:client)
    board,target=sample_preview.prepare({'_channel_design':design},{'url':'https://www.youtube.com/watch?v=abcdefghijk'},tmp_path/'sample')
    assert target<4.2 and board['profile']['content_only']
    assert all(r['source_shot'] is None for r in board['rows'])
    blocked.assert_not_called()


def test_clear_jobs_preserves_approved_design_library(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False})
    store=d.DesignStore(tmp_path);profile=store.save(value())
    from tests.test_channel_design import asset
    profile=store.import_asset(profile['id'],1,asset(tmp_path),'host')
    original=store.asset_path(profile['assets'][0]).read_bytes()
    removable=tmp_path/'jobs'/'old';removable.mkdir();(removable/'test.txt').write_text('temporary')
    assert app.Api().clear_jobs()['ok']
    assert not removable.exists()
    assert store.asset_path(store.get(profile['id'])['assets'][0]).read_bytes()==original
