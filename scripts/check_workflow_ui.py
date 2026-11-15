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
app.save_cfg({'source':'script','rewrite':True,'rewrite_source':'youtube','tts_service':'edge','image_providers':['fastgen'],'fastgen_api_key':'workflow-old-fastgen','llm_engine':'fastgen','stt_engine':'fastgen','lang':'ru'})
api=app.Api();calls=[];speech=[]
provider_access.models=lambda engine,key:['google/gemini-3.6-flash','anthropic/claude-sonnet-5.5','deepseek/deepseek-chat','seedream/4.5','kling-2.6'] if engine=='elysium' else ['claude-sonnet-5-5']
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
 section('voice');page.locator('#tts_voice').fill('ru-fixture');page.locator('#tts_voice').blur()
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
 browser.close()
print('Numbered workflow E2E passed: navigation never generates; keys unique; model roles filtered; voice preview isolates synthesis; layouts fit; data preserved.')
