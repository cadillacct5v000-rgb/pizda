"""Reference-derived style comparison through the real UI and native API; no paid calls."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
TMP=tempfile.TemporaryDirectory(prefix='kadrogen-source-style-ui-');os.environ['KADROGEN_DATA_DIR']=TMP.name
import app
import studio
import pipeline
from playwright.sync_api import sync_playwright

root=Path(TMP.name);app.CONFIG_PATH=str(root/'config.json');app.check_update=lambda:None
app.save_cfg({'source':'script','tts_service':'edge','llm_engine':'anthropic','anthropic_api_key':'ui-fixture',
              'fastgen_api_key':'ui-image-fixture','studio_configured':True,'lang':'ru','qa_images':False})
reference=root/'reference.png';Image.new('RGB',(320,180),'#456789').save(reference)
visual={'description':'Painted gouache shapes with thick irregular contours and layered backgrounds.',
        'palette':['#456789','#f1d2ac'],'character_design':'Faceless human actors; mascot features stay simple.'}
app.STATE.update(source_profile={'visual_style':visual,'montage':{'shots':[{'reference_frame':str(reference)}]}},script='Unchanged narration.',running=False,preparing=False,queue_active=False)
model=Mock();model.chat_json.return_value={'variants':[{'name':name,'description':'Preserve gouache; '+name,'palette':palette,
    'font':'Arial' if i!=1 else 'Intro Demo Black CAPS','changes':{'palette':name+' accents','spacing':'Clearer own spacing'}}
    for i,(name,palette) in enumerate([('Прохладный свет',['#456789','#d3e5ef']),('Тёплый акцент',['#986643','#f1d2ac']),('Глубокий контраст',['#203a49','#b1c8ce'])])]}
studio.text_client=lambda *a,**k:model;images=[];calls=[];api=app.Api()
def generate(scene,out,progress,refs):
    images.append(scene['plan_id']);assert refs==[str(reference.resolve())]
    data=json.loads(scene['prompt'].split('Reference JSON:\n')[1]);assert data['reference_grammar']==visual
    assert data['same_scene']=='Our own whale near a boat.'
    Image.new('RGB',(320,180),data['own_style']['palette'][0]).save(out);return 'fixture-image'
pipeline.make_context=lambda *a,**k:{'providers':['fastgen'],'image_gen':{'fastgen':generate}}
def call(name,args):calls.append(name);return getattr(api,name)(*args)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=os.environ['KADROGEN_BROWSER']);page=browser.new_page(viewport={'width':1366,'height':900})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)));page.expose_function('callApi',call)
    page.add_init_script('window.pywebview={api:new Proxy({}, {get:(_,name)=>(...args)=>window.callApi(name,args)})};')
    page.goto((ROOT/'ui.html').as_uri());page.evaluate("window.dispatchEvent(new Event('pywebviewready'))")
    page.wait_for_selector('[data-workflow="style"]');page.locator('[data-workflow="style"]').click()
    page.get_by_role('button',name='Выбрать или сохранить дизайн канала',exact=True).click()
    page.locator('#cd_style_variants_open').click();page.wait_for_selector('#source_style_variants_dialog[open]')
    page.select_option('#sv_source','analysis');page.locator('#sv_changes').fill('Keep gouache, own palette and labels.')
    page.locator('#sv_prepare').click();page.wait_for_function("sourceStyleComparison?.variants.length===3 && !sourceStyleBusy")
    assert model.chat_json.call_count==1 and not images
    assert not page.locator('#cd_drawing_advanced').evaluate('e=>e.open')
    page.locator('#sv_brief').fill('Our own whale near a boat.');page.locator('#sv_caption').fill('Одна и та же сцена')
    page.locator('#sv_preview').click();page.wait_for_function("document.querySelectorAll('#sv_gallery video').length===3 && !sourceStyleBusy",timeout=60000)
    assert len(images)==3 and model.chat_json.call_count==1
    assert 'Тёплый акцент' in page.locator('#sv_gallery').inner_text()
    assert 'карандаш' not in page.locator('#sv_gallery').inner_text().lower()
    page.locator('#sv_preview').click();page.wait_for_function('!sourceStyleBusy',timeout=60000)
    assert len(images)==3 and model.chat_json.call_count==1
    page.locator('#sv_gallery article').nth(1).get_by_role('button',name='Выбрать и сохранить этот стиль',exact=True).click()
    page.wait_for_function("channelDesignEditing?.palette[0]==='#986643' && !sourceStyleBusy")
    profile=page.evaluate('channelDesignEditing');assert len(profile['assets'])==1
    assert profile['font']=='Intro Demo Black CAPS' and visual['description'] in profile['description']
    assert app.STATE['script']=='Unchanged narration.' and app.STATE['source_profile']['visual_style']==visual
    assert not set(calls)&{'start','rewrite','voice_sample','youtube_sample','drawing_variants'}
    assert not errors,errors
    browser.close()
print('Source-style E2E passed: actual reference grammar retained, three source-derived names/styles, one shared text request, three reference-conditioned frames/videos, completed previews reused, selected own reference saved, script unchanged.')
