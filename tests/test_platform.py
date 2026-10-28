import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import fastgen_api
import platform_support as native


class DataDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_data = fastgen_api._DATA_DIR
        fastgen_api._DATA_DIR = None
        self.addCleanup(setattr, fastgen_api, "_DATA_DIR", self.old_data)

    def test_mac_data_outside_bundle(self):
        with patch.dict(os.environ, {"HOME": self.tmp.name, "USERPROFILE": self.tmp.name, "KADROGEN_DATA_DIR": ""}), \
                patch.object(sys, "platform", "darwin"), \
                patch.object(fastgen_api, "app_dir", return_value="/Applications/KadroGen.app/Contents/MacOS"):
            data = fastgen_api.data_dir()
        self.assertEqual(Path(data), Path(self.tmp.name, "Library", "Application Support", "KadroGen"))
        Path(data, "config.json").write_text("{}")
        self.assertEqual(Path(data, "config.json").read_text(), "{}")

    def test_explicit_directory_created_and_reused(self):
        destination = os.path.join(self.tmp.name, "данные с пробелами")
        with patch.dict(os.environ, {"KADROGEN_DATA_DIR": destination}):
            self.assertEqual(fastgen_api.data_dir(), destination)
            self.assertEqual(fastgen_api.data_dir(), destination)
        self.assertTrue(os.path.isdir(destination))

    def test_writable_source_keeps_user_probe_file(self):
        probe = Path(self.tmp.name, ".write_test")
        probe.write_text("user content")
        with patch.dict(os.environ, {"KADROGEN_DATA_DIR": ""}), \
                patch.object(sys, "platform", "linux"), \
                patch.object(fastgen_api, "app_dir", return_value=self.tmp.name):
            self.assertEqual(fastgen_api.data_dir(), self.tmp.name)
        self.assertEqual(probe.read_text(), "user content")


class NativeDesktopTests(unittest.TestCase):
    def test_mac_open_and_reveal_preserve_unicode_spaces(self):
        path = os.path.join(tempfile.gettempdir(), "Видео с пробелами.mp4")
        with patch.object(sys, "platform", "darwin"), patch.object(native.subprocess, "Popen") as run:
            native.open_path(path)
            run.assert_called_with(["open", path])
            native.reveal_path(path)
            run.assert_called_with(["open", "-R", path])

    def test_mac_clipboard_uses_utf8_stdin(self):
        with patch.object(sys, "platform", "darwin"), patch.object(native.subprocess, "run") as run:
            self.assertTrue(native.copy_text("Отчёт\n動画"))
            run.assert_called_once_with(["pbcopy"], input="Отчёт\n動画".encode("utf-8"), check=True, timeout=10)

    def test_mac_awake_process_stops(self):
        process = Mock()
        process.poll.return_value = None
        with patch.object(sys, "platform", "darwin"), \
                patch.object(native, "_awake_process", None), \
                patch.object(native.subprocess, "Popen", return_value=process) as start:
            native.set_keep_awake(True)
            native.set_keep_awake(True)
            start.assert_called_once_with(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())])
            native.set_keep_awake(False)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=5)
            self.assertIsNone(native._awake_process)

    def test_legacy_updates_disabled_on_every_platform(self):
        for system, frozen, expected in [("darwin", True, False), ("linux", True, False),
                                         ("win32", False, False), ("win32", True, False)]:
            with self.subTest(system=system, frozen=frozen), \
                    patch.object(sys, "platform", system), patch.object(sys, "frozen", frozen, create=True):
                self.assertEqual(native.windows_updates_supported(), expected)

    def test_missing_mac_gui_reports_dependencies_without_windows_call(self):
        with patch.object(sys, "platform", "darwin"), \
                patch.object(native.importlib, "import_module", side_effect=ImportError("no webview")):
            with self.assertRaisesRegex(RuntimeError, "PyObjC"):
                native.load_webview()

    @unittest.skipIf(sys.platform == "win32", "POSIX flock check")
    def test_single_instance_lock_excludes_other_process(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(native, "_instance_file", None):
            self.assertTrue(native.acquire_instance_lock(directory))
            try:
                check = subprocess.run([sys.executable, "-c",
                                        "import platform_support as p,sys; print(p.acquire_instance_lock(sys.argv[1]))",
                                        directory], cwd=Path(__file__).resolve().parents[1],
                                       capture_output=True, text=True, check=True)
                self.assertEqual(check.stdout.strip(), "False")
            finally:
                native._instance_file.close()
                native._instance_file = None
            check = subprocess.run([sys.executable, "-c",
                                    "import platform_support as p,sys; print(p.acquire_instance_lock(sys.argv[1]))",
                                    directory], cwd=Path(__file__).resolve().parents[1],
                                   capture_output=True, text=True, check=True)
            self.assertEqual(check.stdout.strip(), "True")

    def test_app_import_and_help_without_gui(self):
        script = """
import sys
class NoGUI:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'webview':
            raise AssertionError('GUI was loaded in headless mode')
sys.meta_path.insert(0, NoGUI())
import app
assert app.webview is None
sys.argv = ['app.py', '--help']
app.main()
assert app.check_update() is None
"""
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "KADROGEN_DATA_DIR": directory}
            result = subprocess.run([sys.executable, "-c", script], env=env,
                                    cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, check=True)
            self.assertIn("--run AUDIO", result.stdout)


if __name__ == "__main__":
    unittest.main()
