import copy
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

import animation_selection as choose
import channel_design
import reference_animation as motion


def profile(share=.5,confidence='high'):
    return {'camera_styles':['static','push-in','static','pan-left'],'strength':.06,
            'subject_motion_share':share,'instructions':'Restrained movement with still pauses; preserve silhouette and avoid head bobbing.',
            'confidence':confidence}


def test_reference_selection_preserves_local_camera_and_caps_safe_neural_scenes():
    scenes=[{'index':i,'id':str(i),'editorial_role':'hook' if i==0 else 'reveal','prompt':'A whale swims.',
             'reference_animation':profile()} for i in range(10)]
    scenes[1]['shot_size']='diagram'
    original=copy.deepcopy(scenes)
    selected=choose.apply(scenes,{'strategy':'reference','limit':3})
    assert sum(s['_animate'] for s in selected)==3 and not selected[1]['_animate']
    assert [s['_local_motion']['kind'] for s in selected[:4]]==['static','push-in','static','pan-left']
    assert all('Restrained movement' in s['source_motion'] for s in selected if s['_animate'])
    assert scenes==original


@pytest.mark.parametrize('reason',['still','low_confidence','local_policy','zero_budget'])
def test_reference_can_be_reused_without_any_neural_clip(reason):
    reference=profile(0 if reason=='still' else 1,'low' if reason=='low_confidence' else 'high')
    scenes=[{'index':i,'prompt':'A whale','reference_animation':reference,
             'channel_design':{'motion_policy':'local'} if reason=='local_policy' else None} for i in range(8)]
    result=choose.apply(scenes,{'strategy':'reference','limit':0 if reason=='zero_budget' else 8})
    assert not any(s['_animate'] for s in result)
    assert all('_local_motion' in s for s in result)


def test_unknown_motion_is_not_invented_from_a_still():
    with pytest.raises(ValueError,match='Профиль оживления'):
        choose.select([{'index':0}],{'strategy':'reference','limit':10})
    with pytest.raises(ValueError):motion.validate({**profile(),'strength':float('nan')})
    with pytest.raises(ValueError):motion.validate({**profile(),'camera_styles':['spin']})


def test_temporal_sampling_uses_triples_and_reuses_completed_analysis(tmp_path,monkeypatch):
    import source_analysis as sa
    monkeypatch.setattr(sa,'_provider',lambda *a:('elysium','fixture','vision'))
    monkeypatch.setattr(sa,'_duration',lambda *a:60)
    monkeypatch.setattr(sa,'_hash_file',lambda *a:'source-file-hash')
    extracts=[]
    def frames(video,folder,times):
        extracts.append(times);folder.mkdir(parents=True,exist_ok=True);paths=[]
        for i,t in enumerate(times):
            path=folder/f'{i}.jpg';Image.new('RGB',(160,90),(i*10,80,100)).save(path);paths.append(path)
        return paths
    monkeypatch.setattr(sa,'extract_frames',frames)
    requests=[]
    def query(folder,engine,key,model,prompt,image_path,validate,progress=None):
        requests.append(prompt)
        with Image.open(image_path) as image:assert image.size==(1200,1470)
        validate(profile());return profile()
    monkeypatch.setattr(sa,'cached_query',query)
    first=motion.analyse('video',{},tmp_path)
    assert len(extracts)==len(requests)==1 and len(extracts[0])==18
    assert all(a<b<c for a,b,c in zip(extracts[0][::3],extracts[0][1::3],extracts[0][2::3]))
    assert 'Compare movement WITHIN rows only' in requests[0]
    assert motion.analyse('video',{},tmp_path)==first and len(requests)==1


def test_channel_motion_revision_does_not_change_image_fingerprint(tmp_path):
    store=channel_design.DesignStore(tmp_path)
    first=store.save({'name':'Channel','description':'Own flat teal drawings'})
    second=store.save({**first,'reference_animation':profile()},first['id'],first['revision'])
    assert channel_design.generation_fingerprint(first)==channel_design.generation_fingerprint(second)
    assert channel_design.content_profile(second)['reference_animation']==motion.validate(profile())
    assert store.get(first['id'],1)['reference_animation'] is None
    assert channel_design.DesignStore(tmp_path).get(first['id'])['reference_animation']==motion.validate(profile())


