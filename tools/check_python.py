"""Reject undefined names and syntax errors before building a release."""
from pathlib import Path
import sys

from pyflakes.api import checkPath
from pyflakes.messages import UndefinedLocal, UndefinedName
from pyflakes.reporter import Reporter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.build_release import MODULES


class RuntimeNames(Reporter):
    errors = 0

    def flake(self, message):
        if isinstance(message, (UndefinedName, UndefinedLocal)):
            self.errors += 1
            super().flake(message)

    def syntaxError(self, *args, **kwargs):
        self.errors += 1
        super().syntaxError(*args, **kwargs)

    def unexpectedError(self, *args, **kwargs):
        self.errors += 1
        super().unexpectedError(*args, **kwargs)


if __name__ == '__main__':
    report = RuntimeNames(sys.stdout, sys.stderr)
    paths = [*(ROOT / (module+'.py') for module in MODULES), *(ROOT/'tests').glob('*.py'), *(ROOT/'tools').glob('*.py')]
    for path in paths:
        checkPath(str(path), report)
    print(f'Undefined-name and syntax checks: {len(paths)} files, {report.errors} errors')
    raise SystemExit(bool(report.errors))
