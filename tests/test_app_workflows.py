"""Offline application workflows. No service keys or network access required."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app


class AppWorkflows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        state = copy.deepcopy(app.STATE)
        state.update(running=False, preparing=False, queue_active=False,
                     error=None, result=None, cancelled=False, script=None, log=[])
        for p in (patch.object(app, 'CONFIG_PATH', str(self.root / 'config.json')),
                  patch.object(app, 'data_dir', return_value=str(self.root)),
                  patch.object(app, 'STATE', state), patch.object(app, 'QUEUE', []),
                  patch.object(app, 'QUEUE_STOP', False),
                  patch.object(app.endpoints, 'configure'),
                  patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network request'))):
            p.start()
            self.addCleanup(p.stop)
        app.pipeline.CANCEL.clear()
        self.addCleanup(app.pipeline.CANCEL.clear)
        self.api = app.Api()
        app.save_cfg({'fastgen_api_key': 'test-fastgen-secret', 'tts_service': 'edge'})

    def test_config_roundtrip_keeps_unicode_and_masks_secrets(self):
        self.api.save_config({'custom_style': '  Красный   закат ', 'lang': 'en'})
        saved = app.load_cfg()
        self.assertEqual(saved['custom_style'], 'Красный закат')
        self.assertEqual(saved['lang'], 'en')
        public = json.dumps(self.api.get_state(), ensure_ascii=False)
        self.assertNotIn('test-fastgen-secret', public)
        self.assertFalse((self.root / 'config.json.tmp').exists())

    def test_masked_key_row_preserves_existing_key(self):
        self.api.save_config({'keys': {'fastgen': [{'idx': 0, 'value': app.mask('test-fastgen-secret')}]}})
        self.assertEqual(app.load_cfg()['image_keys']['fastgen'], ['test-fastgen-secret'])

    def test_deleting_all_key_rows_does_not_resurrect_legacy_key(self):
        self.api.save_config({'keys': {'fastgen': []}})
        cfg = app.load_cfg()
        self.assertEqual(cfg['image_keys']['fastgen'], [])
        self.assertEqual(cfg['fastgen_api_key'], '')
        self.assertFalse(self.api.get_state()['config']['has_fastgen'])

    def test_reordering_key_rows_updates_primary_key(self):
        self.api.save_config({'keys': {'fastgen': [{'value': 'second-key'}, {'value': 'first-key'}]}})
        self.assertEqual(app.load_cfg()['fastgen_api_key'], 'second-key')

    def test_source_validation_requires_existing_audio_or_nonempty_script(self):
        self.assertIsNotNone(self.api._validate_source({'source': 'mp3', 'audio': '/missing.mp3'}))
        self.assertIsNotNone(self.api._validate_source({'source': 'script', 'script': ' ', 'tts': 'edge'}))
        self.assertIsNone(self.api._validate_source({'source': 'script', 'script': 'Привет!', 'tts': 'edge'}))
        audio = self.root / 'звук с пробелами.wav'
        audio.write_bytes(b'test fixture, validation only')
        self.assertIsNone(self.api._validate_source({'source': 'mp3', 'audio': str(audio)}))

    def test_royal_tts_uses_royaltechno_key(self):
        self.api.save_config({'royal': 'test-royal-secret'})
        self.assertIsNone(self.api._validate_source({'source': 'script', 'script': 'Привет!', 'tts': 'royal'}))

    def test_missing_rewrite_key_rejected_before_thread_starts(self):
        with patch.object(app.threading, 'Thread') as thread:
            r = self.api.rewrite({'rewrite': {'on': True, 'source': 'text', 'text': 'Test', 'engine': 'anthropic'}})
        self.assertFalse(r['ok'])
        thread.assert_not_called()

    def test_queue_persists_and_recovers_interrupted_job(self):
        self.api.queue_add({'source': 'script', 'script': 'Тест очереди', 'tts': 'edge'})
        app.QUEUE[0]['status'] = 'running'
        app.queue_save()
        app.QUEUE = []
        app.queue_load()
        self.assertEqual(len(app.QUEUE), 1)
        self.assertEqual(app.QUEUE[0]['status'], 'queued')
        self.assertEqual(app.QUEUE[0]['script'], 'Тест очереди')

    def test_queue_controls_preserve_running_item(self):
        for text in ['First', 'Second', 'Third']:
            self.api.queue_add({'source': 'script', 'script': text, 'tts': 'edge'})
        first, second, third = [x['id'] for x in app.QUEUE]
        self.api.queue_move(third, -2)
        self.assertEqual(app.QUEUE[0]['id'], third)
        app.QUEUE[0]['status'] = 'running'
        self.api.queue_remove(third)
        self.assertEqual(len(app.QUEUE), 3)
        self.api.queue_clear(False)
        self.assertEqual([x['id'] for x in app.QUEUE], [third])

    def test_queue_retry_clears_previous_error(self):
        self.api.queue_add({'source': 'script', 'script': 'Retry', 'tts': 'edge'})
        app.QUEUE[0].update(status='error', error='temporary issue')
        self.api.queue_retry(app.QUEUE[0]['id'])
        self.assertEqual(app.QUEUE[0]['status'], 'queued')
        self.assertIsNone(app.QUEUE[0]['error'])

    def test_queue_requires_royal_tts_key(self):
        self.api.queue_add({'source': 'script', 'script': 'Test', 'tts': 'edge'})
        with patch.object(app.threading, 'Thread') as thread:
            result = self.api.queue_start({'providers': ['fastgen'], 'tts': 'royal'})
        self.assertFalse(result['ok'])
        self.assertFalse(app.STATE['queue_active'])
        thread.assert_not_called()

    def test_start_sets_state_and_passes_script_to_worker(self):
        opts = {'source': 'script', 'script': 'Test story', 'tts': 'edge', 'providers': ['fastgen'], 'mode': 'images'}
        with patch.object(app.threading, 'Thread') as thread:
            result = self.api.start(opts)
        self.assertTrue(result['ok'])
        self.assertTrue(app.STATE['running'])
        self.assertEqual(thread.call_args.kwargs['target'], app.run_job)
        self.assertEqual(thread.call_args.kwargs['args'][7], 'Test story')
        thread.return_value.start.assert_called_once()
        with patch.object(app.threading, 'Thread') as thread:
            self.assertFalse(self.api.start(opts)['ok'])
        thread.assert_not_called()

    def test_prepare_publishes_estimate_and_releases_busy_state(self):
        app.STATE['preparing'] = True
        with patch.object(app, 'apply_engine'), patch.object(app.pipeline, 'script_job_dir', return_value=(str(self.root), 'test')), patch.object(app.pipeline, 'prepare', return_value={'scenes': 3, 'job_dir': str(self.root)}):
            app.run_prepare(None, ['fastgen'], 'images', False, script='Test')
        self.assertFalse(app.STATE['preparing'])
        self.assertEqual(app.STATE['scenes'], 3)
        self.assertEqual(app.STATE['estimate']['scenes'], 3)

    def test_prepare_cancellation_is_not_reported_as_failure(self):
        app.STATE['preparing'] = True
        with patch.object(app, 'apply_engine'), patch.object(app.pipeline, 'job_dir_for', return_value=(str(self.root), 'test')), patch.object(app.pipeline, 'prepare', side_effect=app.pipeline.Cancelled('stop')):
            app.run_prepare('test.wav', ['fastgen'], 'images', False)
        self.assertFalse(app.STATE['preparing'])
        self.assertTrue(app.STATE['cancelled'])
        self.assertIsNone(app.STATE['error'])

    def test_rewrite_cancellation_is_not_reported_as_failure(self):
        app.STATE['preparing'] = True
        with patch.object(app, 'apply_engine'), patch.object(app.pipeline, 'rewrite_script', side_effect=app.pipeline.Cancelled('stop')):
            app.run_rewrite({'source': 'text', 'text': 'Test'})
        self.assertFalse(app.STATE['preparing'])
        self.assertTrue(app.STATE['cancelled'])
        self.assertIsNone(app.STATE['error'])

    def test_run_job_failure_releases_busy_state_and_sleep_guard(self):
        app.STATE['running'] = True
        with patch.object(app, 'apply_engine'), patch.object(app, 'keep_awake') as awake, patch.object(app.pipeline, 'job_dir_for', return_value=(str(self.root), 'test')), patch.object(app.pipeline, 'make_video', side_effect=RuntimeError('render failed')):
            app.run_job('test.wav', ['fastgen'], 'images', 'ultrareal', False)
        self.assertFalse(app.STATE['running'])
        self.assertIn('render failed', app.STATE['error'])
        self.assertGreater(app.STATE['ended_at'], 0)
        self.assertEqual([c.args for c in awake.call_args_list], [(True,), (False,)])

    def test_cancel_stops_pipeline_and_queue(self):
        self.assertTrue(self.api.cancel())
        self.assertTrue(app.QUEUE_STOP)
        self.assertTrue(app.pipeline.CANCEL.is_set())

    def test_queue_stops_after_cancelled_item(self):
        for text in ['First', 'Second']:
            self.api.queue_add({'source': 'script', 'script': text, 'tts': 'edge'})
        def stop_job(*args):
            app.STATE['cancelled'] = True
        with patch.object(app, 'run_job', side_effect=stop_job) as worker, patch.object(app.pipeline, 'log'):
            app.run_queue({'provider': ['fastgen'], 'mode': 'images', 'style': 'ultrareal', 'use_refs': False, 'subtitles': False, 'motion': False, 'tts': 'edge', 'video_first': 0, 'hd': False})
        worker.assert_called_once()
        self.assertEqual([x['status'] for x in app.QUEUE], ['stopped', 'queued'])
        self.assertFalse(app.STATE['queue_active'])
        self.assertFalse(app.STATE['running'])


if __name__ == '__main__':
    unittest.main()
