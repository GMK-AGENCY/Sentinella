import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import ast

from tools.build_release import build
from startup_manager import set_startup


class ReleaseTests(unittest.TestCase):
    def test_release_contains_only_public_sources_and_verified_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "source.zip"
            result = build(archive)
            self.assertGreater(result["files"], 30)
            with zipfile.ZipFile(archive) as z:
                names = z.namelist()
                for name in names:
                    self.assertFalse(name.startswith(("logs/", ".venv/", "test-project/")))
                    self.assertNotIn(name, ["approval-config.json", "messages-config.json", "claude_test_setup.py", "README.txt"])
                    self.assertNotIn("..", Path(name).parts)
                manifest = json.loads(z.read("release-manifest.json"))["sha256"]
                self.assertEqual(set(manifest), set(names) - {"release-manifest.json"})
                for name, digest in manifest.items():
                    self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(), digest)
                # A GUI that opens while a missing worker crashes is not a
                # working release. Check the local import dependency closure.
                root = Path(__file__).resolve().parents[1]
                for name in [n for n in names if n.endswith(".py")]:
                    tree = ast.parse(z.read(name).decode("utf-8-sig"))
                    for node in ast.walk(tree):
                        imports = [node.module] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names] if isinstance(node, ast.Import) else []
                        for module in imports:
                            candidate = (module or "").split(".")[0] + ".py"
                            if (root / candidate).is_file():
                                self.assertIn(candidate, names, f"{name} imports missing local module")
            with self.assertRaises(FileExistsError):
                build(archive)

    def test_another_installations_startup_link_is_never_overwritten_or_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Sentinella.lnk"
            path.write_bytes(b"other install")
            client = Mock()
            client.CreateShortcut.return_value.TargetPath = "C:/Other/.venv/Scripts/pythonw.exe"
            client.CreateShortcut.return_value.Arguments = '"C:/Other/sentinel_launcher.py" --background'
            with patch("startup_manager.load_config") as load:
                for enabled in [True, False]:
                    with self.assertRaises(ValueError):
                        set_startup(enabled, path, client)
                load.assert_not_called()
            self.assertEqual(path.read_bytes(), b"other install")
