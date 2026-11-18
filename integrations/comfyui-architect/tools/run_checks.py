"""Run release regressions with explicit imports and owned temporary storage."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.report and args.report.exists():
        raise SystemExit('Report exists; preserve previous evidence and choose a new path')
    root = Path(__file__).resolve().parents[1]
    python_code = (
        'import sys,unittest;sys.path.insert(0,sys.argv[1]);'
        'suite=unittest.defaultTestLoader.discover("tests");'
        'result=unittest.TextTestRunner(verbosity=1).run(suite);'
        'sys.exit(not result.wasSuccessful())'
    )
    commands = [('python', [sys.executable, '-B', '-c', python_code, str(root)])]
    node = shutil.which('node')
    if node:
        commands += [(name, [node, 'tests/' + name + '.mjs']) for name in
                     ('test_knowledge_panel', 'test_detail_runner')]
    rows = []
    with tempfile.TemporaryDirectory(prefix='workflow-architect-checks-') as scratch:
        environment = dict(os.environ)
        environment.update(TEMP=scratch, TMP=scratch, TMPDIR=scratch,
                           PYTHONDONTWRITEBYTECODE='1')
        for name, command in commands:
            result = subprocess.run(command, cwd=root, env=environment,
                                    capture_output=True, text=True,
                                    encoding='utf-8', errors='replace')
            output = result.stdout + result.stderr
            rows.append({'check': name, 'exit_code': result.returncode, 'output': output})
            print(name + ': ' + str(result.returncode), flush=True)
            print(output, end='' if output.endswith('\n') else '\n', flush=True)
    ok = bool(node) and all(row['exit_code'] == 0 for row in rows)
    report = {'all_passed': ok, 'node_available': bool(node),
              'scope': 'unit-integration-and-mock-DOM-not-live-model-or-GPU-acceptance',
              'checks': rows}
    if args.report:
        with args.report.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    if not node:
        print('Node.js missing: frontend checks NOT executed.', file=sys.stderr)
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
