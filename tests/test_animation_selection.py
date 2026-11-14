from unittest.mock import Mock
import copy

import pytest
import animation_selection as a
import pipeline
import storyboard_cache
import native_projects


def scenes():
    return [dict(index=i,id=str(i),plan_id=str(i),prompt='A calm view of a road',editorial_role=role,shot_size='wide')
            for i,role in enumerate(['explain','example','hook','explain','reveal','contrast','resolve'])]


def test_budget_and_risk_exclusion_and_deterministic_reasoning():
    rows=scenes();rows[2]['shot_size']='diagram';rows[4]['prompt']='Two cars collide in a crash'
    selected=a.select(rows,{'strategy':'key','limit':3})
    assert selected==a.select(rows,{'strategy':'key','limit':3})
    assert selected['count']==3
    assert not selected['rows'][2]['selected'] and not selected['rows'][4]['selected']
    assert all(r['reason'] for r in selected['rows'])
    assert a.select(rows,{'strategy':'hook','limit':1})['rows'][0]['selected']
    assert a.select(rows,{'strategy':'key','limit':0})['count']==0


def test_explicit_selection_overrides_local_channel_without_changing_prompts_or_receipts():
    rows=scenes()
    for row in rows:row['channel_design']={'motion_policy':'local'}
    assert pipeline.animated_scenes(rows,'video')==[]
    marked=a.apply(rows,{'strategy':'key','limit':2},'images')
    assert len(pipeline.animated_scenes(marked,'images'))==2
    assert [r['prompt'] for r in rows]==[r['prompt'] for r in marked]
    assert [storyboard_cache.image_key(r) for r in rows]==[storyboard_cache.image_key(r) for r in marked]
    assert pipeline.animated_scenes(a.apply(rows,{'strategy':'local','limit':10}),'video')==[]


def test_manual_override_risk_and_multiple_fragments_hard_budget():
    rows=scenes()[:2]
    for r in rows:r.update(animate=True,shot_size='diagram',plan_id='same')
    with pytest.raises(ValueError,match='лимите'):a.apply(rows,{'strategy':'manual','limit':1})
    assert len(pipeline.animated_scenes(a.apply(rows,{'strategy':'manual','limit':2}),'images'))==2
    for r in rows:r['shot_size']='wide'
    assert a.select(rows,{'strategy':'key','limit':2})['count']==1


def test_legacy_behavior_and_project_settings_round_trip(tmp_path):
    rows=scenes()
    for mode in ('images','mixed','video'):
        before=copy.deepcopy(rows)
        after=a.apply(rows,{'strategy':'legacy','limit':0},mode,2)
        assert before==after
        assert [r['index'] for r in pipeline.animated_scenes(after,mode,2)]==[r['index'] for r in rows if mode=='video' or r['index']<2 or mode=='mixed' and r['index']%2==0]
    opts={'animation':{'strategy':'hook','limit':3}}
    assert native_projects.safe_options(opts)==opts
    for invalid in ({'strategy':'other','limit':3},{'strategy':'manual','limit':True},{'limit':151}):
        with pytest.raises(ValueError):a.settings(invalid)


def test_preview_uses_no_models_and_manual_without_board_blocks(monkeypatch):
    import app
    monkeypatch.setattr(app,'load_cfg',lambda:{})
    result=app.Api().animation_preview(scenes(),{'animation':{'strategy':'key','limit':2}})
    assert result['ok'] and result['count']==2
    with pytest.raises(ValueError,match='раскадровке'):
        app.estimate_plan({'source':'script','script':'Words. '*100,'animation':{'strategy':'manual','limit':2}},limits=False)


def test_selection_only_edit_reuses_approved_image_without_paid_recheck(tmp_path):
    import json
    from PIL import Image
    row={**scenes()[0],'channel_design_signature':'fixture-design','duration':5,'prompt':'Stable drawing'}
    original=tmp_path/'first';(original/'images').mkdir(parents=True)
    Image.new('RGB',(64,36),'white').save(original/'images'/'0000.png')
    (original/'images.json').write_text(json.dumps({'0':{'ok':True,'design_approved':True}}))
    cache=tmp_path/'cache';storyboard_cache.publish([row],original,cache)
    selected=a.apply([row],{'strategy':'key','limit':1})
    destination=tmp_path/'second';storyboard_cache.restore(selected,destination,cache)
    assert (destination/'images'/'0000.png').read_bytes()==(original/'images'/'0000.png').read_bytes()
    assert json.loads((destination/'images.json').read_text())['0']['design_approved'] is True
    (destination/'images'/'0000.png').unlink()
    Image.new('RGB',(64,36),'red').save(destination/'images'/'0000.png')
    (destination/'images.json').unlink()
    storyboard_cache.restore(selected,destination,cache)
    assert not (destination/'images.json').exists()
