import copy
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

import app
import pipeline
import storyboard as sb
import studio
import subtitles


def sample_profile():
    return {'visual_style':{'description':'minimal ink cartoon','palette':['#D9B66F']},
            'montage':{'shots':[{'index':0,'duration':5,'start':0,'end':5,'description':'A cat.',
                                'redesign_prompt':'Ink cat','motion':'static'}]}}


def test_direction_preserves_style_and_legacy_identity():
    profile=sample_profile();original=copy.deepcopy(profile)
    enhanced=studio.with_direction(profile,{'enhancement_level':'director'})
    assert profile==original and enhanced['visual_style']==profile['visual_style']
    assert enhanced['creative_direction']['enhancement_level']=='director'
    assert studio.with_direction(enhanced,{'enhancement_level':'faithful'})==original
    assert studio.direction({'caption_style':'focus'})==studio.direction({'caption_style':'panel'})
    assert studio.brief({'enhancement_level':'faithful'})==''
    assert 'SAME LANGUAGE' in studio.brief()


def test_enhanced_storyboard_metadata_and_resume(tmp_path):
    profile=studio.with_direction(sample_profile(),{})
    def answer(prompt):
        data=json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        assert 'Preserve the source palette' in prompt
        return {'plans':[{'index':p['index'],'source_shot':0,'prompt':'Ink cat explaining heat.',
                         'editorial_role':'explain','shot_size':'detail','overlay_text':'Cooling'} for p in data['passages']]}
    query=Mock(side_effect=answer)
    board=sb.create('The cat explains how its body loses heat.',profile,query,tmp_path)
    assert board['rows'][0]['shot_size']=='detail'
    assert sb.create(board['script'],profile,query,tmp_path)==board and query.call_count==1
    invalid={'plans':[{'index':0,'source_shot':0,'prompt':'Cat'}]}
    with pytest.raises(ValueError,match='direction'):
        sb._validated_plans(invalid,[{'index':0}],{0:{}},True)


def test_improve_complete_script_resume_and_language(tmp_path):
    script=' '.join(['This source explains how cooling works.']*120)+' The final conclusion remains.'
    prompts=[]
    def answer(system,prompt,**kwargs):
        prompts.append(prompt)
        data=json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        return {'script':data['source']}
    client=Mock();client.chat_json.side_effect=answer
    result=studio.improve_script(script,sample_profile(),{},tmp_path,client=client,language='английском')
    assert result.endswith('The final conclusion remains.')
    assert len(prompts)>1 and all('English' in p or 'англий' in p for p in prompts)
    count=client.chat_json.call_count
    assert studio.improve_script(script,sample_profile(),{},tmp_path,client=client,language='английском')==result
    assert client.chat_json.call_count==count


def test_report_measures_without_fake_predictions():
    rows=[{'id':str(i),'source_shot':0,'confidence':'low' if i==0 else 'high','shot_size':'wide'} for i in range(4)]
    result=studio.editorial(('One common phrase repeats again. '*3)+' New value is 42.',{'rows':rows},'Old value is 12.')
    assert result['new_numbers_to_verify']==['42']
    assert result['repeated_phrases'] and result['weak_plans']==['0']
    assert result['repeated_references']==[{'source_shot':0,'consecutive_plans':4}]
    assert 'retention' not in result and 'score' not in result


def package():
    return {'titles':[f'Title {i}' for i in range(5)],'hooks':['First','Second','Third'],
            'thumbnail_concepts':[{'concept':'Cat','image_prompt':'Ink cat','overlay_text':'Heat','fake_ctr':99} for _ in range(3)],
            'experiment_plan':['Compare titles','Measure retention','Check sample size'],'fake_ctr':99}


def test_packaging_whitelist_validation_and_full_tail(tmp_path):
    client=Mock();client.chat_json.return_value=package()
    text=('Long complete content. '*4000)+' UNIQUE FINAL PAYOFF'
    result=studio.packaging(text,sample_profile(),{},tmp_path,client)
    assert 'UNIQUE FINAL PAYOFF' in client.chat_json.call_args.args[1]
    assert 'fake_ctr' not in result and all('fake_ctr' not in v for v in result['thumbnail_concepts'])
    assert studio.packaging(text,sample_profile(),{},tmp_path,client)==result
    assert client.chat_json.call_count==1
    client.chat_json.return_value={'titles':['incomplete']}
    with pytest.raises(ValueError):studio.packaging('Different script',{}, {},tmp_path,client)


