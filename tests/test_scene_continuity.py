import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

import app
import netutil
import pipeline
import scene_continuity as continuity
import storyboard_cache as cache


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    pipeline.CANCEL.clear()
    monkeypatch.setattr(netutil, 'CANCEL_CHECK', None)
    monkeypatch.setattr(pipeline, 'LOG_PATH', str(tmp_path / 'log.txt'))
    monkeypatch.setattr(pipeline, 'warn_hours', lambda *a: None)
    monkeypatch.setattr(pipeline, 'QA_STATS', dict.fromkeys(pipeline.QA_STATS, 0))
    yield
    pipeline.CANCEL.clear()


def scenes():
    return [{'index': i, 'text': 'story', 'source_visual': room, 'raw_prompt': room,
             'prompt': room, 'duration': 3, 'plan_id': f'plan-{i}', 'plan_take': 0}
            for i, room in enumerate(['kitchen wide', 'hallway wide', 'kitchen close', 'stairwell', 'hallway return'])]


def answer():
    return {'locations': [{'id': 'kitchen', 'name': 'Kitchen', 'description': 'One kitchen with an oak table and left window.'},
                          {'id': 'hall', 'name': 'Private hallway', 'description': 'One hallway with a white closet and brown entrance door.'}],
            'assignments': [{'index': i, 'location_id': key} for i, key in enumerate(['kitchen', 'hall', 'kitchen', None, 'hall'])]}


def test_locations_are_shared_across_returns_and_cached(tmp_path):
    original = scenes(); llm = SimpleNamespace(chat_json=Mock(return_value=answer()))
    prepared = continuity.prepare(original, llm, tmp_path, Mock())
    assert llm.chat_json.call_count == 1
    assert [s.get('location_anchor_index') for s in prepared] == [0, 1, 0, None, 1]
    assert 'location_id' not in prepared[3]
    assert prepared[0]['location_reference_signature'] == prepared[2]['location_reference_signature']
    assert continuity.prepare(original, llm, tmp_path, Mock()) == prepared
    assert llm.chat_json.call_count == 1
    assert original == scenes(), 'Classification must not mutate source scene descriptions'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unknown', 'path'])
def test_incomplete_or_hallucinated_assignments_do_not_start_images(tmp_path, change):
    value = answer()
    if change == 'missing': value['assignments'].pop()
    if change == 'duplicate': value['assignments'].append(dict(value['assignments'][0]))
    if change == 'unknown': value['assignments'][0]['location_id'] = 'invented'
    if change == 'path': value['locations'][0]['id'] = '../../bad'
    with pytest.raises(continuity.ContinuityError):
        continuity.prepare(scenes(), SimpleNamespace(chat_json=lambda *a, **k: value), tmp_path, Mock())


def test_anchor_edit_invalidates_dependents_but_not_other_room(tmp_path):
    a = continuity.prepare(scenes(), SimpleNamespace(chat_json=lambda *a, **k: answer()), tmp_path, Mock())
    changed = scenes(); changed[0]['plan_take'] = 1
    b = continuity.prepare(changed, SimpleNamespace(chat_json=lambda *a, **k: answer()), tmp_path, Mock())
    assert cache.image_key(a[2]) != cache.image_key(b[2])
    assert cache.image_key(a[4]) == cache.image_key(b[4])


def context(draw, qa=None):
    return {'providers': ['fastgen'], 'bases': ['fastgen'], 'fallback': [], 'api': None,
            'image_gen': {'fastgen': draw}, 'gates': {'fastgen': SimpleNamespace(threads=4)}, 'qa': qa}


def test_same_location_waits_for_final_qa_anchor(tmp_path, monkeypatch):
    selected = continuity.prepare(scenes(), SimpleNamespace(chat_json=lambda *a, **k: answer()), tmp_path, Mock())
    checks = {}
    def qa(client, scene, path):
        index = scene['index']; checks[index] = checks.get(index, 0) + 1
        return (checks[index] > 1 if index == 0 else True), 8, 'fixture'
    monkeypatch.setattr(pipeline, 'qa_check', qa)
    observed = {}
    def draw(scene, path, progress, refs):
        index = scene['index']; observed.setdefault(index, []).append(list(refs))
        if index == 2:
            assert checks[0] == 2
            assert refs[0].endswith('0000.png')
            with Image.open(refs[0]) as reference:
                assert reference.getpixel((0, 0)) == (0, 0, 255)
            assert 'APPROVED LOCATION ANCHOR' in scene['prompt']
        if index == 4: assert refs[0].endswith('0001.png')
        color = 'red' if index == 0 and len(observed[index]) == 1 else 'blue'
        Image.new('RGB', (32, 18), color).save(path)
        return 'fixture'
    pipeline.generate_all_images(context(draw, object()), selected, {}, str(tmp_path), Mock(), {})
    assert observed[0][0] == [] and observed[1][0] == [] and observed[3][0] == []
    assert len(observed[0]) == 2 and len(observed[2]) == 1


