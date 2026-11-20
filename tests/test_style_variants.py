import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
import pytest
import channel_design
import style_variants as styles

@pytest.fixture(autouse=True)
def restore_native_state():
    import app
    previous=copy.deepcopy(app.STATE)
    yield
    app.STATE.clear();app.STATE.update(previous)

VISUAL={'description':'Soft gouache cut-out illustrations with thick irregular contours and layered painted backgrounds.',
        'palette':['#205070','#e4bb72'],'character_design':'Faceless human actors, mascot eyes only.'}

def response():
    return {'variants':[{'name':name,'description':'Same gouache rendering; '+name,
        'palette':palette,'font':'Arial' if i!=1 else 'Intro Demo Black CAPS',
        'changes':{'palette':name+' restrained colours','spacing':name+' breathing space','typography':'clear own labels'}}
        for i,(name,palette) in enumerate([('Холодный контраст',['#205f88','#c6dce2']),('Тёплый свет',['#845434','#e4bb72']),('Сдержанная глубина',['#344a55','#adc1c0'])])]}

def prepared(tmp_path):
    ref=tmp_path/'reference.png';Image.new('RGB',(160,90),'#205070').save(ref)
    client=Mock();client.chat_json.return_value=response()
    result=styles.prepare(tmp_path,VISUAL,[str(ref)],'Keep the gouache character; own colour and type.',client)
    return result,ref,client

def render_fixture(calls,fail=None):
    def generate(scene,out,refs):
        n=scene['index'];calls.append((n,copy.deepcopy(scene),list(refs)))
        if fail and fail(n):raise RuntimeError('Provider failure')
        Image.new('RGB',(320,180),['#205f88','#845434','#344a55'][n]).save(out)
        return 'fixture-image'
    return generate


def test_three_variations_come_from_actual_reference_and_share_its_medium(tmp_path):
    result,ref,client=prepared(tmp_path)
    assert len(result['variants'])==3
    for item in result['variants']:
        assert VISUAL['description'] in item['profile']['description']
        assert 'Faceless human actors' in item['profile']['rules']
        assert item['profile']['faces']=='blank'
        assert not item['profile'].get('drawing')
    assert {v['name'] for v in result['variants']}=={'Холодный контраст','Тёплый свет','Сдержанная глубина'}
    assert 'reference_grammar' in client.chat_json.call_args.args[1]
    assert styles.prepare(tmp_path,VISUAL,[str(ref)],'Keep the gouache character; own colour and type.',client)==result
    assert client.chat_json.call_count==1
    assert str(ref) not in json.dumps(styles.public(result))


@pytest.mark.parametrize('fault',['count','duplicate','palette','font','medium_axis'])
def test_invalid_or_fixed_medium_switch_output_is_not_accepted(tmp_path,fault):
    value=response()
    if fault=='count':value['variants'].pop()
    if fault=='duplicate':value['variants'][1]=copy.deepcopy(value['variants'][0])
    if fault=='palette':value['variants'][0]['palette']=['red','blue']
    if fault=='font':value['variants'][0]['font']='Unavailable Font'
    if fault=='medium_axis':value['variants'][0]['changes']['drawing_medium']='switch to pencil'
    client=Mock();client.chat_json.return_value=value
    with pytest.raises(ValueError,match='три варианта стиля'):styles.prepare(tmp_path,VISUAL,[],'',client)
    assert list(tmp_path.rglob('directions.received.json'))


def test_changed_reference_or_requested_changes_create_new_comparison(tmp_path):
    result,ref,client=prepared(tmp_path)
    other=styles.prepare(tmp_path,VISUAL,[str(ref)],'Different typography',client)
    assert other['id']!=result['id'] and client.chat_json.call_count==2
    Image.new('RGB',(160,90),'red').save(ref)
    newer=styles.prepare(tmp_path,VISUAL,[str(ref)],'Different typography',client)
    assert newer['id']!=other['id'] and client.chat_json.call_count==3


def test_same_scene_all_variants_use_source_frames_and_resume_only_missing(tmp_path):
    result,ref,client=prepared(tmp_path);calls=[];failures=[True]
    def fail(n):
        if n==1 and failures:failures.pop();return True
        return False
    renderer=render_fixture(calls,fail)
    with pytest.raises(RuntimeError):styles.previews(tmp_path,result['id'],'An original whale scene','',['elysium','m1'],renderer)
    choices=styles.previews(tmp_path,result['id'],'An original whale scene','',['elysium','m1'],renderer)
    assert [row[0] for row in calls]==[0,1,1,2]
    assert len(choices)==3 and all(c['video'].startswith('data:video/mp4;base64,') for c in choices)
    for _,scene,refs in calls:
        data=json.loads(scene['prompt'].split('Reference JSON:\n')[1])
        assert data['same_scene']=='An original whale scene' and data['reference_grammar']==VISUAL
        assert refs==[str(ref.resolve())]
    assert styles.previews(tmp_path,result['id'],'An original whale scene','',['elysium','m1'],renderer)==choices
    assert len(calls)==4 and client.chat_json.call_count==1


