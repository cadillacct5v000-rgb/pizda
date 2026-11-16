import copy
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest

import channel_design as design
import narration_rules as rules
import pipeline
import rewrite
import source_narration
import studio


def appearance():
    return {'name':'My channel','description':'Flat teal explanatory illustrations.',
            'palette':['#126B70'],'faces':'blank','font':'Arial','layout':'minimal','rules':'No detailed human faces.'}


def delivery():
    return {'speech_present':True,'instructions':'Restrained curious narration with purposeful pauses and a clear contrast at the reveal.',
            'tempo_wpm':190,'pause_ms':380,'intonation_variation':.6,'energy':.55}


def picture(tmp_path, name='whale.png', transparent=False):
    path=tmp_path/name
    image=Image.new('RGBA' if transparent else 'RGB',(64,32),(0,0,0,0) if transparent else '#126B70')
    if transparent:
        from PIL import ImageDraw
        ImageDraw.Draw(image).ellipse((5,5,58,26),fill='#126B70')
    image.save(path);return path


def test_every_language_has_native_directions_and_a_default_edge_voice():
    from tts_api import EdgeTTS
    assert set(rewrite.LANG_INFO)==set(rules.LANGUAGES)
    for language,code in rules.LANGUAGES.items():
        assert rules.language_code(language)==code==rules.language_code(code)
        assert code in EdgeTTS.DEFAULT_BY_LANG
        assert 'literal translation is not the task' in rules.localisation(language)
    assert 'verb-final' in rules.localisation('немецком')
    assert 'topic markers' in rules.localisation('японском')
    assert 'Russian calques' not in rules.localisation('английском') # Rules target native English.


def test_localised_delivery_is_valid_and_does_not_clone_foreign_timing():
    original=delivery();localized=rules.voice_direction(original,'ja')
    assert localized['tempo_wpm']==159 and localized['tempo_wpm']!=original['tempo_wpm']
    assert 'native ja prosody' in localized['instructions']
    assert source_narration.validate(localized)==localized
    assert original==delivery()


def test_channel_presentation_and_language_voices_survive_restart_without_redrawing(tmp_path):
    store=design.DesignStore(tmp_path);base=store.save(appearance())
    value={**base,'presentation':'Curious, restrained, with short reveals.','delivery':delivery(),
           'voices':{'ru':{'service':'elevenlabs','id':'russian-native'},'en':{'service':'edge','id':'en-US-GuyNeural'}}}
    new=store.save(value,base['id'],base['revision'])
    restored=design.DesignStore(tmp_path).get(new['id'])
    assert restored['voices']['ru']['id']=='russian-native'
    assert restored['delivery']==delivery()
    assert design.generation_fingerprint(base)==design.generation_fingerprint(restored)
    assert design.fingerprint(base)!=design.fingerprint(restored)
    profile=design.content_profile(restored)
    assert profile['narrative_style']==value['presentation']
    assert source_narration.delivery_of(profile)==delivery()
    cfg={'rewrite_lang':'английском'};design.activate_voice(cfg,restored)
    assert cfg['tts_service']=='edge' and cfg['tts_voice']=='en-US-GuyNeural'
    design.activate_voice(cfg,restored,'ru');assert cfg['tts_voice']=='russian-native'
    assert 'api_key' not in json.dumps(restored)
    assert store.get(base['id'],1)['voices']=={}


@pytest.mark.parametrize('role,transparent',[('character',False),('character_layer',True)])
def test_recurring_whale_uses_same_file_only_where_present_across_videos(tmp_path,role,transparent):
    store=design.DesignStore(tmp_path);profile=store.save(appearance());image=picture(tmp_path,transparent=transparent)
    profile=store.import_asset(profile['id'],1,image,role,{'aliases':['Willy','кит'],'identity':'One teal whale with a round fin and a pale left patch.'})
    asset=profile['assets'][0];raw=store.asset_path(asset).read_bytes()
    assert raw==image.read_bytes()
    scenes=[{'index':0,'prompt':'Willy swims past a reef.','text':'Кит плывёт.'},
            {'index':1,'prompt':'A dolphin swims.','text':'Китай показан на карте.'}]
    first=design.apply(scenes,profile,tmp_path)
    second=design.apply([{'index':99,'prompt':'Willy dives.','text':'Another episode.'}],design.DesignStore(tmp_path).get(profile['id']),tmp_path)
    assert first[0]['channel_characters'][0]['sha256']==second[0]['channel_characters'][0]['sha256']==asset['sha256']
    assert not first[1]['channel_characters'] and not first[1]['channel_overlays']
    if role=='character':
        assert first[0]['character_reference_paths']==second[0]['character_reference_paths']
        assert not first[0]['channel_overlays'] and not first[1]['channel_references']
    else:
        assert not first[0]['channel_references']
        assert first[0]['channel_overlays'][0]['path']==second[0]['channel_overlays'][0]['path']
        assert 'Do not draw it again' in first[0]['prompt']
    assert store.asset_path(asset).read_bytes()==raw
    assert design.content_profile(profile)['character_registry'][0]['aliases']==['Willy','кит']
    assert 'channel_characters' not in scenes[0]


