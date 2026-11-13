import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import tempfile
import zipfile
from types import SimpleNamespace
TEST_DATA=tempfile.TemporaryDirectory(prefix='kadrogen-ui-')
os.environ['KADROGEN_DATA_DIR']=TEST_DATA.name
from PIL import Image
import app
import pipeline
import source_analysis
from playwright.sync_api import sync_playwright

root=Path(os.environ['KADROGEN_DATA_DIR']);root.mkdir(exist_ok=True)
refs=[]
for i,color in enumerate(['red','blue']):
 p=root/f'reference{i}.jpg';Image.new('RGB',(320,180),color).save(p);refs.append(str(p))
profile={'visual_style':{'description':'cinematic painting','palette':['red','blue'],'composition':'wide','shot_types':['wide']},
         'narrative_style':'story','sampling':{'duration':10.,'frame_count':6},
         'montage':{'duration':10.,'shots':[{'index':i,'start':i*5.,'end':(i+1)*5.,'duration':5.,'description':desc,
              'redesign_prompt':desc,'motion':'static camera','reference_frame':refs[i],'transition_in':{'type':'cut','duration':0.}}
              for i,desc in enumerate(['A cat inside a red room.','A spaceship near a blue planet.'])]}}
script='The spaceship slowly approaches the blue planet today. The cat waits patiently inside a red room.'
app.save_cfg({'source':'script','tts_service':'edge','image_providers':['fastgen'],'fastgen_api_key':'browser-fixture',
              'llm_engine':'fastgen','stt_engine':'fastgen','music_volume':0,'lang':'ru','rewrite':True})
app.STATE.update(source_profile=profile,source_profile_id='test-profile',script=script,running=False,preparing=False,queue_active=False)
def chat(self,system,user,**kwargs):
 data=json.JSONDecoder().raw_decode(user.split('Reference JSON:\n')[1])[0]
 if 'source' in data:return {'script':data['source']}
 if 'script' in data:return {'titles':['Title '+str(i) for i in range(5)],'hooks':['One','Two','Three'],'thumbnail_concepts':[{'concept':'Ink cat','image_prompt':'Original ink cat illustration','overlay_text':'Heat'} for _ in range(3)],'experiment_plan':['Compare titles','Compare thumbnails','Measure sufficient data']}
 return {'plans':[{'index':u['index'],'source_shot':1 if 'spaceship' in u['narration'] else 0,'confidence':'high',
                  'reason':'Содержание фразы совпадает с предметом в кадре','prompt':'New original redesign: '+u['narration'],'motion':'slow camera pan', 'editorial_role':'explain','shot_size':'detail','overlay_text':'Cooling'}
                 for u in data['passages']]}
pipeline.LLM.chat_json=chat
launched=[]
def run_job(*args):
 launched.append(args)
 with app.LOCK:
  app.STATE.update(running=False,stage='done',done=1,total=1,result=str(root/'fixture.mp4'),ended_at=time.time())
app.run_job=run_job
app.fg_limits=lambda key:(500,10,0)
api=app.Api()
docx_file=root/'fixture.docx'
with zipfile.ZipFile(docx_file,'w') as archive:
 archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>S01.01</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Imported narration for the forest scene.</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Original cinematic forest.</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
app.webview=SimpleNamespace(OPEN_DIALOG='open')
app.WINDOW=SimpleNamespace(create_file_dialog=lambda *args,**kwargs:[str(docx_file)])
vision_access_response={'capabilities':{'chat.multimodal':False}}
access_requests=[]
def capability_request(method,url,**kwargs):
 assert method=='GET'
 access_requests.append(method)
 return vision_access_response
source_analysis._http=capability_request
def call(name,args):
 return getattr(api,name)(*args)