def test_local_paths_and_real_clip_cache_follow_profile(tmp_path,monkeypatch):
    import pipeline
    import subprocess
    pipeline.CANCEL.clear()
    for key,value in [('W',128),('H',72),('FPS',6)]:monkeypatch.setattr(pipeline,key,value)
    image=Image.new('RGB',(320,180))
    for x in range(320):
        for y in range(180):image.putpixel((x,y),(x%256,y%256,(x+y)%256))
    source=tmp_path/'source.png';image.save(source);output=tmp_path/'clip.mp4'
    pipeline.render_scene_clip(str(source),0,1,str(output),{'kind':'static','strength':.1})
    old=json.loads(Path(str(output)+'.json').read_text())
    pipeline.render_scene_clip(str(source),0,1,str(output),{'kind':'pan-left','strength':.1})
    new=json.loads(Path(str(output)+'.json').read_text())
    assert old['reference_motion']['kind']=='static' and new['reference_motion']['kind']=='pan-left'
    decoded=subprocess.run([pipeline.ffmpeg_bin(),'-v','error','-i',str(output),'-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
    size=128*72*3;assert len(decoded)>=size*5 and decoded[:size]!=decoded[-size:]
    assert motion.local_path('static',1,.1) is None
    assert motion.local_path('push-in',1,.1)(1)[0]>motion.local_path('push-in',1,.1)(0)[0]


def test_saved_channel_reuses_motion_without_new_source_video_analysis(tmp_path,monkeypatch):
    import pipeline
    import studio
    import youtube_source
    from test_source_adaptation import SOURCE,SCRIPT,client_fixture
    pipeline.CANCEL.clear();monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    source=tmp_path/'jobs'/'youtube_sources';source.mkdir(parents=True)
    (source/'Cs9lG-Meo1s.txt').write_text(SOURCE)
    store=channel_design.DesignStore(tmp_path)
    design=store.save({'name':'Channel','description':'Own flat art','reference_animation':profile()})
    cfg={'anthropic_api_key':'fixture','enhancement_level':'original','animation':{'strategy':'reference','limit':3},'_channel_design':design}
    monkeypatch.setattr(youtube_source,'video',Mock(side_effect=AssertionError('Saved motion needs no new source frames')))
    monkeypatch.setattr(motion,'analyse',Mock(side_effect=AssertionError('No repeated motion request')))
    client,_=client_fixture();monkeypatch.setattr(studio,'text_client',lambda *a:client)
    profiles=[]
    result=pipeline.rewrite_script(cfg,{'source':'youtube','url':'https://www.youtube.com/watch?v=Cs9lG-Meo1s','engine':'anthropic','language':'русском'},lambda *a:None,profiles.append)
    assert result==SCRIPT and profiles[-1]['reference_animation']==motion.validate(profile())


def test_preparing_a_source_learns_motion_once_and_keeps_it_in_original_profile(tmp_path,monkeypatch):
    import pipeline
    import studio
    import rewrite
    import source_analysis
    import source_narration
    import youtube_source
    from test_source_adaptation import SOURCE,SCRIPT,PROFILE,client_fixture
    pipeline.CANCEL.clear();monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(pipeline.channel_designs,'selected',lambda *a:None)
    monkeypatch.setattr(rewrite,'youtube_transcript',lambda *a,**k:SOURCE)
    monkeypatch.setattr(youtube_source,'video',lambda *a,**k:'video.mp4')
    monkeypatch.setattr(source_analysis,'analyse',lambda *a,**k:copy.deepcopy(PROFILE))
    monkeypatch.setattr(source_narration,'attach_youtube',lambda p,*a:p)
    learned=Mock(return_value=motion.validate(profile()));monkeypatch.setattr(motion,'analyse',learned)
    client,_=client_fixture();monkeypatch.setattr(studio,'text_client',lambda *a:client)
    profiles=[]
    result=pipeline.rewrite_script({'anthropic_api_key':'fixture','enhancement_level':'original','animation':{'strategy':'reference','limit':3}},
        {'source':'youtube','url':'https://www.youtube.com/watch?v=Cs9lG-Meo1s','analyze_video':True,'engine':'anthropic','language':'русском'},lambda *a:None,profiles.append)
    assert result==SCRIPT and learned.call_count==1
    assert profiles[-1]['reference_animation']==motion.validate(profile()) and profiles[-1]['content_only']


def test_motion_only_learning_does_not_rewrite_or_change_channel_before_save(tmp_path,monkeypatch):
    import app
    import studio
    import source_analysis
    import youtube_source
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False,'service_switch':None})
    monkeypatch.setattr(app,'keep_awake',lambda *a:None)
    monkeypatch.setattr(source_analysis,'_provider',lambda *a:('elysium','fixture','vision'))
    monkeypatch.setattr(youtube_source,'video',lambda *a,**k:'video.mp4')
    monkeypatch.setattr(motion,'analyse',lambda *a,**k:motion.validate(profile(0)))
    monkeypatch.setattr(studio,'improve_script',Mock(side_effect=AssertionError('No rewrite')))
    app.save_cfg({'channel_design_id':'','rewrite_lang':'русском'})
    before=app.load_cfg()
    result=app.Api().channel_animation_learn('https://www.youtube.com/watch?v=Cs9lG-Meo1s')
    assert result['ok'] and result['reference_animation']['subject_motion_share']==0
    assert app.load_cfg()==before and not app.STATE['preparing']


def test_render_without_observed_motion_stops_before_a_paid_worker(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'));monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False,'service_switch':None,'source_profile':None,'source_profile_id':None})
    worker=Mock(side_effect=AssertionError('No render'));monkeypatch.setattr(app,'run_job',worker)
    app.save_cfg({'fastgen_api_key':'fixture','llm_engine':'fastgen','stt_engine':'fastgen','tts_service':'edge'})
    result=app.Api().start({'source':'script','script':'A short original narration.','tts':'edge','providers':['fastgen'],
                          'animation':{'strategy':'reference','limit':3}})
    assert not result['ok'] and 'Профиль оживления' in result['error']
    assert not worker.called
