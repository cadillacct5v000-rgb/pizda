"""Exercise the production speech bridge and audio caching in real Chromium, no paid calls."""
import copy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
folder=tempfile.TemporaryDirectory(prefix='kadrogen-speech-ui-');root=Path(folder.name)
os.environ['KADROGEN_DATA_DIR']=str(root)
import app
import pipeline
import tts_api
from playwright.sync_api import sync_playwright
app.CONFIG_PATH=str(root/'config.json');app.check_update=lambda:None
app.save_cfg({'source':'script','rewrite':False,'tts_service':'lumean','lumean_api_key':'fixture-lumean',
              'tts_voice':'native-ro','image_providers':['fastgen'],'lang':'ru','rewrite_lang':'румынском','studio_configured':True})
api=app.Api();speech=[];calls=[]
tone=root/'tone.mp3';pipeline.run_ffmpeg(['-f','lavfi','-i','sine=frequency=400:duration=42','-c:a','libmp3lame',str(tone)])
class Speaker:
 def synthesize(self,text,voice,out,progress=None,language=None):
  speech.append((text,voice,language,self.speech_locale));Path(out).write_bytes(tone.read_bytes())
 def voices(self,language=None):return [{'id':'native-'+(language or 'ro'),'name':'Native '+(language or 'ro'),'lang':language or 'ro','gender':''}]
 def check(self):return 'Fixture'
pipeline.make_tts=lambda *a,**k:Speaker();tts_api.make_tts=pipeline.make_tts
pipeline.make_context=lambda *a:(_ for _ in ()).throw(AssertionError('No pictures in a voice preview'))
pipeline.make_text_engine=lambda *a:(_ for _ in ()).throw(AssertionError('No ASR or rewrite in a voice preview'))
def call(name,args):calls.append(name);return getattr(api,name)(*args)
delivery={'speech_present':True,'instructions':'Clear conversational voice, restrained curiosity, brief pauses before explanations, then a gentle downward phrase ending.','tempo_wpm':160,'pause_ms':400,'intonation_variation':.5,'energy':.4}
text='În adâncul oceanului, lumina dispare. Cum reușește balena să găsească hrană? Răspunsul începe cu sunetul. '*20
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['KADROGEN_BROWSER']} if os.environ.get('KADROGEN_BROWSER') else {}))
 page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.expose_function('callApi',call);page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
 page.goto((ROOT/'ui.html').as_uri());page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
 page.wait_for_selector('#screen4.active #wf_source_kind')
 page.evaluate("value=>{F.script=value.text;if(document.getElementById('script'))document.getElementById('script').value=value.text;reviewSourceProfile={narration:{delivery:value.delivery}};}",{'text':text,'delivery':delivery})
 page.locator('[data-workflow="voice"]').click();page.wait_for_selector('#wf_speech_prepare:visible')
 assert page.input_value('#wf_voice_language')=='ro'
 assert page.locator('#wf_speech_panel [data-speech-region=ro]').input_value()=='ro-RO'
 page.click('#wf_speech_prepare')
 try:page.wait_for_function("document.getElementById('wf_speech_prompt').textContent.includes('REFERENCE DELIVERY')")
 except Exception:
  print('STATUS',page.locator('#wf_speech_status').text_content(),app.STATE.get('error'),errors,flush=True);raise
 assert not speech and app.load_cfg()['speech_plan_id']
 page.click('#wf_speech_preview');page.wait_for_selector('#wf_speech_player audio',timeout=15000)
 assert len(speech)==1 and speech[0][1:]==('native-ro','ro','ro-RO'),speech
 assert 29.9<pipeline.audio_duration(app._VOICE_SAMPLE)<30.1
 page.click('#wf_speech_preview');page.wait_for_function('speechBusy===false')
 assert len(speech)==1
 assert page.evaluate('F.script')==text
 # Distinct regional options, same setting used when writing the script.
 for code,region in [('es','es-ES'),('pt','pt-PT')]:
  page.select_option('#wf_voice_language',code)
  page.wait_for_selector('#wf_speech_panel [data-speech-region='+code+']')
  page.select_option('#wf_speech_panel [data-speech-region='+code+']',region)
  page.wait_for_function('speechBusy===false')
  page.wait_for_function('async value=>(await pywebview.api.get_state()).config.speech_locales[value[0]]===value[1]',arg=[code,region])
 assert not set(calls)&{'start','rewrite','prepare_storyboard','youtube_sample'}
 assert not errors,errors
 print('Speech UI PASS: native reference plan, real 30-second MP3, repeat zero synthesis, unchanged script, regional menus.')
 browser.close()
