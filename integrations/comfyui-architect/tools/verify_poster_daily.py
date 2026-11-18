"""Verify the restarted daily HTTP server without queuing any workflow."""
import argparse
import io
import json
from pathlib import Path
import urllib.request
from urllib.parse import urlencode
import zipfile
from PIL import Image, ImageChops

BASE = 'http://127.0.0.1:8188'

def fetch(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={
        'Origin': BASE, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=35) as response:
        return response.read(), dict(response.headers)

def main(directory):
    target = directory / 'poster-daily-http-verification.json'
    if target.exists():
        raise RuntimeError('Preserve existing evidence')
    def getjson(path, body=None):
        return json.loads(fetch(path, body)[0])
    health = getjson('/workflow-architect/health')
    assert health['data']['version'] == '2.12.0'
    before = getjson('/queue')
    assert not before['queue_running'] and not before['queue_pending']
    options = getjson('/workflow-architect/poster/options', {})
    assert options['ok'] and options['data']['font_available']
    payload = json.loads((directory/'poster-prepare-request.json').read_text(encoding='utf-8'))
    response = getjson('/workflow-architect/poster/prepare', payload)
    assert response['ok'], response.get('error')
    prepared = response['data']
    assert prepared['executed'] is False and prepared['commercial_quality_approved'] is False
    ui = prepared['compiled']['ui_workflow']
    assert len(ui['nodes']) == 5 and len(ui['links']) == 5
    raw, headers = fetch('/workflow-architect/poster/package/'+prepared['package_id'])
    assert 'no-store' in headers.get('Cache-Control', '')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len([n for n in archive.namelist() if n.startswith('input/')]) == 2
        assert json.loads(archive.read('workflow.json')) == ui
    preview, _ = fetch('/view?'+urlencode(prepared['preview']))
    actual = Image.open(io.BytesIO(preview)).convert('RGB')
    with Image.open(directory/'native-poster.png') as original:
        difference = max(e[1] for e in ImageChops.difference(actual, original.convert('RGB')).getextrema())
    assert actual.size == (1600,1600) and difference <= 1
    after = getjson('/queue')
    assert not after['queue_running'] and not after['queue_pending']
    for name, data in [('daily-prepared-response.json', json.dumps(response, ensure_ascii=False, indent=2).encode()),
                       ('daily-prepared-preview.png', preview), ('daily-workflow-package.zip', raw)]:
        with (directory/name).open('xb') as stream:
            stream.write(data)
    result = {'scope':'actual-restarted-daily-8188-http-not-browser', 'runtime_version':'2.12.0',
              'asset_count':len(options['data']['assets']), 'nodes':5, 'links':5,
              'package_download_verified':True, 'preview_dimensions':list(actual.size),
              'maximum_channel_difference':difference, 'queue_empty_before_and_after':True,
              'queued_prompts':0, 'external_api_calls':0, 'visual_user_approval':False}
    with target.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--directory', type=Path, required=True)
    main(parser.parse_args().directory)
