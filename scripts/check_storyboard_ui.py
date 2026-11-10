import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import tempfile
TEST_DATA=tempfile.TemporaryDirectory(prefix='kadrogen-ui-')
os.environ['KADROGEN_DATA_DIR']=TEST_DATA.name
from PIL import Image
import app
import pipeline
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
api=app.Api()
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
 page.evaluate('(data)=>{reviewSourceProfile=data.profile;reviewProfileId="test-profile";source="script";cur=5; document.querySelectorAll(".screen").forEach(e=>e.classList.remove("active"));document.getElementById("screen5").classList.add("active");renderReview(data.script);}',{'profile':profile,'script':script})
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
 assert launched[-1][-2]=={'start':2.,'seconds':20.}
 assert launched[-1][-3]['script'].startswith('A redesigned')
 assert launched[-1][-1] in (25,30,60)
 page.locator('#btn_board_return').click()
 page.wait_for_selector('#screen5.active #board_panel:not([hidden])')
 page.locator('#btn_go').click()
 page.wait_for_selector('#screen6.active')
 page.wait_for_function('document.getElementById("btn_again").disabled === false')
 assert len(launched)==2 and launched[-1][-2] is None
 page.locator('#btn_board_return').click()
 page.wait_for_selector('#screen5.active')
 page.evaluate('hideStoryboard()')
 page.locator('#rv_script').fill('A completely edited global narration.')
 assert page.evaluate('boardState') is None
 assert not errors,errors
 b.close()
print('Browser E2E passed: semantic selection, real API save, per-plan take, invalid preview guard, preview launch, full launch, return, stale-script invalidation.')
