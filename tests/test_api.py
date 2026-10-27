"""Offline API regressions; all HTTP interactions are mocked, no credits spent."""
import json
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import endpoints
import google_api
import llm_api
import netutil
import nonstop_api
import openai_api
import rewrite
import royaltechno_api
import secretslider_api
import yougen_api


def response(code=200, body=None, headers=None):
    r = requests.Response()
    r.status_code = code
    r._content = json.dumps(body or {}).encode()
    r._content_consumed = True
    r.headers.update(headers or {})
    return r


class Cancelled(Exception):
    pass


class APIRegressionTests(unittest.TestCase):
    def setUp(self):
        self.cancel = netutil.CANCEL_CHECK
        netutil.CANCEL_CHECK = None
        self.endpoints = endpoints.CUSTOM.copy()

    def tearDown(self):
        netutil.CANCEL_CHECK = self.cancel
        endpoints.CUSTOM.clear()
        endpoints.CUSTOM.update(self.endpoints)

    def test_network_retry_recovers_after_timeout_and_overload(self):
        ok = response(body={"status": "completed"})
        fn = Mock(side_effect=[requests.Timeout(), response(503), ok])
        with patch('netutil.pause') as pause:
            self.assertIs(netutil.patient(fn), ok)
        self.assertEqual(fn.call_count, 3)
        self.assertEqual(pause.call_count, 2)

    def test_network_auth_failure_is_not_retried(self):
        denied = response(401)
        fn = Mock(side_effect=requests.HTTPError(response=denied))
        with patch('netutil.pause') as pause, self.assertRaises(requests.HTTPError):
            netutil.patient(fn)
        fn.assert_called_once()
        pause.assert_not_called()

    def test_retry_count_must_be_positive(self):
        with self.assertRaises(ValueError):
            netutil.patient(Mock(), tries=0)

    def test_pause_cancels_without_sleeping(self):
        netutil.CANCEL_CHECK = Mock(side_effect=Cancelled())
        with patch('netutil.time.sleep') as sleep, self.assertRaises(Cancelled):
            netutil.pause(180)
        sleep.assert_not_called()

    def test_provider_polling_retries_raw_http_errors(self):
        for cls, terminal in [(nonstop_api.NonStop, 'completed'), (yougen_api.YouGen, 'succeeded')]:
            with self.subTest(provider=cls.__name__):
                client = cls('test-key')
                client.s.get = Mock(side_effect=[response(503), response(429), response(body={'status': terminal})])
                with patch('netutil.pause'):
                    self.assertEqual(client.wait('id')['status'], terminal)
                self.assertEqual(client.s.get.call_count, 3)

    def test_provider_canceled_status_is_terminal(self):
        for cls in (nonstop_api.NonStop, yougen_api.YouGen):
            for terminal in ('canceled', 'cancelled'):
                with self.subTest(provider=cls.__name__, terminal=terminal):
                    client = cls('test-key')
                    client.s.get = Mock(return_value=response(body={'status': terminal}))
                    with patch('netutil.pause') as pause:
                        self.assertEqual(client.wait('id')['status'], terminal)
                    pause.assert_not_called()

    def test_nonstop_completed_without_images_reports_error(self):
        client = nonstop_api.NonStop('test-key')
        with patch.object(client, 'create_image', return_value='id'), patch.object(client, 'wait', return_value={'status': 'completed'}):
            with self.assertRaisesRegex(nonstop_api.NonStopError, 'картинок нет'):
                client.generate_image('prompt', 'unused.png')

    def test_failed_stream_does_not_destroy_existing_cached_file(self):
        def interrupted():
            yield b'partial'
            raise requests.ConnectionError('broken stream')
        r = Mock()
        r.iter_content.return_value = interrupted()
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / 'result.mp4'
            target.write_bytes(b'previous-good-file')
            with self.assertRaises(requests.ConnectionError):
                netutil.save_response(r, str(target))
            self.assertEqual(target.read_bytes(), b'previous-good-file')
            self.assertEqual([p.name for p in pathlib.Path(tmp).iterdir()], ['result.mp4'])
        r.close.assert_called_once()

    def test_successful_stream_publishes_complete_file(self):
        r = Mock()
        r.iter_content.return_value = [b'video-', b'', b'complete']
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / 'result.mp4'
            self.assertEqual(netutil.save_response(r, str(target)), str(target))
            self.assertEqual(target.read_bytes(), b'video-complete')
        r.close.assert_called_once()

    def test_empty_download_is_not_cached(self):
        r = Mock()
        r.iter_content.return_value = []
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / 'result.mp4'
            with self.assertRaises(requests.ConnectionError):
                netutil.save_response(r, str(target))
            self.assertFalse(target.exists())
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_cancelled_download_is_not_cached(self):
        r = Mock()
        r.iter_content.return_value = [b'one', b'two']
        netutil.CANCEL_CHECK = Mock(side_effect=[None, None, Cancelled()])
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / 'result.mp4'
            with self.assertRaises(Cancelled):
                netutil.save_response(r, str(target))
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])

    def test_yougen_download_fallback_does_not_forward_api_key(self):
        client = yougen_api.YouGen('test-key')
        denied = response(403)
        downloaded = Mock()
        downloaded.iter_content.return_value = [b'image']
        with tempfile.TemporaryDirectory() as tmp, patch.object(client.s, 'get', return_value=denied) as private_get, patch('yougen_api.requests.get', return_value=downloaded) as public_get:
            path = pathlib.Path(tmp) / 'image.png'
            client.download('id', str(path), {'download_url': 'https://cdn.example/result'})
            self.assertEqual(path.read_bytes(), b'image')
        private_get.assert_called_once()
        public_get.assert_called_once_with('https://cdn.example/result', timeout=600, stream=True)

    def test_openai_vision_reasoning_model_uses_supported_token_parameter(self):
        client = openai_api.OpenAIClient('test-key')
        with patch('llm_api.image_b64', return_value='image'), patch.object(client.s, 'post', return_value=response(body={'choices': [{'message': {'content': '{"ok": true}'}}]})) as post:
            self.assertEqual(client.see_json('system', 'user', 'unused', model='gpt-5', max_tokens=600), {'ok': True})
        body = post.call_args.kwargs['json']
        self.assertEqual(body['max_completion_tokens'], 600)
        self.assertNotIn('max_tokens', body)
        self.assertNotIn('temperature', body)

    def test_whisper_keeps_fractional_tail_after_ten_minutes(self):
        client = openai_api.OpenAIClient('test-key')
        chunk = {'text': 'hello.', 'words': [{'word': 'hello', 'start': 0, 'end': 0.5}]}
        with patch('openai_api.subprocess.run') as ffmpeg, patch.object(client, '_transcribe_file', return_value=chunk) as transcribe:
            result = client.transcribe('unused.mp3', 'ffmpeg', 'ffprobe', 600.5)
        self.assertEqual(transcribe.call_count, 2)
        self.assertEqual(ffmpeg.call_count, 2)
        self.assertEqual(result['words'][-1]['end'], 600.5)

    def test_whisper_exact_chunk_boundary_has_no_empty_extra_chunk(self):
        client = openai_api.OpenAIClient('test-key')
        with tempfile.NamedTemporaryFile() as f, patch.object(client, '_transcribe_file', return_value={'text': '', 'words': []}) as transcribe:
            client.transcribe(f.name, 'ffmpeg', 'ffprobe', 600)
        transcribe.assert_called_once()

    def test_openai_json_retry_increases_truncated_token_budget(self):
        client = openai_api.OpenAIClient('test-key')
        results = [response(body={'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}),
                   response(body={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"scenes": []}'}}]})]
        with patch.object(client.s, 'post', side_effect=results) as post:
            self.assertEqual(client.chat_json('s', 'u', max_tokens=100), {'scenes': []})
        self.assertEqual(post.call_args_list[1].kwargs['json']['max_tokens'], 200)

    def test_google_retry_retains_server_error_status(self):
        client = google_api.GoogleAI('test-key')
        with patch.object(client.s, 'post', return_value=response(503, {'error': {'message': 'unavailable'}})), patch('netutil.pause') as pause:
            with self.assertRaises(google_api.GoogleError) as error:
                client._post_retry('https://unused', {}, tries=2)
        self.assertEqual(error.exception.status, 503)
        pause.assert_called_once()

    def test_google_backoff_can_be_cancelled(self):
        client = google_api.GoogleAI('test-key')
        netutil.CANCEL_CHECK = Mock(side_effect=[None, Cancelled()])
        with patch.object(client.s, 'post', return_value=response(429)) as post, self.assertRaises(Cancelled):
            client._post_retry('https://unused', {}, tries=4)
        post.assert_called_once()

    def test_royal_retry_keeps_idempotency_key(self):
        client = royaltechno_api.RoyalTechno('test-key')
        with patch.object(client.s, 'post', side_effect=[response(503, headers={'Retry-After': '2'}), response(body={'id': 'job'})]) as post, patch('netutil.pause') as pause:
            self.assertEqual(client.create('model', {'prompt': 'hello'}), {'id': 'job'})
        self.assertEqual(post.call_args_list[0].kwargs['headers'], post.call_args_list[1].kwargs['headers'])
        pause.assert_called_once_with(2)

    def test_slider_backoff_can_be_cancelled(self):
        client = secretslider_api.SecretSlider('test-key')
        netutil.CANCEL_CHECK = Mock(side_effect=[None, Cancelled()])
        with patch.object(client.s, 'post', return_value=response(429, {'retry_after': 120})) as post, self.assertRaises(Cancelled):
            client.create_batch(['hello'])
        post.assert_called_once()

    def test_assemblyai_word_timestamps_are_seconds(self):
        client = llm_api.AssemblyAI('test-key')
        with tempfile.NamedTemporaryFile() as audio, patch.object(client.s, 'post', side_effect=[response(body={'upload_url': 'https://unused'}), response(body={'id': 'task'})]), patch.object(client.s, 'get', return_value=response(body={'status': 'completed', 'text': 'Hi.', 'words': [{'text': 'Hi.', 'start': 500, 'end': 1250}]})):
            result = client.transcribe(audio.name)
        self.assertEqual(result['words'], [{'text': 'Hi.', 'start': 0.5, 'end': 1.25}])

    def test_custom_endpoints_trim_and_reset(self):
        endpoints.configure({'openai_base_url': ' https://proxy.example/v1/ '})
        self.assertEqual(endpoints.base('openai'), 'https://proxy.example/v1')
        self.assertTrue(endpoints.is_custom('openai'))
        endpoints.configure({})
        self.assertEqual(endpoints.base('openai'), endpoints.DEFAULTS['openai'])

    def test_rewrite_language_and_youtube_parsing(self):
        self.assertEqual(rewrite.youtube_id('https://youtu.be/abcdefghijk?t=12'), 'abcdefghijk')
        self.assertEqual(rewrite.youtube_id('https://youtube.com/shorts/abcdefghijk'), 'abcdefghijk')
        with self.assertRaises(rewrite.RewriteError):
            rewrite.youtube_id('invalid')
        self.assertTrue(rewrite.wrong_language('Это русский текст. ' * 20, 'английском'))
        self.assertFalse(rewrite.wrong_language('English narration goes here. ' * 20, 'английском'))


if __name__ == '__main__':
    unittest.main()
