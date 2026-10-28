"""Export public files into a new checkout, without private Git history."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from build_release import public_files


def prepare(output,initialize_git=False):
    output=Path(output).resolve()
    entries=list(public_files())
    output.mkdir(parents=True,exist_ok=False)
    for name,data in entries:
        target=output/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    result={'path':str(output),'files':len(entries),
            'sha256':{name:hashlib.sha256(data).hexdigest() for name,data in entries},
            'license':'MIT','published':False}
    if initialize_git:
        def git(*args):
            return subprocess.run(['git','-C',str(output),*args],check=True,capture_output=True,text=True,timeout=20).stdout.strip()
        git('init','--initial-branch=main')
        git('add','--all')
        git('-c','user.name=Sentinella contributors','-c','user.email=contributors@sentinella.invalid',
            '-c','commit.gpgsign=false','commit','-m','Prepare Sentinella public source release')
        result['commit']=git('rev-parse','HEAD')
        result['clean']=not git('status','--porcelain')
        result['branch']=git('branch','--show-current')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--initialize-git',action='store_true')
    args=parser.parse_args()
    print(json.dumps(prepare(args.output,args.initialize_git)))
