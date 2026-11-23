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
app.save_cfg({'source':'script','rewrite':True,'rewrite_source':'youtube','rewrite_url':'https://www.youtube.com/watch?v=Cs9lG-Meo1s','rewrite_engine':'elysium','elysium_api_key':'fixture-elysium','elysium_model':'anthropic/claude-sonnet-5.5','youtube_analyze_video':False,'fastgen_api_key':'fixture-images','mode':'images','quality':'speed','tts_service':'lumean','lumean_api_key':'fixture-lumean',
              'tts_voice':'native-ro','image_providers':['fastgen'],'lang':'ru','rewrite_lang':'румынском','studio_configured':True})
api=app.Api();speech=[];calls=[];rewrites=[];renders=[]
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
def automatic_rewrite(options):
 rewrites.append(options)
 with app.LOCK:app.STATE.update(script=text,source_profile={'narration_language':'ro','narration':{'delivery':delivery}},source_profile_id='fixture-prepared',preparing=False,error=None)
def automatic_render(*args,**kwargs):
 renders.append((args,kwargs))
 with app.LOCK:app.STATE.update(running=False,error=None,result=str(root/'fixture-full.mp4'))
app.run_rewrite=automatic_rewrite;app.run_job=automatic_render

with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,**({'executable_path':os.environ['KADROGEN_BROWSER']} if os.environ.get('KADROGEN_BROWSER') else {}))
 page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.expose_function('callApi',call);page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
 page.goto((ROOT/'ui.html').as_uri());page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
 page.wait_for_selector('#screen4.active #wf_source_kind')
 assert page.evaluate('F.script')==''
 page.locator('[data-workflow="voice"]').click();page.wait_for_selector('#wf_speech_prepare:visible')
 assert page.input_value('#wf_voice_language')=='ro'
 assert page.locator('#wf_speech_panel [data-speech-region=ro]').input_value()=='ro-RO'
 page.click('#wf_speech_preview');page.wait_for_selector('#wf_speech_player audio',timeout=20000)
 assert len(rewrites)==1 and rewrites[0]['language']=='румынском'
 assert app.load_cfg()['speech_plan_id']
 assert len(speech)==1 and speech[0][1:]==('native-ro','ro','ro-RO'),speech
 assert 29.9<pipeline.audio_duration(app._VOICE_SAMPLE)<30.1
 page.click('#wf_speech_preview');page.wait_for_function('speechBusy===false')
 assert len(speech)==1
 assert page.evaluate('F.script')==text and len(rewrites)==1
 page.evaluate("workflowOpen('source')");page.wait_for_selector('#wf_auto_start:visible');page.click('#wf_auto_start')
 page.wait_for_function('document.getElementById("screen6").classList.contains("active")')
 assert len(renders)==1 and len(rewrites)==1,(len(renders),len(rewrites),calls,app.STATE.get('error'))
 args,kwargs=renders[0];assert args[7]==text.strip() and args[8] is None
 assert kwargs['config_snapshot']['_automatic_speech'] is True
 page.evaluate("workflowOpen('voice')");page.wait_for_selector('#wf_voice_language:visible')
 # Distinct regional options, same setting used when writing the script.
 for code,region in [('es','es-ES'),('pt','pt-PT')]:
  page.select_option('#wf_voice_language',code)
  page.wait_for_selector('#wf_speech_panel [data-speech-region='+code+']')
  page.select_option('#wf_speech_panel [data-speech-region='+code+']',region)
  page.wait_for_function('speechBusy===false')
  page.wait_for_function('async value=>(await pywebview.api.get_state()).config.speech_locales[value[0]]===value[1]',arg=[code,region])
 # A fresh link can also enter the full worker directly, without checkpoints.
 page.evaluate("F.script='';reviewSourceProfile=null;reviewProfileId=null;workflowOpen('source')")
 page.wait_for_selector('#wf_auto_start:visible');page.click('#wf_auto_start')
 page.wait_for_function('document.getElementById("screen6").classList.contains("active")')
 assert len(renders)==2 and renders[-1][0][8]['on'] is True
 assert renders[-1][1]['config_snapshot']['_automatic_speech'] is True
 assert calls.count('rewrite')==1 and calls.count('start')==2
 # Voice-only cancellation must never stop an unrelated full video.
 page.evaluate('speechBusy=true;workflowSpeechOwnTask=false');app.STATE['running']=True
 before=len(calls);page.evaluate('workflowStopVoice()')
 assert 'cancel' not in calls[before:] and app.STATE['running']
 app.STATE['running']=False;page.evaluate('speechBusy=false;workflowSpeechCancelled=false')
 assert not set(calls)&{'prepare_storyboard','youtube_sample'}
 assert not errors,errors
 print('Automatic UI PASS: link to native script to delivery to real 30-second MP3; full render starts in one click; ready text/audio reused; regional menus.')
 browser.close()
