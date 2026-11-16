import copy
import json
from unittest.mock import Mock

import pytest

import source_adaptation as adaptation
import studio
import storyboard

SOURCE = ('A metal spoon left in warm water becomes warm because heat travels through the metal. '
          'The handle warms gradually rather than instantly. A wooden handle transfers heat differently. '
          'The lesson explains why material and contact affect what a person feels. '
          'The source concludes that choosing a suitable handle helps with comfortable use.')
QUOTE = 'A metal spoon left in warm water becomes warm'
SCRIPT = ('Представьте чашку тёплой воды и ложку: почему ручка нагревается не сразу? '
          'Тепло передаётся через материал постепенно. Здесь важно различать сам контакт '
          'с водой и то, как тепло доходит до руки. С деревянной ручкой ощущения будут '
          'другими. Поэтому подходящий материал ручки помогает пользоваться предметом комфортнее.')
PROFILE = {'visual_style': {'description': 'source ink cartoon', 'palette': ['red'],
                           'composition': 'source layout', 'shot_types': ['wide']},
           'narrative_style': 'Question followed by an explanation.',
           'montage': {'shots': [{'index': 0, 'duration': 5, 'reference_frame': '/private/source.jpg'}]},
           'sampling': {'frame_count': 8, 'duration': 30}}


def client_fixture():
    calls = []
    def answer(system, prompt, **kwargs):
        calls.append(prompt)
        data = json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]
        if 'source' in data:
            return {'facts': [{'statement': 'Heat travels through metal gradually.', 'source_excerpt': QUOTE}]}
        if 'maximum_sections' in data:
            return {'angle': 'Why a handle changes the experience', 'viewer_promise': 'Explain the delay and material choice',
                    'keep': ['A concrete everyday mechanism'], 'change': ['Start with a question about the handle'],
                    'visual_style': {'description': 'Own flat cut-paper diagrams with teal accents', 'palette': ['#126B70'],
                                     'composition': 'Own split-panel layout', 'shot_types': ['detail', 'diagram']},
                    'sections': [{'purpose': 'explain', 'focus': 'The handle', 'fact_ids': ['f1']}]}
        return {'script': SCRIPT}
    client = Mock(); client.chat_json.side_effect = answer
    return client, calls


def test_independent_script_profile_and_resume_without_source_frames(tmp_path):
    client, calls = client_fixture(); original = copy.deepcopy(PROFILE)
    text, profile = adaptation.create(SOURCE, PROFILE, {}, tmp_path, client, language='русском')
    assert text == SCRIPT and PROFILE == original
    assert profile['content_only'] and profile['montage']['shots'] == []
    assert profile['visual_style']['palette'] == ['#126B70']
    assert profile['adaptation']['keep'] and profile['adaptation']['change']
    assert '/private/source.jpg' not in json.dumps(profile)
    assert len(calls) == 3
    assert 'source_excerpt' not in calls[-1] and QUOTE not in calls[-1]
    assert adaptation.create(SOURCE, PROFILE, {}, tmp_path, client, language='русском') == (text, profile)
    assert len(calls) == 3
    assert studio.with_direction(profile, {'enhancement_level': 'original'})['content_only']
    query = Mock(side_effect=lambda prompt: {'plans': [
        {'index': item['index'], 'source_shot': None, 'confidence': 'high', 'prompt': 'Own teal diagram of heat.',
         'editorial_role': 'explain', 'shot_size': 'diagram'}
        for item in json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0]['passages']]})
    board = storyboard.create(text, profile, query, tmp_path / 'boards')
    assert all(row['source_shot'] is None for row in board['rows'])
    assert all('"source_shots": []' in call.args[0] and '/private/source.jpg' not in call.args[0] for call in query.call_args_list)
    assert not studio.editorial(text, board)['weak_plans']


def test_saved_channel_design_wins_and_source_is_not_modified(tmp_path):
    client, _ = client_fixture(); profile = copy.deepcopy(PROFILE)
    profile['channel_design_ref'] = {'id': 'channel'}
    profile['narration'] = {'tempo': 'balanced'}
    _, result = adaptation.create(SOURCE, profile, {}, tmp_path, client)
    assert result['visual_style'] == PROFILE['visual_style']
    assert result['channel_design_ref'] == profile['channel_design_ref']
    assert result['narration'] == profile['narration']