def test_failed_anchor_blocks_dependent_requests(tmp_path):
    selected = continuity.prepare(scenes()[:3], SimpleNamespace(chat_json=lambda *a, **k: {
        'locations': answer()['locations'], 'assignments': answer()['assignments'][:3]}), tmp_path, Mock())
    calls = []
    def draw(scene, path, *args):
        calls.append(scene['index'])
        if scene['index'] == 0: return None
        Image.new('RGB', (32, 18), 'red').save(path)
        return 'fixture'
    with pytest.raises(RuntimeError, match='Не получен кадр локации'):
        pipeline.generate_all_images(context(draw), selected, {}, str(tmp_path), Mock(), {})
    assert 2 not in calls


def test_location_qa_uses_two_panels_and_removes_temporary_file(tmp_path):
    anchor = tmp_path / 'anchor.png'; target = tmp_path / 'target.png'
    Image.new('RGB', (160, 90), 'red').save(anchor)
    Image.new('RGB', (160, 90), 'blue').save(target)
    compared = []
    def see(system, user, path):
        compared.append(path)
        with Image.open(path) as image:
            assert image.size == (1920, 540)
            assert image.getpixel((400, 270)) == (255, 0, 0)
            assert image.getpixel((1400, 270)) == (0, 0, 255)
        assert 'Judge ONLY the RIGHT' in system and 'window' in user
        return {'ok': False, 'score': 2, 'issues': 'Room layout differs'}
    assert pipeline.qa_check(SimpleNamespace(see_json=see), {'location_reference_path': str(anchor),
            'location_description': 'left window'}, str(target)) == (False, 2., 'Room layout differs')
    assert not Path(compared[0]).exists()


def test_mode_has_separate_visual_cache_and_saves_setting(tmp_path, monkeypatch):
    legacy = pipeline.visual_job_dir(str(tmp_path), 'source', 'speed', False, False)[0]
    enabled = pipeline.visual_job_dir(str(tmp_path), 'source', 'speed', False, False, location_continuity=True)[0]
    assert legacy != enabled
    assert pipeline.visual_job_dir(str(tmp_path), 'source', 'speed', False, False, location_continuity=False)[0] == legacy
    cfg = {'image_providers': ['fastgen'], 'tts_service': 'elevenlabs'}
    monkeypatch.setattr(app, 'load_cfg', lambda: dict(cfg))
    monkeypatch.setattr(app, 'save_cfg', lambda value: cfg.update(value))
    app.Api().save_config({'location_continuity': True})
    assert cfg['location_continuity'] is True and cfg['tts_service'] == 'elevenlabs'


def test_registry_persists_across_batches_and_recovers_completed_batches(tmp_path):
    first = {'locations': [{'id': 'k', 'name': 'Kitchen', 'description': 'The same white cabinets.'}],
             'assignments': [{'index': 0, 'location_id': 'k'}, {'index': 1, 'location_id': None}]}
    second = {'locations': [], 'assignments': [{'index': 2, 'location_id': 'k'}, {'index': 3, 'location_id': None}]}
    source = scenes()[:4]
    fn = Mock(side_effect=[first, RuntimeError('offline')])
    with pytest.raises(RuntimeError, match='offline'):
        continuity.prepare(source, SimpleNamespace(chat_json=fn), tmp_path, Mock(), batch_size=2)
    def resume(system, prompt, **kwargs):
        data = json.loads(prompt.split('Reference JSON:\n')[1])
        assert data['registry']['k']['description'] == 'The same white cabinets.'
        assert [s['index'] for s in data['shots']] == [2, 3]
        return second
    fn = Mock(side_effect=resume)
    prepared = continuity.prepare(source, SimpleNamespace(chat_json=fn), tmp_path, Mock(), batch_size=2)
    assert fn.call_count == 1
    assert prepared[0]['location_id'] == prepared[2]['location_id'] == 'k'


def test_qa_keeps_approved_redraw_over_higher_scored_rejected_frame(tmp_path, monkeypatch):
    answers = iter([(False, 9, 'wrong room'), (True, 8, '')])
    monkeypatch.setattr(pipeline, 'qa_check', lambda *a: next(answers))
    calls = []
    def draw(scene, path, *args):
        calls.append(path)
        Image.new('RGB', (32, 18), 'red' if len(calls) == 1 else 'blue').save(path)
        return 'fixture'
    pipeline.generate_all_images(context(draw, object()), [{'index': 0, 'prompt': 'Kitchen'}], {}, str(tmp_path), Mock(), {})
    with Image.open(tmp_path / 'images/0000.png') as image:
        assert image.getpixel((0, 0)) == (0, 0, 255)


def test_redrawing_anchor_invalidates_only_its_room(tmp_path):
    prepared = continuity.prepare(scenes(), SimpleNamespace(chat_json=lambda *a, **k: answer()), tmp_path, Mock())
    assert continuity.affected(prepared, {0}) == {0, 2}
    assert continuity.affected(prepared, {1}) == {1, 4}
    assert continuity.affected(prepared, {2}) == {2}
    assert continuity.affected(prepared, set()) == set()
