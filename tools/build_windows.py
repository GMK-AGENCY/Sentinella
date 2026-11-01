"""Build the self-contained Windows app; use only explicit public modules."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import importlib.metadata
import shutil
import uuid
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_release import MODULES, public_files


def build(output):
    # Run the same source privacy/import-closure gate before collecting code.
    list(public_files())
    from app_version import VERSION
    version=tuple(map(int,VERSION.split('.')))+(0,)
    version_file=output/'version-info.txt'
    version_file.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers="+repr(version)+",prodvers="+repr(version)+",mask=0x3f,flags=0x0,OS=0x40004,fileType=0x1,subtype=0x0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','Sentinella contributors'),StringStruct('FileDescription','Sentinella'),StringStruct('FileVersion','"+VERSION+"'),StringStruct('ProductName','Sentinella'),StringStruct('ProductVersion','"+VERSION+"'),StringStruct('OriginalFilename','Sentinella.exe')])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])",encoding='utf-8')
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--windowed", "--noupx",
            "--icon",str(ROOT/'assets/sentinella.ico'),"--version-file",str(version_file),
            "--name", "Sentinella", "--distpath", str(output / "dist"), "--workpath", str(output / "work"),
            "--specpath", str(output), "--collect-all", "uiautomation", "--collect-submodules", "winrt",
            "--hidden-import", "comtypes.gen.UIAutomationClient", "--hidden-import", "comtypes.gen.stdole",
            "--copy-metadata", "comtypes", "--copy-metadata", "psutil"]
    for name in MODULES:
        args += ["--hidden-import", name]
    for name in ["README.md", "SECURITY.md", "CHANGELOG.md", "LICENSE"]:
        args += ["--add-data", str(ROOT / name) + ":."]
    for name in ['sentinella.ico','sentinella.png']:
        args += ['--add-data',str(ROOT/'assets'/name)+':assets']
    args += ['--add-data',str(ROOT/'assets/native_claude_bridge')+':assets/native_claude_bridge']
    args.append(str(ROOT / "sentinella_app.py"))
    # The orchestrator polls this process; no synchronous wait is unbounded.
    return subprocess.Popen(args, cwd=ROOT, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def notices(bundle):
    output = bundle / "THIRD_PARTY_NOTICES"
    output.mkdir(exist_ok=True)
    distributions = ["comtypes", "psutil", "uiautomation", "typing_extensions", "winrt-runtime", "pyinstaller",
        "winrt-Windows.UI.Notifications.Management", "winrt-Windows.UI.Notifications", "winrt-Windows.Foundation",
        "winrt-Windows.Foundation.Collections", "winrt-Windows.Data.Xml.Dom", "winrt-Windows.ApplicationModel"]
    for name in distributions:
        dist = importlib.metadata.distribution(name)
        for file in dist.files or []:
            if any(part.lower().startswith(("license", "copying", "notice")) for part in file.parts):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = output / (name + "-" + file.name)
                    shutil.copyfile(source, target)
    shutil.copyfile(Path(sys.base_prefix) / "LICENSE.txt", output / "Python-LICENSE.txt")
    shutil.copyfile(ROOT/'LICENSE',output/'Sentinella-LICENSE.txt')
    for name in ['PyWinRT-LICENSE','Tcl-LICENSE','SOURCES.md']:
        shutil.copyfile(ROOT/'assets/licenses'/name,output/name)
    shutil.copyfile(Path(sys.base_prefix)/'tcl/tk8.6/license.terms',output/'Tk-LICENSE.txt')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iscc", type=Path)
    parser.add_argument('--build-timeout',type=int,default=300)
    args = parser.parse_args()
    if not 1<=args.build_timeout<=600:parser.error('Build timeout must be within 1..600 seconds')
    output=ROOT / "build" / ("windows-" + str(uuid.uuid4()))
    output.mkdir(parents=True)
    bundle=output / "dist/Sentinella"
    process = build(output)
    print(json.dumps({"build_pid":process.pid,"bundle":str(bundle)}),flush=True)
    deadline=time.monotonic()+args.build_timeout
    while True:
        remaining=deadline-time.monotonic()
        if remaining<=0:
            from bounded_process import stop_owned
            stop_owned(process,{})
            raise SystemExit(f'Build did not finish within {args.build_timeout} seconds')
        try:
            result=process.wait(timeout=min(5,remaining))
            break
        except subprocess.TimeoutExpired:pass
    if result:
        raise SystemExit(result)
    notices(bundle)
    (ROOT / "build/latest-windows.json").write_text(json.dumps({"bundle":str(bundle)}),encoding="utf-8")
    if args.iscc:
        from app_version import VERSION
        raise SystemExit(subprocess.run([str(args.iscc.resolve()), "/DAppVersion="+VERSION, "/DBinaryDir="+str(bundle), str(ROOT / "installer/Sentinella.iss")], cwd=ROOT, timeout=55).returncode)