@pytest.mark.parametrize('fault', ['quote', 'fact_id', 'copied_style', 'empty', 'verbatim'])
def test_bad_evidence_or_copied_output_stops_before_media(tmp_path, fault):
    client, calls = client_fixture(); base = client.chat_json.side_effect
    def bad(system, prompt, **kwargs):
        value = base(system, prompt, **kwargs)
        if fault == 'quote' and 'facts' in value: value['facts'][0]['source_excerpt'] = 'Invented evidence that does not occur'
        if fault == 'fact_id' and 'sections' in value: value['sections'][0]['fact_ids'] = ['missing']
        if fault == 'copied_style' and 'sections' in value: value['visual_style'] = copy.deepcopy(PROFILE['visual_style'])
        if fault == 'empty' and 'script' in value: value['script'] = ''
        if fault == 'verbatim' and 'script' in value: value['script'] = SOURCE
        return value
    client.chat_json.side_effect = bad
    with pytest.raises(ValueError, match='непроверяемый'):
        adaptation.create(SOURCE, PROFILE, {}, tmp_path, client)
    assert len(calls) <= 3
    client.chat_json.side_effect = base
    assert adaptation.create(SOURCE, PROFILE, {}, tmp_path, client)[0] == SCRIPT
    # Rejected stages are invalidated, accepted extraction and blueprint are reused.
    assert len(calls) <= 6


def test_original_does_not_silently_reuse_source_shots():
    with pytest.raises(ValueError, match='сначала создайте самостоятельный сценарий'):
        studio.with_direction(PROFILE, {'enhancement_level': 'original'})
    assert 'Preserve the source palette' not in studio.brief({'enhancement_level': 'original'})
    assert 'REFERENCE-LED ORIGINAL' in studio.brief({'enhancement_level': 'original'})


def test_verbatim_diagnostic_is_not_a_plagiarism_score():
    report = adaptation.overlap_report(SOURCE, SOURCE)
    assert report['substantial_verbatim_overlap'] and report['long_shared_phrases']
    assert 'score' not in report and 'YouTube' in report['limitations']
    assert not adaptation.overlap_report(SOURCE, SCRIPT)['substantial_verbatim_overlap']


def test_original_short_trial_stays_bounded(tmp_path):
    client, _ = client_fixture(); profile = copy.deepcopy(PROFILE)
    profile['sample'] = {'source_seconds': 2, 'start': 0}
    with pytest.raises(ValueError, match='непроверяемый'):
        adaptation.create(SOURCE, profile, {}, tmp_path, client)


def test_rewrite_original_uses_overview_and_frozen_options(tmp_path, monkeypatch):
    import pipeline
    import rewrite
    import source_analysis
    import source_narration
    import youtube_source
    pipeline.CANCEL.clear()
    monkeypatch.setattr(pipeline, 'data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(pipeline.channel_designs, 'selected', lambda *a: None)
    monkeypatch.setattr(rewrite, 'youtube_transcript', lambda *a, **k: SOURCE)
    monkeypatch.setattr(youtube_source, 'video', lambda *a, **k: 'source.mp4')
    analysis = []
    def analyse(video, text, cfg, *args):
        analysis.append(cfg); return copy.deepcopy(PROFILE)
    monkeypatch.setattr(source_analysis, 'analyse', analyse)
    monkeypatch.setattr(source_narration, 'attach_youtube', lambda p, *a: p)
    client, calls = client_fixture()
    monkeypatch.setattr(studio, 'text_client', lambda *a: client)
    profiles = []
    text = pipeline.rewrite_script({'anthropic_api_key': 'fixture', 'enhancement_level': 'faithful'},
        {'source': 'youtube', 'url': 'https://www.youtube.com/watch?v=Cs9lG-Meo1s', 'engine': 'anthropic',
         'analyze_video': True, 'montage': True, 'language': 'русском', '_creative_options': {'enhancement_level': 'original'}},
        lambda *a: None, profiles.append)
    assert text == SCRIPT and analysis[0]['source_montage'] is False
    assert profiles[-1]['content_only'] and profiles[-1]['adaptation']
    assert profiles[-1]['montage']['shots'] == []


def test_reference_visual_default_ignores_legacy_photoreal_preset(tmp_path):
    client, calls=client_fixture()
    adaptation.create(SOURCE,PROFILE,{'style':'ultrareal'},tmp_path,client)
    first=json.JSONDecoder().raw_decode(calls[1].split('Reference JSON:\n')[1])[0]
    assert first['own_style_request']==''
    adaptation.create(SOURCE,PROFILE,{'style':'ultrareal','original_visual':'custom'},tmp_path,client)
    outlines=[json.JSONDecoder().raw_decode(prompt.split('Reference JSON:\n')[1])[0] for prompt in calls if 'maximum_sections' in prompt]
    assert 'photograph' in outlines[-1]['own_style_request']
    assert len([prompt for prompt in calls if 'Extract the important claims' in prompt])==1


def test_questionable_claim_notes_are_kept_out_of_spoken_script(tmp_path):
    client,calls=client_fixture();base=client.chat_json.side_effect
    def answer(system,prompt,**kwargs):
        result=base(system,prompt,**kwargs)
        if 'facts' in result:result['facts'][0]['review_note']='Check the material-specific conditions before publication.'
        return result
    client.chat_json.side_effect=answer
    text,profile=adaptation.create(SOURCE,PROFILE,{},tmp_path,client)
    assert profile['adaptation']['facts_to_verify'][0]['note'].startswith('Check the material')
    assert 'Check the material' not in text
