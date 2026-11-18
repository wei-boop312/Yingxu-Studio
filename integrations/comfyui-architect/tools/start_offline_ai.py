"""Explicit verified local model launcher; never installs an autostart task."""
import argparse
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import zipfile
from deploy_offline_ai import ROOT,ASSETS,sha

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--start',action='store_true');args=parser.parse_args()
    if not args.start:raise SystemExit('Use --start explicitly')
    for name,_,size,digest in ASSETS:
        path=ROOT/name
        if not path.is_file() or path.stat().st_size!=size or sha(path)!=digest:raise SystemExit('Pinned model/runtime failed verification')
    binary=ROOT/'llama-b12032-cpu'
    with zipfile.ZipFile(ROOT/ASSETS[0][0]) as archive:
        for info in archive.infolist():
            if info.is_dir():continue
            local=binary/info.filename
            if local.is_symlink() or not local.is_file() or hashlib.sha256(local.read_bytes()).digest()!=hashlib.sha256(archive.read(info)).digest():raise SystemExit('Extracted runtime changed')
    matches=list(binary.rglob('llama-server.exe'))
    if len(matches)!=1:raise SystemExit('Expected exactly one pinned server binary')
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1',18189))==0:raise SystemExit('Port occupied; do not replace or stop an unknown service')
    exe=matches[0]
    command=[str(exe),'-m',str(ROOT/ASSETS[1][0]),'--alias','wa-qwen3-4b','--host','127.0.0.1','--port','18189',
             '--ctx-size','8192','--threads','6','--parallel','1','--n-gpu-layers','0','--offline','--no-webui','--no-agent',
             '--no-ui-mcp-proxy','--jinja','--cors-origins','http://127.0.0.1:8188,http://localhost:8188']
    # Do not inherit API keys, proxy, model auto-download or agent environment flags.
    environment={'SystemRoot':'C:\\Windows','WINDIR':'C:\\Windows','PATH':'C:\\Windows\\System32'}
    with (ROOT/'server-stdout.log').open('ab') as out,(ROOT/'server-stderr.log').open('ab') as err:
        process=subprocess.Popen(command,cwd=exe.parent,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                                 env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
    receipt=ROOT/('launch-'+str(process.pid)+'.json')
    with receipt.open('x',encoding='utf-8') as stream:json.dump({'pid':process.pid,'command':command,'cpu_only':True,'auto_start':False},stream,indent=2)
    print(json.dumps({'pid':process.pid,'receipt':str(receipt),'ready':False,'notice':'Poll health; a PID is not successful model loading.'}))

if __name__=='__main__':main()
