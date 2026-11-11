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
 data=json.loads(user.split('Reference JSON:\n')[1])
 return {'plans':[{'index':u['index'],'source_shot':1 if 'spaceship' in u['narration'] else 0,'confidence':'high',
                  'reason':'Содержание фразы совпадает с предметом в кадре','prompt':'New original redesign: '+u['narration'],'motion':'slow camera pan'}
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
 b=p.chromium.launch(headless=True)
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
 assert not errors,errors
 b.close()
print('Browser E2E passed: semantic selection, real API save, per-plan take, invalid preview guard, preview launch, full launch, return, stale-script invalidation.')