def test_character_layer_requires_transparency_and_character_needs_explicit_binding(tmp_path):
    store=design.DesignStore(tmp_path);profile=store.save(appearance());image=picture(tmp_path)
    with pytest.raises(ValueError,match='прозрачным'):store.import_asset(profile['id'],1,image,'character_layer',{'aliases':['Willy'],'identity':'Whale'})
    with pytest.raises(ValueError,match='точных'):store.import_asset(profile['id'],1,image,'character',{'aliases':[],'identity':'Whale'})
    assert store.get(profile['id'])['revision']==1


def test_character_qa_compares_approved_image_and_rejects_changed_identity(tmp_path):
    store=design.DesignStore(tmp_path);profile=store.save(appearance());image=picture(tmp_path)
    profile=store.import_asset(profile['id'],1,image,'character',{'aliases':['Willy'],'identity':'Teal whale with a pale patch.'})
    scene=design.apply([{'index':0,'prompt':'Willy swims.'}],profile,tmp_path)[0]
    client=Mock();client.see_json.return_value={'ok':True,'score':8,'identity_ok':False,'issues':'wrong silhouette'}
    ok,score,issues=pipeline.qa_check(client,scene,str(image))
    assert not ok and issues.startswith('IDENTITY_MISMATCH:')
    assert 'approved recurring character references' in client.see_json.call_args.args[0]


def test_style_derivation_keeps_broad_grammar_not_original_artwork(tmp_path):
    visual={'description':'Source ink drawings','palette':['red'],'character_design':'Faceless humans'}
    client=Mock();client.chat_json.return_value={k:v for k,v in appearance().items() if k!='name'}
    result=design.adapt_visual(visual,'My channel','Teal palette and blank faces',client,tmp_path)
    assert result['description']!=visual['description'] and result['palette']==['#126B70']
    assert design.adapt_visual(visual,'My channel','Teal palette and blank faces',client,tmp_path)==result
    assert client.chat_json.call_count==1
    assert 'Faceless human figures must remain faceless' in client.chat_json.call_args.args[1]


def test_explicit_tts_language_is_sent_and_part_of_cache(tmp_path,monkeypatch):
    import subprocess
    pipeline.CANCEL.clear();calls=[]
    def synth(text,voice_id,out,progress=None,language=None):
        calls.append(language)
        subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-f','mp3',str(out)],check=True)
    speaker=Mock();speaker.synthesize=synth
    monkeypatch.setattr(pipeline,'make_tts',lambda *a:speaker)
    cfg={'_tts_language':'ja'};progress=lambda *a:None
    pipeline.make_voiceover(cfg,'短い文です。','edge','',str(tmp_path),progress)
    pipeline.make_voiceover(cfg,'短い文です。','edge','',str(tmp_path),progress)
    assert calls==['ja']
    pipeline.make_voiceover({'_tts_language':'zh'},'短い文です。','edge','',str(tmp_path),progress)
    assert calls==['ja','zh']


@pytest.mark.parametrize('engine',rewrite.ENGINES)
def test_every_rewrite_provider_receives_documentary_and_native_rules(tmp_path,monkeypatch,engine):
    prompts=[]
    def ask(e,key,model,prompt,*args):prompts.append(prompt);return 'Native original narration. '*30
    monkeypatch.setattr(rewrite,'_ask',ask)
    rewrite.rewrite('A complete reference narrative.',engine,'fixture','fixture-model',100,'немецком',str(tmp_path))
    assert len(prompts)==1 and rules.VERSION in prompts[0] and 'verb-final' in prompts[0]
    assert 'silently edit' in prompts[0] and 'Never invent' in prompts[0]


