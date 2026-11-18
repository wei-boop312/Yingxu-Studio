"""Explicit pinned public downloads into an owned isolated runtime; no pip changes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT=Path('E:/AI/WorkflowArchitect-Offline')
ASSETS=[
 ('llama-b12032-bin-win-cpu-x64.zip','https://github.com/ggml-org/llama.cpp/releases/download/b12032/llama-b12032-bin-win-cpu-x64.zip',24582754,'5296f5d18bb7d422c7c26f4bb9bc796d9d4f41e1312a8c296cff475bdc60d0d4'),
 ('Qwen3-4B-Q4_K_M.gguf','https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/bc640142c66e1fdd12af0bd68f40445458f3869b/Qwen3-4B-Q4_K_M.gguf',2497280256,'7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5')]

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(4*1024*1024),b''):h.update(part)
    return h.hexdigest()

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--download-pinned-assets',action='store_true');args=parser.parse_args()
    if not args.download_pinned_assets:raise SystemExit('Explicit download flag required')
    ROOT.mkdir(exist_ok=True)
    if ROOT.is_symlink() or ROOT.resolve()!=Path('E:/AI/WorkflowArchitect-Offline').resolve():raise RuntimeError('Unsafe runtime directory')
    if shutil.disk_usage(ROOT).free<6*1024**3:raise RuntimeError('Need 6GiB free before download')
    rows=[]
    for name,url,size,digest in ASSETS:
        target=ROOT/name
        if target.exists():
            if target.stat().st_size!=size or sha(target)!=digest:raise RuntimeError('Existing runtime asset mismatch; preserved')
        else:
            partial=target.with_suffix(target.suffix+'.part')
            if partial.exists():raise RuntimeError('Prior partial exists; preserved, no automatic retry')
            print('Downloading '+name,flush=True)
            with urllib.request.urlopen(url,timeout=45) as response,partial.open('xb') as stream:
                received=0;last=0
                while True:
                    part=response.read(4*1024*1024)
                    if not part:break
                    received+=len(part)
                    if received>size:raise RuntimeError('Download exceeds pinned size')
                    stream.write(part)
                    if received-last>=128*1024*1024:print(f'{name}: {received}/{size}',flush=True);last=received
            if partial.stat().st_size!=size or sha(partial)!=digest:raise RuntimeError('Downloaded asset SHA mismatch')
            partial.rename(target)
        rows.append({'name':name,'url':url,'bytes':size,'sha256':digest})
    binary=ROOT/'llama-b12032-cpu'
    if not binary.exists():
        with zipfile.ZipFile(ROOT/ASSETS[0][0]) as archive:
            if sum(i.file_size for i in archive.infolist())>200*1024**2:raise RuntimeError('Archive exceeds extraction bound')
            for info in archive.infolist():
                dest=(binary/info.filename).resolve()
                if not dest.is_relative_to(binary.resolve()) or info.filename.startswith(('/', '\\')) or ':' in info.filename or ((info.external_attr>>16)&0o170000)==0o120000:raise RuntimeError('Unsafe archive member')
            archive.extractall(binary)
    receipt=ROOT/'deployment.json'
    if not receipt.exists():
        with receipt.open('x',encoding='utf-8') as stream:json.dump({'model':'Qwen3-4B-Q4_K_M','assets':rows,'cpu_only':True,'external_inference_calls':0},stream,indent=2)
    print('Pinned files verified. No model started or network API inference.',flush=True)

if __name__=='__main__':main()
