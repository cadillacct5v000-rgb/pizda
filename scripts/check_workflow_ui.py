"""Exercise the numbered workflow with local API fixtures; no paid provider calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TEST_DATA=tempfile.TemporaryDirectory(prefix='kadrogen-workflow-')
os.environ['KADROGEN_DATA_DIR']=TEST_DATA.name
import app
import pipeline
import provider_access
import tts_api
from playwright.sync_api import sync_playwright

root=Path(TEST_DATA.name)
app.CONFIG_PATH=str(root/'config.json');app.check_update=lambda:None
app.save_cfg({'source':'script','rewrite':True,'rewrite_source':'youtube','tts_service':'edge','image_providers':['fastgen'],'fastgen_api_key':'workflow-old-fastgen','llm_engine':'fastgen','stt_engine':'fastgen','lang':'ru','studio_configured':True,'enhancement_level':'enhanced'})
api=app.Api();calls=[];speech=[]
from types import SimpleNamespace
from PIL import Image
character_file=root/'Willy.png';Image.new('RGB',(64,32),'#126B70').save(character_file)
app.webview=SimpleNamespace(OPEN_DIALOG='open');app.WINDOW=SimpleNamespace(create_file_dialog=lambda *a,**k:[str(character_file)])
import reference_animation
import youtube_source
motion_requests=[]
youtube_source.video=lambda *a,**k:'fixture-source.mp4'
def motion_fixture(*a,**k):
 motion_requests.append(a);return reference_animation.validate({'camera_styles':['static','push-in'],'subject_motion_share':0,'strength':.06,'instructions':'Restrained camera movement with static pauses.','confidence':'high'})
reference_animation.analyse=motion_fixture
provider_access.models=lambda engine,key:['google/gemini-3.6-flash','anthropic/claude-sonnet-5.5','anthropic/claude-opus-5.5','deepseek/deepseek-chat','seedream/4.5','kling-2.6'] if engine=='elysium' else ['claude-sonnet-5-5']
tone=root/'fixture.mp3'
subprocess.run([pipeline.ffmpeg_bin(),'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','libmp3lame','-b:a','128k',str(tone)],check=True)
class Speaker:
 def synthesize(self,text,voice_id,out,progress=None,language=None):
  speech.append((text,voice_id,language));Path(out).write_bytes(tone.read_bytes())
 def voices(self):return [{'id':'ru-fixture','name':'Russian fixture','lang':'ru','gender':''}]
 def check(self):return 'OK · fixture'
pipeline.make_tts=lambda *a,**k:Speaker();tts_api.make_tts=pipeline.make_tts
pipeline.make_text_engine=lambda *a:(_ for _ in ()).throw(AssertionError('No text/STT call for voice-only preview'))
pipeline.make_context=lambda *a:(_ for _ in ()).throw(AssertionError('No images for voice-only preview'))
def call(name,args):
 calls.append(name);return getattr(api,name)(*args)

with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['KADROGEN_BROWSER']} if os.environ.get('KADROGEN_BROWSER') else {}))
 page=browser.new_page(viewport={'width':1366,'height':768});errors=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.expose_function('callApi',call)
 page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
 page.goto((ROOT/'ui.html').as_uri());page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
 page.wait_for_selector('#screen4.active #wf_source_kind')
 def section(name):
  page.locator('[data-workflow="'+name+'"]').click()
  page.wait_for_selector('#screen5.active' if name=='board' else '#screen4.active')
  if name!='board':assert page.locator('[data-workflow-pane]:visible').count()==1
 # Navigation only changes the view. It must never generate a script, voice, or media.
 for name in ['source','style','text','voice','board','images','video','render','connections','queue']:section(name)
 assert not set(calls)&{'start','rewrite','storyboard_prepare','voice_sample','youtube_sample'}
 section('connections')
 assert page.locator('[data-key-provider=elysium] input.key').count()==1
 page.locator('[data-key-provider=elysium] input.key').fill('workflow-elysium-fixture');page.locator('[data-key-provider=elysium] input.key').blur()
 page.wait_for_function("async()=> (await pywebview.api.get_state()).config.has_elysium")
 page.locator('#replace_mode').select_option('on');page.wait_for_function('elysiumReplacement.elysium_replace_fastgen')
 assert app.load_cfg()['fastgen_api_key']=='workflow-old-fastgen'
 assert page.locator('[data-key-provider=fastgen]').count()==0
 # Opus is available in the source-analysis preset and stays independent of the writer model.
 section('style');page.select_option('#analysis_engine','elysium')
 page.wait_for_function("analysisEngine==='elysium' && document.getElementById('analysis_model')")
 assert page.locator('#analysis_model option[value="anthropic/claude-opus-5.5"]').count()==1
 writer_model=page.evaluate('models.elysium_model')
 page.select_option('#analysis_model','anthropic/claude-opus-5.5')
 page.wait_for_function("async()=>(await pywebview.api.get_state()).config.source_analysis_model==='anthropic/claude-opus-5.5'")
 import source_analysis
 engine,key,model=source_analysis._provider(app.load_cfg())
 assert engine=='elysium' and model=='anthropic/claude-opus-5.5' and key=='workflow-elysium-fixture'
 section('source');section('style')
 assert page.locator('#analysis_model').input_value()=='anthropic/claude-opus-5.5'
 assert page.evaluate('models.elysium_model')==writer_model
 # Show the effective video provider even when the saved legacy selection was 'providers'.
 section('video')
 assert page.locator('#video_engine').input_value()=='elysium'
 assert page.locator('#elysium_video_model').is_visible()
 # Source choices expose the appropriate input instead of an unrelated YouTube form.
 section('source');page.locator('#wf_source_kind').select_option('text')
 page.wait_for_selector('#script');assert not page.locator('#youtube_source_url').is_visible()
 page.locator('#script').fill('Это мой готовый сценарий. Проверьте русское произношение.')
 # Provider-specific fields: ElevenLabs direct does not show unrelated Gemini TTS choices.
 section('voice');page.locator('#tts_sel').select_option('elevenlabs')
 page.wait_for_selector('#tts_voice')
 assert not page.locator('#replace_elysium_tts_model').is_visible()
 assert not page.locator('#replace_elysium_tts_voice').is_visible()
 section('connections')
 key=page.locator('[data-key-provider=elevenlabs] input.key');assert key.count()==1
 key.fill('workflow-eleven-fixture');key.blur()
 page.wait_for_function("async()=> (await pywebview.api.get_state()).config.has_elevenlabs")
 section('voice');page.select_option('#wf_voice_language','ru');page.locator('#tts_voice').fill('ru-fixture');page.locator('#tts_voice').blur()
 page.locator('details').filter(has=page.locator('#wf_voice_text')).evaluate("e=>e.open=true")
 page.locator('#wf_voice_text').fill('Русская речь должна быть понятной. Ударения и интонация важны.')
 page.locator('#wf_voice_preview').click();page.wait_for_selector('#wf_voice_player audio',timeout=15000)
 assert len(speech)==1 and speech[0][1:] == ('ru-fixture','ru')
 assert not set(calls)&{'start','rewrite','storyboard_prepare','youtube_sample'}
 # Image QA menus contain vision chat models, not generators or text-only models.
 section('images');page.locator('#qa').click();page.wait_for_selector('#qa_eng')
 page.locator('#qa_eng').select_option('elysium');page.wait_for_function("qaEngine==='elysium'")
 page.locator('#qa_eng').locator('..').get_by_role('button',name='Модели с сервера',exact=True).click()
 page.wait_for_function("!!TASK_MODEL_OPTS['elysium:vision']")
 options=page.locator('#qa_model option').evaluate_all('rows=>rows.map(e=>e.value)')
 assert 'kling-2.6' not in options and 'seedream/4.5' not in options and 'deepseek/deepseek-chat' not in options
 page.locator('#qa_model').select_option('google/gemini-3.6-flash')
 section('text');page.locator('#elysium_model').locator('..').get_by_role('button',name='Модели с сервера',exact=True).click()
 page.wait_for_function("!!TASK_MODEL_OPTS['elysium:text']")
 options=page.locator('#elysium_model option').evaluate_all('rows=>rows.map(e=>e.value)')
 assert 'deepseek/deepseek-chat' in options and 'kling-2.6' not in options and 'seedream/4.5' not in options
 section('images');assert page.locator('#qa_model').input_value()=='google/gemini-3.6-flash'
 # Add one connection without duplicate keys in other working steps.
 section('connections');page.locator('#wf_connection_provider').select_option('anthropic');page.locator('#wf_add_connection').click()
 key=page.locator('[data-key-provider=anthropic] input.key');assert key.count()==1
 key.fill('workflow-anthropic-fixture');key.blur()
 page.wait_for_function("async()=> (await pywebview.api.get_state()).config.has_anthropic")
 assert page.locator('#dropzone input.key[data-k=elysium]').count()==1
 assert page.locator('#dropzone input.key[data-k=elevenlabs]').count()==1
 assert page.locator('#dropzone input.key[data-k=anthropic]').count()==1
 # A ready audio source does not show or trigger an unrelated TTS preview.
 section('source');page.locator('#wf_source_kind').select_option('audio')
 page.wait_for_selector('#file_name');section('voice')
 assert not page.locator('#wf_voice_text').is_visible()
 assert 'готовая озвучка' in page.locator('[data-workflow-pane=voice]').text_content()
 section('source');page.locator('#wf_source_kind').select_option('text');page.wait_for_selector('#script')
 page.locator('#helpbtn').click();page.wait_for_selector('#workflow_help_dialog[open]')
 assert 'ручной редактор голосовых акцентов пока не реализованы' in page.locator('#workflow_help_dialog').text_content()
 page.locator('#workflow_help_dialog').get_by_role('button',name='Закрыть',exact=True).click()
 assert len(speech)==1
 # Check useful narrow desktop layouts. No horizontal page overflow or hidden navigation.
 for width,height in [(1366,768),(1440,900),(900,800)]:
  page.set_viewport_size({'width':width,'height':height});section('voice')
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2'),(width,height)
  assert page.locator('[data-workflow=connections]').is_visible()
 page.set_viewport_size({'width':1366,'height':768});section('source')
 page.screenshot(path=str(root/'workflow-source.png'),full_page=True)
 assert not errors,errors
 # Choosing reference-led creation is independent of provider selection and never generates.
 section('source');page.select_option('#wf_source_kind','youtube')
 section('style');before=len(calls)
 page.select_option('#wf_approach','original')
 page.wait_for_function("document.getElementById('studio_level').value==='original'")
 page.wait_for_function("async()=>(await pywebview.api.get_state()).config.enhancement_level==='original'")
 assert page.locator('#youtube_mode').is_hidden()
 page.wait_for_function("document.getElementById('source_processing_note').textContent.includes('Самостоятельные сцены')")
 assert page.evaluate('opts().studio.enhancement_level')=='original'
 assert not any(name in ('rewrite','prepare_storyboard','start','youtube_sample') for name in calls[before:])
 assert not errors,errors
 # Save a reusable channel, a language-specific voice and a named recurring whale through the real UI.
 section('voice');page.select_option('#tts_sel','elevenlabs');page.wait_for_selector('#tts_voice')
 page.fill('#tts_voice','native-en-fixture');page.locator('#tts_voice').dispatch_event('change')
 section('style');page.get_by_role('button',name='Выбрать или сохранить дизайн канала',exact=True).click()
 page.wait_for_selector('#channel_design_dialog[open]')
 page.fill('#cd_name','Persistent whale channel');page.fill('#cd_description','Own flat teal drawings with featureless human figures.')
 page.fill('#cd_presentation','Curious, clear, with short reveals.')
 page.select_option('#cd_voice_language','en');page.get_by_role('button',name='Закрепить голос, выбранный на шаге 4',exact=True).click()
 page.get_by_role('button',name='Сохранить и использовать',exact=True).click()
 page.wait_for_function("channelDesignEditing?.id && Object.keys(channelDesignEditing.voices).includes('en')")
 assert page.evaluate("channelDesignEditing.voices.en.id")=='native-en-fixture'
 page.select_option('#cd_role','character');page.wait_for_selector('#cd_character_fields:not([hidden])')
 page.fill('#cd_aliases','Willy, кит Вилли');page.fill('#cd_identity','One teal whale with a pale left patch and rounded fins.')
 page.get_by_role('button',name='Добавить файл',exact=True).click()
 page.wait_for_function("channelDesignEditing?.assets.some(a=>a.role==='character')")
 assert page.evaluate("channelDesignEditing.assets[0].aliases")==['Willy','кит Вилли']
 assert page.evaluate("channelDesignEditing.presentation")=='Curious, clear, with short reveals.'
 page.locator('#channel_design_dialog').get_by_role('button',name='Закрыть',exact=True).click()
 section('voice');page.select_option('#wf_voice_language','en')
 page.wait_for_function("F.voice==='native-en-fixture'")
 page.locator('details').filter(has=page.locator('#wf_voice_text')).evaluate("e=>e.open=true")
 page.fill('#wf_voice_text','This English sample uses clear native sentences and a natural spoken rhythm.')
 page.click('#wf_voice_preview');page.wait_for_function("document.getElementById('wf_voice_status').textContent.includes('Прослушайте')")
 assert len(speech)==2 and speech[-1][1:] == ('native-en-fixture','en'),speech
 assert not errors,errors
 # Learning only motion neither rewrites nor generates media, and the saved channel reuses it.
 section('style');page.get_by_role('button',name='Выбрать или сохранить дизайн канала',exact=True).click()
 page.wait_for_selector('#channel_design_dialog[open]');page.fill('#cd_source_url','https://www.youtube.com/watch?v=Cs9lG-Meo1s')
 previous_revision=page.evaluate('channelDesignEditing.revision');before=len(calls)
 page.get_by_role('button',name='Изучить только оживление по ссылке · платно',exact=True).click()
 page.wait_for_function("channelDesignMotion?.confidence==='high'")
 assert len(motion_requests)==1 and page.evaluate('channelDesignEditing.revision')==previous_revision
 assert not any(name in ('rewrite','start','voice_sample','prepare_storyboard') for name in calls[before:])
 page.get_by_role('button',name='Сохранить и использовать',exact=True).click()
 page.wait_for_function("channelDesignEditing?.reference_animation?.confidence==='high'")
 page.locator('#channel_design_dialog').get_by_role('button',name='Закрыть',exact=True).click()
 section('video');page.select_option('#animation_strategy','reference')
 page.wait_for_function("async()=>(await pywebview.api.get_state()).config.animation.strategy==='reference'")
 preview=page.evaluate("async()=>await pywebview.api.animation_preview([{id:'scene',prompt:'Willy swims.'}],opts())")
 assert preview['ok'] and preview['count']==0,preview
 assert len(motion_requests)==1 and not errors,errors
 browser.close()
print('Numbered workflow E2E passed: navigation never generates; keys unique; model roles filtered; voice preview isolates synthesis; layouts fit; data preserved.')
