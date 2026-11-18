"""Create a new source-only release, preserving originals and private assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile

ROOT_FILES = {'__init__.py', 'pyproject.toml', 'requirements.txt', 'LICENSE',
              'README.md', 'CHANGELOG.md', '升级说明.md', '.gitignore', '.gitattributes'}
DIRECTORIES = {'workflow_architect', 'web', 'docs', 'tests', 'tools', 'example_workflows'}
EXTENSIONS = {'.py', '.mjs', '.js', '.css', '.json', '.yaml', '.yml', '.md', '.toml', '.txt', '.ps1'}
PRIVATE_TOOLS = {
    'build_v3_poster_assets.py', 'refine_user_swimwear.py', 'review_klein_comparison.py',
    'run_user_swimwear_test.py', 'run_klein_comparison.py', 'run_fashn_swimwear.py',
    'run_fashn_swimwear_parts.py', 'verify_v3_detailed.py', 'verify_v3_materials.py',
    'verify_v3_http.py', 'verify_v3_native_poster.py', 'accept_phase4.py',
    'verify_poster_portability.py',
}
SECRET = re.compile(r'(?:\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9_]{20,}|'
                    r'\bgithub_pat_[A-Za-z0-9_]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    archive = output.parent / (output.name + '.zip')
    if output.exists() or archive.exists() or output.is_relative_to(source):
        raise SystemExit('Use a new destination outside the source tree; nothing overwritten')
    rows, omitted, chosen = [], [], []
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part in {'.git', '__pycache__', '.pytest_cache', '.venv', 'node_modules'} for part in relative.parts):
            continue
        if path.is_symlink():
            raise SystemExit('Symlink rejected: ' + relative.as_posix())
        if not path.is_file():
            continue
        include = relative.as_posix() in ROOT_FILES or (
            relative.parts[0] in DIRECTORIES and path.suffix.lower() in EXTENSIONS)
        if relative.parts[0] == 'tools' and path.name in PRIVATE_TOOLS:
            include = False
        if not include:
            omitted.append(relative.as_posix())
            continue
        if path.stat().st_size > 4 * 1024 * 1024:
            raise SystemExit('Unexpected large source file: ' + relative.as_posix())
        content = path.read_text(encoding='utf-8')
        if SECRET.search(content):
            raise SystemExit('Potential secret; content withheld: ' + relative.as_posix())
        if re.search(r'[A-Z]:[/\\]Users[/\\][^/\\\s]+', content):
            raise SystemExit('Private user path; content withheld: ' + relative.as_posix())
        chosen.append((path, relative, digest(path)))
    output.mkdir(parents=True)
    for path, relative, expected in chosen:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        if digest(path) != expected or digest(destination) != expected:
            raise SystemExit('Source drift or copy mismatch: ' + relative.as_posix())
        rows.append({'path': relative.as_posix(), 'bytes': destination.stat().st_size,
                     'sha256': expected})
    manifest = {'version': '2.15.2', 'report_date': '2026-11-16',
                'scope': 'plugin-source-tests-templates-and-documentation-only',
                'included_files': rows, 'omitted_source_paths': omitted,
                'runtime_model_credentials_user_images_databases_included': False,
                'note': 'Manifest excludes its own hash. Local research and other projects are outside the source root.'}
    with (output / 'RELEASE_MANIFEST.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                bundle.write(path, Path('ComfyUI-WorkflowArchitect') / path.relative_to(output))
    print(json.dumps({'files': len(rows) + 1, 'source_bytes': sum(row['bytes'] for row in rows),
                      'omitted_files': len(omitted), 'archive_bytes': archive.stat().st_size,
                      'archive_sha256': digest(archive)}, indent=2))


if __name__ == '__main__':
    main()