with sync_playwright() as p:
 b=p.chromium.launch(headless=True, **({'executable_path':os.environ['KADROGEN_BROWSER']} if os.environ.get('KADROGEN_BROWSER') else {}))
 page=b.new_page(viewport={'width':1440,'height':1100})
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.expose_function('callApi',call)
 page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
 page.goto((ROOT/'ui.html').as_uri())
 page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
 page.wait_for_function('typeof STYLES !== "undefined" && STYLES.length > 0')
 page.evaluate('(data)=>{reviewSourceProfile=data.profile;reviewProfileId="test-profile";source="script";$("use_rw").checked=false;styleKey="custom";$("custom_style").value="";document.querySelector("input[name=mode][value=images]").checked=true;cur=5; document.querySelectorAll(".screen").forEach(e=>e.classList.remove("active"));document.getElementById("screen5").classList.add("active");renderReview(data.script);}',{'profile':profile,'script':script})
 assert page.locator('#rv_mode').input_value()=='images'
 assert page.locator('#rv_style').is_disabled()
 assert 'Стиль исходного ролика' in page.locator('#rv_style').text_content()
 assert page.evaluate('opts().source_profile_id')=='test-profile'
 page.locator('#board_create').click()
 page.wait_for_selector('.board-row',timeout=20000)
 assert page.locator('.board-row').count()==2
 first=page.locator('.board-row').first
 assert first.locator('[data-field=source_shot]').input_value()=='1'
 page.wait_for_function('document.querySelector("[data-reference]").src.startsWith("data:image")')
 first.locator('[data-field=narration]').fill('A redesigned spaceship reaches this blue planet.')
 first.locator('[data-field=duration]').fill('3')
 page.evaluate('saveStoryboard()')
 page.wait_for_function('boardDirty === false && boardState.script.startsWith("A redesigned")')
 first.locator('[data-take]').click()
 page.wait_for_function('boardState.rows[0].take === 1')
 assert page.evaluate('boardState.rows[1].take')==0
 source_board_id=page.evaluate('boardState.id')
 (ROOT/'test-results').mkdir(exist_ok=True)
 page.screenshot(path=str(ROOT/'test-results/storyboard-editor.png'),full_page=True)
 page.locator('#preview_start').fill('-1')
 page.locator('#board_preview').click()
 page.wait_for_function('document.getElementById("s_prep2").textContent.includes("Предпросмотр")')
 assert len(launched)==0
 page.locator('#preview_start').fill('2')
 page.locator('#preview_seconds').select_option('20')
 page.locator('#board_preview').click()
 page.wait_for_selector('#screen6.active')
 page.wait_for_selector('#btn_board_return:not([hidden])')
 assert len(launched)==1
 assert launched[-1][2]=='images'
 assert launched[-1][-2]=={'start':2.,'seconds':20.}
 assert launched[-1][-3]['script'].startswith('A redesigned')
 assert launched[-1][-1] in (25,30,60)
 page.locator('#btn_board_return').click()
 page.wait_for_selector('#screen5.active #board_panel:not([hidden])')
 page.locator('#btn_go').click()
 page.wait_for_selector('#screen6.active')
 page.wait_for_function('document.getElementById("btn_again").disabled === false')
 assert len(launched)==2 and launched[-1][-2] is None
 assert launched[-1][2]=='images'
 page.locator('#btn_board_return').click()
 page.wait_for_selector('#screen5.active')
 page.evaluate('hideStoryboard()')
 page.locator('#rv_script').fill('A completely edited global narration.')
 assert page.evaluate('boardState') is None
 page.locator('#projectsbtn').click()
 page.get_by_role('button',name='Импорт DOCX',exact=True).click()
 page.wait_for_function('boardState && boardState.rows[0].prompt === "Original cinematic forest."')
 assert page.locator('.board-row').count()==1
 page.locator('#projectsbtn').click()
 page.locator('#project_title').fill('Unified native project')
 page.locator('#project_save').click()
 page.wait_for_function('desktopProject && desktopProject.title === "Unified native project"')
 assert (root/'projects.sqlite').exists()
 page.locator('[data-open-project]').click()
 page.wait_for_selector('#screen5.active')
 assert page.evaluate('boardState.script')=='Imported narration for the forest scene.'
 page.evaluate('async () => {reviewSourceProfile=null;reviewProfileId=null;resetStoryboard();await restoreStoryboard();}')
 assert page.locator('#rv_style').is_disabled()
 assert 'Стиль исходного ролика' in page.locator('#rv_style').text_content()
 assert page.locator('#rv_mode').input_value()=='images'

 assert page.locator('input[name=prov][value=syntx]').count()==1
 page.evaluate("() => { $('use_rw').checked=true; rwSrc='youtube'; analyzeVideo=true; analysisEngine='fastgen'; go(4); }")
 page.wait_for_selector('#screen4.active #analysis_access_check')
 page.locator('#analysis_access_check').click()
 try:
  page.wait_for_function('document.getElementById("s_analysis").textContent.includes("chat.multimodal=false")', timeout=10000)
 except Exception:
  print('Diagnostic UI:',page.locator('#s_analysis').text_content(),'GET requests:',access_requests,'JS errors:',errors)
  raise
 vision_access_response['capabilities']={}
 page.locator('#analysis_access_check').click()
 page.wait_for_function('document.getElementById("s_analysis").textContent.includes("не сообщил")')
 assert access_requests==['GET','GET']
 # Gateway settings: a single Elysium key stays synchronized across all four tasks.
 import provider_access
 catalog_calls=[]
 def gateway_models(engine,key):
  catalog_calls.append((engine,key))
  assert engine=='elysium' and key=='gateway-browser-fixture'
  return ['gemini-vision-fixture','claude-text-fixture']
 provider_access.models=gateway_models
 page.evaluate("async () => { await pywebview.api.save_config({llm_engine:'elysium', qa_engine:'elysium', qa:true, rewrite_engine:'elysium', source_analysis_engine:'elysium'}); llm='elysium';qaEngine='elysium';qaModel='';rwEngine='elysium';analysisEngine='elysium';analysisModel='';$('use_qa').checked=true;renderAccess(); }")
 page.locator('#k_qa').fill('gateway-browser-fixture')
 assert page.locator('#k_llm').input_value()=='gateway-browser-fixture'
 assert page.locator('#k_analysis').input_value()=='gateway-browser-fixture'
 assert page.locator('#k_rw').input_value()=='gateway-browser-fixture'
 page.locator('#qa_eng').locator('..').get_by_role('button',name='Модели с сервера',exact=True).click()
 page.wait_for_function("document.querySelector('#qa_model option[value=gemini-vision-fixture]') !== null")
 page.locator('#qa_model').select_option('gemini-vision-fixture')
 page.evaluate('saveKeys()')
 cfg=app.load_cfg()
 assert cfg['elysium_api_key']=='gateway-browser-fixture'
 assert cfg['qa_model']=='gemini-vision-fixture'
 assert cfg['llm_engine']=='elysium' and cfg['source_analysis_engine']=='elysium'
 assert len(catalog_calls)==1
 page.evaluate('renderAccess()')
 assert page.locator('#qa_model').input_value()=='gemini-vision-fixture'
 # Custom API URL must be visible even if OpenAI is used only for image selection.
 page.evaluate("() => {qaEngine='openai';llm='fastgen';rwEngine='fastgen';analysisEngine='fastgen';renderAccess();}")
 assert page.locator('input.base[data-svc=openai]').count()==1
 # Studio settings and measured checks run through the real native bridge.
 page.evaluate('openStudio()')
 page.wait_for_selector('#studio_dialog[open]')
 assert page.locator('audio').count()==0
 assert page.get_by_text('СДЕЛАНО ГРИШЕЙ ЮТУБЕРОМ',exact=False).count()==0
 page.locator('#studio_captions').select_option('focus')
 page.locator('#studio_pace').select_option('dynamic')
 page.locator('#studio_apply').click()
 page.wait_for_function('document.getElementById("studio_status").textContent.includes("сохранены")')
 assert app.load_cfg()['caption_style']=='focus'
 assert page.evaluate('opts().studio.editing_pace')=='dynamic'
 page.locator('#studio_report_btn').click()
 page.wait_for_selector('#studio_output ul')
 assert 'не прогноз' in page.locator('#studio_output').text_content()
 page.set_viewport_size({'width':1366,'height':768})
 page.screenshot(path=str(ROOT/'test-results/studio-interface.png'),full_page=True)
 assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
 page.evaluate('async id => {const value=await pywebview.api.get_storyboard(id);boardState=value.board;boardDirty=false;F.script=boardState.script;renderReview(F.script);}',source_board_id)
 # Use fixture model responses to exercise async native workers, no paid calls.
 page.evaluate("async () => {await pywebview.api.save_config({llm_engine:'fastgen'});llm='fastgen';}")
 page.locator('#studio_package_btn').click()
 page.wait_for_function('document.getElementById("studio_status").textContent.includes("Упаковка готова")',timeout=15000)
 assert page.locator('#studio_output textarea').count()==3
 page.locator('#studio_board_btn').click()
 try:
  page.wait_for_function('document.getElementById("studio_status").textContent.includes("отдельной версией")',timeout=15000)
 except Exception:
  print('Studio diagnostic:',page.locator('#studio_status').text_content(),app.STATE.get('studio_task'),errors)
  raise
 assert page.evaluate('boardState.rows[0].shot_size')=='detail'
 assert page.evaluate('boardState.creative_direction.editing_pace')=='dynamic'
 page.locator('#studio_script_btn').click()
 page.wait_for_function('document.getElementById("studio_status").textContent.includes("Новая версия сценария")',timeout=15000)
 assert page.evaluate('boardState') is None
 page.evaluate('closeStudio()')
 page.locator('#projectsbtn').click()
 page.locator('#project_save').click()
 page.wait_for_function('document.getElementById("project_status").textContent.includes("сохранён")')
 page.evaluate("document.getElementById('studio_pace').value='calm'")
 page.locator('[data-open-project]').click()
 page.wait_for_function("document.getElementById('studio_pace').value==='dynamic'")
 assert app.load_cfg()['caption_style']=='focus'
 # The persistent YouTube entry works even when the rewrite option is currently off.
 rewritten=[]
 def fixture_rewrite(rw):
  rewritten.append(rw)
  with app.LOCK:
   app.STATE.update(preparing=False,error=None,cancelled=False,script=script,source_profile=profile,source_profile_id='fresh-youtube-fixture')
 app.run_rewrite=fixture_rewrite
 page.evaluate("() => {rwEngine='fastgen';source='script';F.script='';$('use_rw').checked=false;go(4);}")
 page.wait_for_selector('#screen4.active #youtube_source_url')
 assert page.locator('#youtube_source_url').is_visible()
 assert page.locator('#script').input_value()==''
 page.locator('#youtube_source_url').fill('https://www.youtube.com/watch?v=fixture_new')
 page.locator('#youtube_prepare').click()
 page.wait_for_function('boardState && boardState.creative_direction.enhancement_level === "enhanced"',timeout=15000)
 assert len(rewritten)==1 and rewritten[0]['url'].endswith('fixture_new')
 assert rewritten[0]['source']=='youtube' and rewritten[0]['montage']
 assert 'Улучшенная' in page.locator('#board_count').text_content()
 # A failed Studio upgrade must not silently render the previously open storyboard.
 previous=page.evaluate('boardState.id');before=len(launched)
 def broken_chat(self,system,user,**kwargs):
  units=json.JSONDecoder().raw_decode(user.split('Reference JSON:\n')[1])[0]['passages']
  return {'plans':[{'index':u['index'],'source_shot':0,'prompt':'Original ink illustration'} for u in units]}
 pipeline.LLM.chat_json=broken_chat
 page.evaluate('openStudio()')
 page.locator('#studio_level').select_option('director')
 page.locator('#studio_board_btn').click()
 page.wait_for_function('document.getElementById("studio_status").textContent.includes("Не приняты планы")',timeout=15000)
 page.evaluate('closeStudio()')
 assert page.evaluate('boardState.id')==previous
 assert page.locator('#btn_go').is_disabled()
 page.evaluate('startFromReview()')
 assert len(launched)==before
 assert 'прежняя версия' in page.locator('#s_prep2').text_content()
 # Choosing a saved version explicitly restores that version's controls.
 page.evaluate('restoreStoryboard()')
 page.wait_for_function('document.getElementById("btn_go").disabled === false')
 # Direct 30-second trial has its own worker and never prepares the full source.
 import copy
 import storyboard as sb
 sample_requests=[]
 def fixture_sample(opts,cfg,start,seconds):
  sample_requests.append((opts,start,seconds))
  limited=copy.deepcopy(profile);limited['sample']={'start':start,'requested_seconds':seconds,'source_seconds':seconds,'source_id':'dQw4w9WgXcQ'}
  result=sb.seal({'version':1,'script':script,'profile':limited,'rows':[{'id':'plan-00000','narration':script,'source_shot':0,'confidence':'high','reason':'fixture','prompt':'Original ink sample','motion':'static','take':0,'duration':None}]})
  with app.LOCK:
   app.store_storyboard(result)
   app.STATE.update(running=False,result=str(root/'sample.mp4'),script=script,active_board_id=result['id'],sample_board_id=result['id'],sample_info=limited['sample'],stage='done',ended_at=time.time())
 app.run_youtube_sample=fixture_sample
 page.evaluate("async () => {await pywebview.api.save_config({qa:false});$('use_qa').checked=false;$('render_fps').value='60';go(4);}")
 page.wait_for_selector('#screen4.active #youtube_sample')
 page.locator('#youtube_source_url').fill('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
 page.locator('#youtube_sample_start').fill('-1')
 page.locator('#youtube_sample').click()
 assert len(sample_requests)==0
 page.locator('#youtube_sample_start').fill('0')
 page.evaluate("document.getElementById('dropzone').scrollTop=0")
 page.screenshot(path=str(ROOT/'test-results/youtube-sample-entry.png'),full_page=True)
 page.locator('#youtube_sample').click()
 page.wait_for_selector('#screen6.active')
 page.wait_for_selector('#btn_board_return:not([hidden])')
 assert len(sample_requests)==1 and sample_requests[0][1:]==(0.,30.)
 assert sample_requests[0][0]['render_fps']==60
 assert len(rewritten)==1, 'The trial must not run the full-source rewrite worker'
 page.locator('#btn_board_return').click()
 page.wait_for_selector('#screen5.active')
 assert page.evaluate('boardState.sample.source_seconds')==30
 assert 'Проба' in page.locator('#board_count').text_content()
 # Video provider has independent settings and must preserve image/TTS selections.
 page.evaluate('go(4)')
 page.wait_for_selector('#video_engine')
 page.locator('#video_engine').select_option('elysium')
 page.wait_for_selector('#elysium_video_resolution')
 page.locator('#elysium_video_resolution').select_option('1080p')
 page.wait_for_function("elysiumVideoResolution === '1080p'")
 page.wait_for_timeout(300)
 assert app.load_cfg()['video_engine']=='elysium'
 assert app.load_cfg()['elysium_video_resolution']=='1080p'
 assert app.load_cfg()['image_providers']==['fastgen']
 page.evaluate('loadConfig(true)')
 page.wait_for_selector('#k_video_elysium')
 assert page.locator('#video_engine').input_value()=='elysium'
 assert page.locator('#elysium_video_model').input_value()=='google/gemini-omni-flash-1.2'
 page.locator('#elysium_video_model').select_option('kling-3.0/video')
 page.wait_for_selector('#elysium_kling_mode')
 assert page.locator('#elysium_video_resolution').count()==0
 page.locator('#elysium_kling_mode').select_option('pro')
 page.wait_for_function("elysiumKlingMode === 'pro'")
 page.wait_for_timeout(300)
 page.evaluate('loadConfig(true)')
 page.wait_for_selector('#elysium_kling_mode')
 assert page.locator('#elysium_video_model').input_value()=='kling-3.0/video'
 assert page.locator('#elysium_kling_mode').input_value()=='pro'
 assert app.load_cfg()['elysium_video_model']=='kling-3.0/video'
 page.locator('#elysium_video_model').select_option('hailuo/2-3-image-to-video-standard-provider-4')
 page.wait_for_function("elysiumVideoModel === 'hailuo/2-3-image-to-video-standard-provider-4'")
 page.wait_for_timeout(300)
 page.evaluate('loadConfig(true)')
 page.wait_for_selector('#elysium_video_model')
 assert page.locator('#elysium_video_model').input_value()=='hailuo/2-3-image-to-video-standard-provider-4'
 assert page.locator('#elysium_video_resolution').count()==0
 assert page.locator('#elysium_kling_mode').count()==0
 assert 'data URI' in page.locator('#dropzone').text_content()
 assert app.load_cfg()['elysium_video_model']=='hailuo/2-3-image-to-video-standard-provider-4'

 assert app.load_cfg()['image_providers']==['fastgen']
 assert not errors,errors
 b.close()
print('Browser E2E passed: source-aware preview/full render, Studio workers, durable settings, always-visible YouTube URL to automatic enhanced storyboard, failed upgrade blocks implicit old-board render, explicit saved-version recovery, bounded 30-second source trial and marked result-board navigation.')
