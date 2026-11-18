"""Start an isolated CPU acceptance server, not the user's daily instance."""
import json
from pathlib import Path
import socket
import subprocess
import uuid

def main():
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',18188))==0:raise SystemExit('Acceptance port already occupied; preserve existing process')
    root=Path('E:/AI/WorkflowArchitect-Offline')/('cpu-acceptance-'+uuid.uuid4().hex)
    root.mkdir()
    for name in ('input','output','user','temp','frontend','cache'):(root/name).mkdir()
    command=['E:/AI/ComfyUI_windows_portable/python_embeded/python.exe','-B','E:/AI/ComfyUI_windows_portable/ComfyUI/main.py',
             '--cpu','--listen','127.0.0.1','--port','18188','--disable-all-custom-nodes','--disable-api-nodes',
             '--base-directory',str(root),'--user-directory',str(root/'user'),'--input-directory',str(root/'input'),
             '--output-directory',str(root/'output'),'--temp-directory',str(root/'temp'),'--front-end-root',str(root/'frontend')]
    environment={'SystemRoot':'C:\\Windows','WINDIR':'C:\\Windows','PATH':'C:\\Windows\\System32',
                 'HF_HOME':str(root/'cache'),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                 'TORCHINDUCTOR_CACHE_DIR':str(root/'cache'),'USERNAME':'wa-owned-cpu-test',
                 'USERPROFILE':str(root/'user'),'APPDATA':str(root/'cache'),'LOCALAPPDATA':str(root/'cache'),
                 'TEMP':str(root/'temp'),'TMP':str(root/'temp')}
    with (root/'stdout.log').open('xb') as out,(root/'stderr.log').open('xb') as err:
        child=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=err,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
    receipt={'pid':child.pid,'root':str(root),'command':command,'scope':'isolated-CPU-acceptance-not-daily-8188'}
    with (root/'launch.json').open('x',encoding='utf-8') as stream:json.dump(receipt,stream,indent=2)
    print(json.dumps(receipt))

if __name__=='__main__':main()