def test_publication_export_recovers_after_restart_without_keys(tmp_path,monkeypatch):
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path));monkeypatch.setattr(app,'WINDOW',None)
    monkeypatch.setattr(app,'load_cfg',lambda:{'fastgen_api_key':'DO-NOT-EXPORT'})
    monkeypatch.setattr(app,'STATE',{'script':'A cat explains heat.','source_profile':sample_profile()})
    stored=package();stored.pop('fake_ctr')
    sb.atomic(studio.publication_path(tmp_path/'jobs/studio',app.STATE['script'],sample_profile()),stored)
    result=app.Api().export_studio({})
    assert result['ok'],result
    with zipfile.ZipFile(result['path']) as archive:
        assert 'publication-package.json' in archive.namelist()
        assert all('DO-NOT-EXPORT' not in archive.read(n).decode() for n in archive.namelist())
    assert studio.publication_path(tmp_path,'changed',sample_profile())!=studio.publication_path(tmp_path,app.STATE['script'],sample_profile())


def test_video_preflight_blocks_explicit_denial_without_generation(monkeypatch):
    monkeypatch.setattr(app,'load_cfg',lambda:{'fastgen_api_key':'fixture'})
    monkeypatch.setattr(app,'estimate_plan',lambda opts,**kwargs:{'clips':2})
    fake=Mock();fake.usage.return_value={'capabilities':{'video.generate':False}}
    constructor=Mock(return_value=fake);monkeypatch.setattr(app,'FastGen',constructor)
    result=app.production_check({'providers':['fastgen'],'mode':'video'})
    assert not result['ok'] and result['video_access']=='denied'
    fake.create.assert_not_called();constructor.assert_called_once_with('fixture',timeout=15)
    monkeypatch.setattr(app,'estimate_plan',lambda opts,**kwargs:{'clips':0})
    assert app.production_check({'providers':['fastgen'],'mode':'images'})['ok']
    assert constructor.call_count==1


@pytest.mark.parametrize('preset',['source','focus','panel','minimal'])
def test_real_ass_render_preserves_audio_and_neutralizes_markup(tmp_path,preset):
    ffmpeg=shutil.which('ffmpeg');probe=shutil.which('ffprobe')
    if not ffmpeg or not probe:pytest.skip('FFmpeg not installed')
    raw=tmp_path/'raw.mp4';out=tmp_path/'out.mp4';ass=tmp_path/'captions.ass'
    subprocess.run([ffmpeg,'-y','-v','error','-f','lavfi','-i','color=c=0x181c20:s=640x360:r=25:d=2',
                    '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(raw)],check=True)
    words=[{'text':'Важная','start':.1,'end':.6},{'text':r'{\pos(0,0)}мысль','start':.6,'end':1.5}]
    subtitles.write_ass(words,ass,preset=preset,profile=sample_profile(),width=640,height=360,
                        annotations=[{'start':0,'end':2,'overlay_text':'Как работает охлаждение'}])
    value=ass.read_text(encoding='utf-8-sig')
    assert r'{\pos' not in value and '｛' in value and 'Cooling' not in value
    subtitles.burn(ffmpeg,str(raw),str(ass),str(out))
    streams=json.loads(subprocess.check_output([probe,'-v','error','-show_streams','-of','json',str(out)]))['streams']
    assert {v['codec_type'] for v in streams}=={'audio','video'}
    # Decode the same frame: burn-in must materially change its pixels.
    def frame(path):return subprocess.check_output([ffmpeg,'-v','error','-ss','0.8','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
    assert sum(a!=b for a,b in zip(frame(raw),frame(out)))>1000


def test_project_roundtrip_typed_studio_settings(tmp_path):
    from native_projects import ProjectStore
    store=ProjectStore(tmp_path)
    value=store.save('Studio',{'script':'Test','studio':{'editing_pace':'dynamic','caption_style':'focus','api_key':'never-save'}})
    result=ProjectStore(tmp_path).get(value['id'])['options']['studio']
    assert result['editing_pace']=='dynamic' and result['caption_style']=='focus'
    assert 'api_key' not in result
