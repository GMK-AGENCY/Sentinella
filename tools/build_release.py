"""Build a source release from an explicit allowlist, never from a live folder."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULES = """adapter_entry agenda_view app_icon app_version approval_desktop approval_policy approval_simulation approvals_panel
automation_policy bounded_process chat_catalog chat_identity chat_desktop claude_delivery codex_delivery codex_local_sender codex_permission_selftest
config_store dashboard_model default_message_templates desktop_identity file_recovery loop_diagnostics message_engine message_library message_templates message_worker
messages_panel notification_backend notification_core notification_watch operations_view performance_check
polling_policy question_desktop question_supervisor schedule_display sentinel_launcher service_recovery settings_panel startup_manager
switch_control runtime_paths sentinella_app release_smoke ui_components unsaved_changes history_cleanup workspace_ui""".split()


def public_files(root=ROOT):
    root = Path(root)
    names = [*(name + ".py" for name in MODULES), "README.md", "SECURITY.md", "CHANGELOG.md", "LICENSE", "CONTRIBUTING.md",
             "requirements.txt", ".gitignore", ".gitattributes", "Avvia Sentinella.cmd", "Installa Sentinella.cmd",
             ".github/workflows/test.yml", ".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/feature_request.yml", "tools/build_release.py", "tools/build_windows.py", "tools/check_python.py", "requirements-build.txt", "installer/Sentinella.iss", "assets/sentinella.ico", "assets/sentinella.png"]
    names += [p.relative_to(root).as_posix() for p in sorted((root / "tests").glob("test_*.py"))]
    names += ["tests/native_controls_fixture.py", "tests/verify_native_controls.py", "tests/verify_command_fixture.py", "tests/smoke_clean_install.py", "tools/prepare_github.py", "assets/licenses/PyWinRT-LICENSE", "assets/licenses/Tcl-LICENSE", "assets/licenses/SOURCES.md"]
    for name in names:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or linked release source: {name}")
        if name in {'assets/sentinella.ico','assets/sentinella.png'}:
            yield name,path.read_bytes()
            continue
        text = path.read_text(encoding="utf-8-sig")
        profiles = re.findall(r'''[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s"']+)''', text, re.I)
        private_profile = any(name.casefold() not in {"test", "public", "default", "example", "<user>"} for name in profiles)
        if re.search(r"sk-[A-Za-z0-9_-]{24,}", text) or private_profile:
            raise ValueError(f"Potential private data in release source: {name}")
        yield name, path.read_bytes()


def build(output, root=ROOT):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = list(public_files(root))
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in entries}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)
        archive.writestr("release-manifest.json", json.dumps({"sha256": manifest}, indent=2) + "\n")
    return {"path": str(output.resolve()), "files": len(entries), "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/Sentinella-source.zip")
    args = parser.parse_args()
    print(json.dumps(build(args.output)))