def test_selected_style_saves_own_reference_and_keeps_immutable_previous_versions(tmp_path):
    result,ref,client=prepared(tmp_path);calls=[]
    styles.previews(tmp_path,result['id'],'A whale','One shared caption',['elysium','m1'],render_fixture(calls))
    store=channel_design.DesignStore(tmp_path)
    old=store.save({'name':'Our channel','description':'Old channel drawing','drawing':{'style':'lineart'}})
    old=store.import_asset(old['id'],1,ref)
    chosen=styles.save_choice(tmp_path,result['id'],'v2',old,old['id'],old['revision'])
    assert chosen['font']=='Intro Demo Black CAPS' and chosen['palette']==response()['variants'][1]['palette']
    assert chosen['drawing']['enabled'] is False
    assert len(chosen['assets'])==1 and chosen['assets'][0]['sha256']!=hashlib.sha256(ref.read_bytes()).hexdigest()
    assert store.get(old['id'],old['revision'])['assets']==old['assets']
    assert store.asset_path(chosen['assets'][0]).is_file()
    Image.new('RGB',(160,90),'blue').save(ref)
    # Own approved reference does not depend on the source file after selection.
    assert channel_design.selected(tmp_path,{'channel_design_id':chosen['id']})['assets']==chosen['assets']


def test_tampered_source_is_rejected_before_image_request(tmp_path):
    result,ref,_=prepared(tmp_path);ref.write_bytes(b'changed');renderer=Mock()
    with pytest.raises(ValueError,match='Кадры образца изменились'):styles.previews(tmp_path,result['id'],'A whale','',[],renderer)
    renderer.assert_not_called()


def test_native_prepare_preserves_script_and_board_without_reanalysis(tmp_path,monkeypatch):
    import app
    import studio
    import source_analysis
    source={'visual_style':VISUAL,'montage':{'shots':[]}}
    app.STATE.update(running=False,preparing=False,queue_active=False,service_switch=False,source_profile=source,script='Preserve script',storyboard_id='preserve-board')
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path));monkeypatch.setattr(app,'load_cfg',lambda:{})
    client=Mock();client.chat_json.return_value=response();monkeypatch.setattr(studio,'text_client',lambda cfg:client)
    analyse=Mock(side_effect=AssertionError('No repeated source analysis'));monkeypatch.setattr(source_analysis,'analyse_design',analyse)
    r=app.Api().style_variants_prepare({'name':'Own channel','description':'draft'},'analysis')
    assert r['ok'] and len(r['comparison']['variants'])==3
    assert app.STATE['script']=='Preserve script' and app.STATE['storyboard_id']=='preserve-board' and not app.STATE['preparing']
    analyse.assert_not_called()


def test_comparison_paths_are_not_client_controlled(tmp_path):
    for value in ('../secret','/tmp/file','v1',None):
        with pytest.raises(ValueError):styles.folder(tmp_path,value)


def test_provider_jpeg_is_normalised_before_using_it_as_channel_reference(tmp_path):
    result,_,_=prepared(tmp_path)
    def generate(scene,out,refs):
        Image.new('RGB',(320,180),'#456789').save(out,format='JPEG')
        return 'jpeg-fixture'
    styles.previews(tmp_path,result['id'],'An original scene','',['fixture'],generate)
    chosen=styles.save_choice(tmp_path,result['id'],'v1',{'name':'Own channel','description':'draft'})
    path=channel_design.DesignStore(tmp_path).asset_path(chosen['assets'][0])
    with Image.open(path) as image:assert image.format=='PNG'


def test_own_adapted_storyboard_is_not_mislabelled_as_original_source_style(tmp_path,monkeypatch):
    import app
    import studio
    app.STATE.update(running=False,preparing=False,queue_active=False,service_switch=False,
        source_profile={'visual_style':VISUAL,'content_only':True,'adaptation':{'angle':'New own angle'}})
    monkeypatch.setattr(app,'data_dir',lambda:str(tmp_path));monkeypatch.setattr(app,'load_cfg',lambda:{})
    model=Mock(side_effect=AssertionError('No model call for the wrong source'));monkeypatch.setattr(studio,'text_client',model)
    result=app.Api().style_variants_prepare({'name':'Our channel','description':'draft'},'analysis')
    assert not result['ok'] and 'уже своё оформление' in result['error']
    model.assert_not_called()


def test_freeform_variant_description_cannot_override_reference_medium(tmp_path):
    client=Mock();value=response();value['variants'][0]['description']='Replace the source with a pencil rendering.'
    client.chat_json.return_value=value
    result=styles.prepare(tmp_path,VISUAL,[],'',client)
    assert all(VISUAL['description'] in row['profile']['description'] for row in result['variants'])
    assert 'pencil rendering' not in result['variants'][0]['profile']['description']
