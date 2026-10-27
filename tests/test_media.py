"""Offline media regression tests; FFmpeg outputs are probed and decoded."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image

import media_tools
import pipeline
import subtitles
import tts_api


class ToolTests(unittest.TestCase):
    def test_same_filename_different_audio_has_separate_cache(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pipeline, "data_dir", return_value=directory):
            root = Path(directory)
            (root / "one").mkdir()
            (root / "two").mkdir()
            first, second = root / "one/audio.mp3", root / "two/audio.mp3"
            first.write_bytes(b"recording one")
            second.write_bytes(b"recording two")
            a, _ = pipeline.job_dir_for(str(first))
            b, _ = pipeline.job_dir_for(str(second))
            self.assertNotEqual(a, b)
            self.assertEqual(pipeline.job_dir_for(str(first))[0], a)
            first.write_bytes(b"replacement")
            self.assertNotEqual(pipeline.job_dir_for(str(first))[0], a)

    def test_finder_homebrew_discovery(self):
        with patch.object(media_tools.sys, "platform", "darwin"), \
             patch.object(media_tools.shutil, "which", return_value=None), \
             patch.object(Path, "is_file", lambda p: str(p) == "/opt/homebrew/bin/ffmpeg"), \
             patch.object(media_tools.os, "access", return_value=True):
            self.assertEqual(media_tools.ffmpeg_bin(), str(Path("/opt/homebrew/bin/ffmpeg").resolve()))

    def test_cancellation_reaps_process(self):
        spawned = []
        original = subprocess.Popen
        def record(*args, **kwargs):
            proc = original(*args, **kwargs)
            spawned.append(proc)
            return proc
        stop = threading.Event()
        timer = threading.Timer(0.3, stop.set)
        def check():
            if stop.is_set():
                raise pipeline.Cancelled("test")
        timer.start()
        before = time.monotonic()
        try:
            with patch.object(media_tools.subprocess, "Popen", side_effect=record):
                with self.assertRaises(pipeline.Cancelled):
                    media_tools.run_media([sys.executable, "-c", "import time; time.sleep(30)"], cancel_check=check)
            self.assertLess(time.monotonic() - before, 3)
            self.assertIsNotNone(spawned[0].poll())
        finally:
            timer.cancel()

    def test_tts_empty_text_rejected_before_network(self):
        for cls in (tts_api.EdgeTTS, tts_api.FastGenTTS, tts_api.RoyalTTS):
            with self.subTest(service=cls.__name__):
                instance = object.__new__(cls)
                with self.assertRaisesRegex(tts_api.TTSError, "Нет текста"):
                    instance.synthesize("  ", "", "unused.mp3")

    def test_voiceover_cache_tracks_voice_and_text(self):
        pipeline.CANCEL.clear()
        calls = []
        class FakeTTS:
            def synthesize(self, text, voice_id, out, progress=None):
                calls.append((text, voice_id))
                Path(out).write_bytes(b"audio" * 300)
        with tempfile.TemporaryDirectory() as directory, patch.object(pipeline, "make_tts", return_value=FakeTTS()):
            def make(text, voice):
                return pipeline.make_voiceover({}, text, "edge", voice, directory, lambda *a: None)
            make("Hello", "voice1")
            make("Hello", "voice1")
            self.assertEqual(len(calls), 1)
            make("Hello", "voice2")
            make("Changed text", "voice2")
            self.assertEqual(calls, [("Hello", "voice1"), ("Hello", "voice2"), ("Changed text", "voice2")])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class MediaTests(unittest.TestCase):
    def setUp(self):
        pipeline.CANCEL.clear()
        self.tmp = tempfile.TemporaryDirectory(prefix="kadrogen test '")
        self.root = Path(self.tmp.name)
        self.size = patch.multiple(pipeline, W=320, H=180)
        self.size.start()
        self.img = self.root / "кадр.png"
        Image.new("RGB", (640, 360), "darkblue").save(self.img)
        self.audio = self.root / "audio.wav"
        pipeline.run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(self.audio)])

    def tearDown(self):
        pipeline.CANCEL.clear()
        self.size.stop()
        self.tmp.cleanup()

    def probe(self, path):
        r = subprocess.run([media_tools.ffmpeg_bin("ffprobe"), "-v", "error", "-show_streams", "-show_format",
                            "-of", "json", str(path)], check=True, capture_output=True)
        # Decode every frame; container metadata alone does not prove usability.
        pipeline.run_ffmpeg(["-i", str(path), "-f", "null", "-"])
        return json.loads(r.stdout)

    def test_still_motion_and_cache_duration(self):
        for index in (0, 1):
            out = str(self.root / f"scene{index}.mp4")
            pipeline.render_scene_clip(str(self.img), index, 0.4, out)
            self.assertAlmostEqual(float(self.probe(out)["format"]["duration"]), 0.4, delta=0.05)
            pipeline.render_scene_clip(str(self.img), index, 0.8, out)
            self.assertAlmostEqual(float(self.probe(out)["format"]["duration"]), 0.8, delta=0.05)
            stat = os.stat(out).st_mtime_ns
            pipeline.render_scene_clip(str(self.img), index, 0.8, out)
            self.assertEqual(os.stat(out).st_mtime_ns, stat)

    def test_video_extension_and_cache(self):
        src = str(self.root / "source.mp4")
        pipeline.render_scene_clip(str(self.img), 0, 0.4, src)
        out = str(self.root / "video.mp4")
        for duration in (0.8, 1.2):
            pipeline.render_video_clip(src, duration, out)
            info = self.probe(out)
            self.assertAlmostEqual(float(info["format"]["duration"]), duration, delta=0.05)
            video = next(s for s in info["streams"] if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (320, 180))

    def test_assembly_transitions_and_audio(self):
        images = self.root / "images"
        images.mkdir()
        scenes = [{"index": i, "duration": 1.0} for i in range(2)]
        for i in range(2):
            Image.new("RGB", (640, 360), ("darkblue", "darkred")[i]).save(images / f"{i:04d}.png")
        for motion in (False, True):
            out = str(self.root / f"assembled{motion}.mp4")
            pipeline.assemble_video(scenes, str(images), {}, str(self.audio), str(self.root), out, lambda *a: None, motion=motion)
            info = self.probe(out)
            self.assertEqual({s["codec_type"] for s in info["streams"]}, {"audio", "video"})
            self.assertAlmostEqual(float(info["format"]["duration"]), 2.0, delta=0.15)

    def test_subtitles_special_filename_render_pixels(self):
        src = str(self.root / "source.mp4")
        pipeline.render_scene_clip(str(self.img), 0, 1.2, src)
        srt = self.root / "суб'титры: [1],.srt"
        subtitles.write_srt([{"text": "HELLO", "start": 0, "end": 1.2}], str(srt))
        out = str(self.root / "subtitled.mp4")
        subtitles.burn(media_tools.ffmpeg_bin(), src, str(srt), out)
        self.probe(out)
        frame = pipeline.run_ffmpeg(["-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
        # White subtitle pixels differ from the solid blue background.
        pixels = zip(frame[0::3], frame[1::3], frame[2::3])
        self.assertGreater(sum(r > 180 and g > 180 and b > 180 for r, g, b in pixels), 20)

    def test_speed_copies(self):
        src = str(self.root / "with_audio.mp4")
        pipeline.run_ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=1", "-i", str(self.audio),
                             "-c:v", "libx264", "-c:a", "aac", "-shortest", src])
        copies = pipeline.make_speed_copies(src, lambda *a: None)
        self.assertEqual(len(copies), 2)
        for path in copies:
            info = self.probe(path)
            self.assertEqual({s["codec_type"] for s in info["streams"]}, {"audio", "video"})


if __name__ == "__main__":
    unittest.main()
