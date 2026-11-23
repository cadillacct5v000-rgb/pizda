"""Native reference delivery, actual opening preview and Lumean billing regressions."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
import requests
import app
import pipeline
import narration_rules as rules
import source_narration
import speech_plan as speech
from tts_api import Lumean, TTSError

DELIVERY={'speech_present':True,'instructions':'Measured, conversational delivery. Brief pauses before each explanation, restrained curiosity and clear phrase endings.',
          'tempo_wpm':165,'pause_ms':350,'intonation_variation':.6,'energy':.4}
TEXTS={'ro':'În adâncul oceanului, lumina dispare. Cum reușește balena să găsească hrană? Răspunsul începe cu sunetul. ',
       'es':'En el fondo del océano, la luz desaparece. ¿Cómo encuentra alimento una ballena? La respuesta empieza con el sonido. ',
       'pt':'No fundo do oceano, a luz desaparece. Como a baleia encontra alimento? A resposta começa com o som. '}

@pytest.fixture(autouse=True)
def no_cancel():
    pipeline.CANCEL.clear();yield;pipeline.CANCEL.clear()

@pytest.fixture
def tone(tmp_path):
    out=tmp_path/'tone.mp3'
    pipeline.run_ffmpeg(['-f','lavfi','-i','sine=frequency=410:duration=42','-c:a','libmp3lame','-b:a','128k',str(out)])
    return out.read_bytes()

@pytest.mark.parametrize('locale',list(rules.LOCALES))
def test_native_plans_preserve_script_and_region(locale,tmp_path):
    code=rules.LOCALES[locale][0];text=TEXTS[code]*25
    plan=speech.build(text,code,locale,DELIVERY)
    assert ''.join(plan['passages'])==text.strip()
    assert locale in plan['prompt'] and rules.LOCALES[locale][2] in plan['prompt']
    assert DELIVERY['instructions'] in plan['prompt']
    key=speech.save(tmp_path,plan);assert speech.load(tmp_path,key)==plan
    assert speech.bind({'speech_plan_id':key,'speech_locales':{code:locale}},tmp_path,text)==plan
    with pytest.raises(ValueError,match='Сценарий изменился'):speech.bind({'speech_plan_id':key},tmp_path,text+' Another sentence.')
    assert rules.LOCALES[locale][2] in rules.writing('script',code,locale)
    assert rules.language_code(locale)==code


def test_romanian_rules_and_bad_locale_are_checked():
    assert 'ș' in rules.localisation('ro') and 'clitics' in rules.localisation('ro')
    with pytest.raises(ValueError,match='Регион'):speech.build(TEXTS['ro'],'ro','pt-BR',DELIVERY)
    with pytest.raises(ValueError,match='Письменность'):speech.build('Это русский текст, его нельзя просто объявить румынским. '*3,'ro','ro-RO',DELIVERY)
    with pytest.raises(ValueError,match='Голос Edge'):speech.check_voice('edge','fr-FR-HenriNeural','ro-RO')


def test_first30_and_full_reuse_the_same_synthesized_opening(tmp_path,monkeypatch,tone):
    calls=[]
    class Speaker:
        def synthesize(self,text,voice,out,progress=None,language=None):
            calls.append((text,voice,language,self.speech_locale,self.speech_prompt));Path(out).write_bytes(tone)
    monkeypatch.setattr(pipeline,'make_tts',lambda *a:Speaker())
    monkeypatch.setattr(pipeline,'data_dir',lambda:str(tmp_path))
    plan=speech.build(TEXTS['ro']*22,'ro','ro-RO',DELIVERY)
    key=speech.save(tmp_path,plan);cfg={'speech_plan_id':key,'tts_service':'edge'}
    progress=lambda *a:None
    sample=speech.render(tmp_path,plan,cfg,'edge','',progress,preview=True)
    assert 29.9<=pipeline.audio_duration(sample)<=30.1
    assert len(calls)==1 and calls[0][0]==plan['passages'][0]
    assert calls[0][2:4]==('ro','ro-RO')
    assert speech.render(tmp_path,plan,cfg,'edge','',progress,preview=True)==sample and len(calls)==1
    job=tmp_path/'job';job.mkdir()
    full=pipeline.make_voiceover(cfg,plan['script'],'edge','',str(job),progress)
    assert len(calls)==len(plan['passages']) and pipeline.audio_duration(full)>60
    assert calls[0][0]==plan['passages'][0]
    pipeline.make_voiceover(cfg,plan['script'],'edge','',str(job),progress)
    assert len(calls)==len(plan['passages'])
    speech.render(tmp_path,plan,cfg,'edge','ro-RO-AlinaNeural',progress,preview=True)
    assert len(calls)==len(plan['passages'])+1


def test_short_script_preview_is_short_not_padded(tmp_path,monkeypatch,tone):
    shorter=tmp_path/'short.mp3';pipeline.run_ffmpeg(['-i',str(tmp_path/'tone.mp3'),'-t','2','-c:a','libmp3lame',str(shorter)])
    def fake(cfg,text,service,voice,job,progress):return str(shorter)
    monkeypatch.setattr(pipeline,'make_voiceover',fake)
    # Return an audio path inside the expected segment directory, as production does.
    def synth(cfg,text,service,voice,job,progress):
        out=Path(job)/'voiceover.mp3';out.write_bytes(shorter.read_bytes());return str(out)
    monkeypatch.setattr(pipeline,'make_voiceover',synth)
    plan=speech.build(TEXTS['pt'],'pt','pt-BR',DELIVERY)
    out=speech.render(tmp_path,plan,{},'edge','',lambda *a:None,preview=True)
    assert 1.9<pipeline.audio_duration(out)<2.2


def response(value,status=200):
    return Mock(status_code=status,json=lambda:{'data':value},text=str(value))

@pytest.mark.parametrize('code',['ro','es','pt'])
def test_lumean_templates_language_and_reference_settings_are_matched(code):
    client=Lumean('fixture');client.source_delivery=rules.voice_direction(DELIVERY,code)
    old={'id':'old','name':client.TEMPLATE_NAME,'config':{'tts_settings':{'voice_id':'voice','model_id':'eleven_v4','language_code':'ru'}}}
    client.s.get=Mock(return_value=response([old]));client.s.post=Mock(return_value=response({'id':'new'}))
    assert client._template('voice',code)=='new'
    body=client.s.post.call_args.kwargs['json']['config']['tts_settings']
    assert body['language_code']==code and body['advanced_voice_settings'] is True
    assert body['voice_settings']['speed']==1 and body['voice_settings']['style']==.12
    client.s.get=Mock(return_value=response([{'id':'matching','name':client.TEMPLATE_NAME,'config':{'tts_settings':body}}]))
    client.s.post.reset_mock();assert client._template('voice',code)=='matching';client.s.post.assert_not_called()


def test_lumean_v3_stability_is_quantized_before_template_creation():
    client=Lumean('fixture');client.MODELS=('eleven_v3',);client.source_delivery=DELIVERY
    client.s.get=Mock(return_value=response([]));client.s.post=Mock(return_value=response({'id':'v3'}))
    client._template('voice','ro')
    assert client.s.post.call_args.kwargs['json']['config']['tts_settings']['voice_settings']['stability'] in (0.,.5,1.)


def test_lumean_unknown_submit_is_never_repeated(tmp_path):
    client=Lumean('fixture');client._template=Mock(return_value='template')
    client.s.post=Mock(side_effect=requests.ReadTimeout('lost receipt'))
    with pytest.raises(requests.ReadTimeout):client.synthesize(TEXTS['ro'],'voice',str(tmp_path/'audio.mp3'),language='ro')
    with pytest.raises(TTSError,match='не подтверждена'):client.synthesize(TEXTS['ro'],'voice',str(tmp_path/'audio.mp3'),language='ro')
    assert client.s.post.call_count==1


def test_lumean_resume_keeps_order_and_native_control_tags(tmp_path,monkeypatch,tone):
    monkeypatch.setattr('netutil.patient',lambda f:f())
    client=Lumean('fixture');client._template=Mock(return_value='template');client.source_delivery=DELIVERY
    client.speech_prompt='Full native reference plan';client.speech_locale='pt-BR';client.speech_notes='Emphasize the question.'
    client.s.post=Mock(return_value=response({'id':'paid-once'}));client.s.get=Mock(side_effect=requests.ReadTimeout('poll failed'))
    out=str(tmp_path/'audio.mp3')
    with pytest.raises(requests.ReadTimeout):client.synthesize(TEXTS['pt'],'voice',out,language='pt')
    body=client.s.post.call_args.kwargs['json'];assert body['input_text'].endswith(TEXTS['pt'])
    assert 'Brazilian Portuguese' in body['input_text'] and 'Emphasize the question.' in body['input_text']
    assert 'Full native reference plan' not in body['input_text']
    client.s.get=Mock(return_value=response({'status':'completed','result':{'files':['output/audio.mp3']}}))
    client.s.post=Mock(return_value=response({'url':'https://example.test/audio'}))
    downloaded=Mock();downloaded.raise_for_status=Mock();monkeypatch.setattr(requests,'get',Mock(return_value=downloaded))
    monkeypatch.setattr('netutil.save_response',lambda r,p:Path(p).write_bytes(tone))
    client.synthesize(TEXTS['pt'],'voice',out,language='pt')
    assert client.s.post.call_count==1 and client.s.post.call_args.args[0].endswith('/storage/url')
    assert Path(out).read_bytes()==tone


def test_elysium_and_lumean_do_not_skip_reference_audio(monkeypatch):
    analyse=Mock(return_value={'delivery':DELIVERY});monkeypatch.setattr(source_narration,'analyse',analyse)
    for service in ('elysium','lumean'):
        assert source_narration.attach({},'source.mp3',42,{'tts_service':service},'.')['narration']['delivery']==DELIVERY
    assert analyse.call_count==2


def test_gemini_gets_language_prompt_separate_from_verbatim_narration():
    plan=speech.build(TEXTS['es'],'es','es-ES',DELIVERY)
    prompt=source_narration.gemini_prompt(plan['script'],DELIVERY,plan['prompt'])
    assert '<narration>\n'+TEXTS['es'].strip()+'\n</narration>' in prompt and 'es-ES' in prompt
