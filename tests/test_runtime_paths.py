import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import runtime_paths as runtime
from startup_manager import owns_shortcut
from unittest.mock import Mock
from types import SimpleNamespace


class RuntimePathTests(unittest.TestCase):
    def test_frozen_workers_use_bundled_executable_and_user_state_is_separate(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime.sys, "frozen", True, create=True), patch.dict(os.environ, {"LOCALAPPDATA":folder}):
            os.environ.pop("SENTINELLA_DATA_DIR", None)
            self.assertEqual(runtime.data_root(),Path(folder)/"Sentinella")
            command=runtime.command("notification_backend", executable="Sentinella.exe")
            self.assertEqual(command,["Sentinella.exe","--process","notification_backend"])

    def test_isolated_profile_must_be_absolute_and_is_inherited_without_code_lookup(self):
        with patch.dict(os.environ,{"SENTINELLA_DATA_DIR":"relative"}):
            with self.assertRaises(ValueError):
                runtime.data_root()
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ,{"SENTINELLA_DATA_DIR":folder}):
            self.assertEqual(runtime.data_root(),Path(folder).resolve())
            self.assertEqual(Path(runtime.command("adapter_entry")[2]).parent,runtime.SOURCE_ROOT)
        with self.assertRaises(ValueError):
            runtime.command("arbitrary_module")

    def test_packaged_startup_link_ownership_never_accepts_another_executable(self):
        import startup_manager
        with patch("startup_manager.frozen",return_value=True),patch("startup_manager.sys.executable",str(Path('Sentinella.exe').resolve())):
            target,args=startup_manager.startup_target()
            self.assertTrue(owns_shortcut(SimpleNamespace(TargetPath=target,Arguments=args)))
            self.assertFalse(owns_shortcut(SimpleNamespace(TargetPath=target+'other',Arguments=args)))

    def test_uninstall_leaves_another_installations_startup_link_untouched(self):
        from startup_manager import remove_owned_startup
        with tempfile.TemporaryDirectory() as folder:
            link=Path(folder)/'Sentinella.lnk'
            link.write_bytes(b'owned by another installation')
            client=Mock()
            client.CreateShortcut.return_value=SimpleNamespace(TargetPath='other.exe',Arguments='--background')
            with patch('startup_manager.startup_path',return_value=link),patch('startup_manager.shell',return_value=client):
                remove_owned_startup()
            self.assertTrue(link.exists())