def test_original_draft_keeps_reference_presentation_and_adapts_target_language(tmp_path):
    from test_source_adaptation import SOURCE,PROFILE,client_fixture
    import source_adaptation
    profile=copy.deepcopy(PROFILE);profile['narration']={'delivery':delivery()}
    client,calls=client_fixture()
    _,result=source_adaptation.create(SOURCE,profile,{},tmp_path,client,language='русском')
    data=json.JSONDecoder().raw_decode(calls[-1].split('Reference JSON:\n')[1])[0]
    assert data['reference_presentation']['narrative_style']==profile['narrative_style']
    assert data['reference_presentation']['voice_direction']==delivery()
    assert result['narration_language']=='ru' and rules.VERSION in calls[-1]
    assert result['adaptation']['writing_rules']==rules.VERSION


def test_script_guard_detects_obvious_wrong_alphabet_without_claiming_fluency():
    assert rules.wrong_script('A long English narration with enough letters to make the target script mismatch obvious.','японском')
    assert not rules.wrong_script('Это достаточно длинный русский рассказ с естественными словами для проверки письменности.','русском')
    assert not rules.wrong_script('A long French or English sentence using the same alphabet cannot be distinguished this way.','французском')


@pytest.mark.parametrize('text,code',[
 ('これは日本語の自然なナレーションです。','ja'),('这是一段中文旁白。','zh'),
 ('이것은 한국어 내레이션입니다.','ko'),('यह हिन्दी की एक कहानी है।','hi'),
 ('هذه قصة باللغة العربية.','ar'),('זה סיפור בעברית.','he'),('Αυτή είναι μια ελληνική ιστορία.','el')])
def test_auto_voice_language_does_not_default_distinct_scripts_to_english(text,code):
    assert pipeline.detect_language(text)==code


def test_source_evidence_is_reused_when_target_language_changes(tmp_path):
    from test_source_adaptation import SOURCE,PROFILE,client_fixture
    import source_adaptation
    client,calls=client_fixture()
    source_adaptation.create(SOURCE,PROFILE,{},tmp_path,client,language='русском')
    base=client.chat_json.side_effect
    def english(system,prompt,**kwargs):
        value=base(system,prompt,**kwargs)
        if 'script' in value:value['script']='This native English narration explains the handle through a concrete question. Heat travels gradually through metal, so the moment of contact and the later sensation in the hand are separate parts of the explanation. The practical choice is a suitable handle rather than a mysterious property.'
        return value
    client.chat_json.side_effect=english
    source_adaptation.create(SOURCE,PROFILE,{},tmp_path,client,language='английском')
    assert len([p for p in calls if 'Extract the important claims' in p])==1


def test_own_style_learning_does_not_import_source_frames(tmp_path,monkeypatch):
    import app
    import source_analysis
    import youtube_source
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False,'service_switch':None})
    monkeypatch.setattr(app,'keep_awake',lambda *a:None)
    monkeypatch.setattr(source_analysis,'_provider',lambda *a:('elysium','fixture','vision'))
    monkeypatch.setattr(youtube_source,'video',lambda *a,**k:'source.mp4')
    source=picture(tmp_path)
    monkeypatch.setattr(source_analysis,'analyse_design',lambda *a,**k:{'visual_style':{'description':'Source ink drawing','palette':['red'],'character_design':'Blank faces'},'reference_frames':[str(source)]})
    client=Mock();client.chat_json.return_value={k:v for k,v in appearance().items() if k!='name'}
    monkeypatch.setattr(studio,'text_client',lambda *a,**k:client)
    app.save_cfg({})
    result=app.Api().channel_design_learn('Own channel','https://www.youtube.com/watch?v=Cs9lG-Meo1s',True,'Teal palette')
    assert result['ok'],result
    assert result['profile']['assets']==[] and result['profile']['description']!='Source ink drawing'
    assert app.load_cfg()['channel_design_id']==result['profile']['id']
    assert client.chat_json.call_count==1


def test_generated_own_profile_can_be_saved_without_source_shots(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(app,'CONFIG_PATH',str(tmp_path/'config.json'))
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path))
    profile={'content_only':True,'visual_style':{'description':'Own flat teal drawings','palette':['teal']},
             'narrative_style':'Curious and clear','narration':{'delivery':delivery()},'montage':{'shots':[]}}
    monkeypatch.setattr(app,'STATE',{**app.STATE,'running':False,'preparing':False,'queue_active':False,'service_switch':None,'source_profile':profile,'active_board_id':None,'storyboard_id':None})
    app.save_cfg({})
    result=app.Api().channel_design_capture('My original channel')
    assert result['ok'],result
    assert result['profile']['assets']==[] and result['profile']['presentation']=='Curious and clear'
    assert result['profile']['delivery']==delivery()
