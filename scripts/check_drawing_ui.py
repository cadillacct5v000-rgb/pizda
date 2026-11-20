"""Real browser + native API drawing flow; local fixture model, no paid requests."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TEST_DATA=tempfile.TemporaryDirectory(prefix='kadrogen-drawing-ui-');os.environ['KADROGEN_DATA_DIR']=TEST_DATA.name
import app
import drawing_plugins
import studio
from playwright.sync_api import sync_playwright

app.CONFIG_PATH=str(Path(TEST_DATA.name)/'config.json');app.check_update=lambda:None
app.save_cfg({'source':'script','tts_service':'edge','llm_engine':'anthropic','anthropic_api_key':'drawing-ui-fixture',
              'stt_engine':'assemblyai','assemblyai_api_key':'drawing-ui-fixture','studio_configured':True,'lang':'ru','qa_images':False})
api=app.Api();calls=[];model_calls=[]
client=Mock()
def answer(system,prompt,**kwargs):
    model_calls.append(prompt);data=json.loads(prompt.split('Reference JSON:\n')[1])
    if data['required_symbols']:return {'objects':[{'symbol':data['required_symbols'][0],'x':.1,'y':.1,'w':.7,'h':.6}]}
    return drawing_plugins.example()
client.chat_json.side_effect=answer;studio.text_client=lambda *a,**k:client

def call(name,args):calls.append(name);return getattr(api,name)(*args)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ['KADROGEN_BROWSER'])
    page=browser.new_page(viewport={'width':1366,'height':900});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.expose_function('callApi',call)
    page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
    page.goto((ROOT/'ui.html').as_uri());page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
    page.wait_for_selector('#workflow_rail',state='attached') if page.locator('#workflow_rail').count() else page.wait_for_selector('[data-workflow="style"]')
    page.locator('[data-workflow="style"]').click()
    page.get_by_role('button',name='Выбрать или сохранить дизайн канала',exact=True).click()
    page.wait_for_selector('#channel_design_dialog[open]')
    page.locator('#cd_name').fill('Проверенный стиль');page.locator('#cd_description').fill('Original faceless hand-drawn whale illustrations.')
    page.locator('#cd_drawing_advanced summary').click();page.locator('#cd_drawing_open').click();page.wait_for_selector('#drawing_plugins_dialog[open]')
    page.locator('#dp_example').click()
    page.wait_for_function("document.querySelectorAll('#dp_gallery video').length===3",timeout=90000)
    assert not model_calls
    page.locator('#dp_gallery .drawing-card').first.get_by_role('button',name='Выбрать этот вариант').click()
    page.wait_for_function("channelDesignDrawing?.style==='sketchbook' && channelDesignDrawing.motion==='draw'")
    page.locator('#dp_object_name').fill('Willy');page.locator('#dp_aliases').fill('наш кит, Willy, whale')
    page.locator('#dp_pin').click();page.wait_for_function('channelDesignDrawing.symbols.length===1')
    page.locator('#dp_brief').fill('Our whale swims past a boat.')
    page.locator('#dp_create').click();page.wait_for_function("document.querySelectorAll('#dp_gallery video').length===3 && !document.getElementById('dp_create').disabled",timeout=90000)
    assert len(model_calls)==1
    assert page.evaluate("drawingPreviewScene.objects[0].symbol===channelDesignDrawing.symbols[0].id")
    page.locator('#drawing_plugins_dialog').get_by_role('button',name='К настройкам и сохранению профиля',exact=True).click()
    page.locator('#channel_design_dialog').get_by_role('button',name='Сохранить и использовать',exact=True).click()
    page.wait_for_function("channelDesignEditing?.drawing?.symbols.length===1")
    profile=page.evaluate('channelDesignEditing');saved=api.channel_design_get(profile['id'],profile['revision'])
    assert saved['ok'] and saved['profile']['drawing']['motion']=='draw'
    page.locator('#channel_design_dialog').get_by_role('button',name='Закрыть',exact=True).click()
    page.locator('[data-workflow="images"]').click();page.wait_for_selector('#wf_drawing_active',state='visible')
    assert not page.locator('#providers').is_visible(),page.evaluate("({active:channelActiveDrawing?.enabled,display:document.getElementById('providers').style.display,computed:getComputedStyle(document.getElementById('providers')).display,hidden:document.getElementById('providers').hidden,workflow:workflowCurrent})")
    assert not set(calls)&{'start','rewrite','voice_sample','youtube_sample'}
    assert not errors,errors
    page.screenshot(path=str(Path(TEST_DATA.name)/'drawing-images-step.png'))
    # A later video reuses the approved symbol and the cached geometry without a new model call.
    drawing_plugins.plan(client,'Our whale swims past a boat.',saved['profile'],Path(TEST_DATA.name)/'drawing-plugins/scenes')
    assert len(model_calls)==1
    browser.close()
print('Drawing E2E passed: three five-second video variants, zero calls for example, one shared geometry call, fixed object, saved channel style, unused image services hidden, cross-video cache reused.')
