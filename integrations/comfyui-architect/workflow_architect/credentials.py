"""Windows current-user DPAPI vault, explicit CLI writes only. No HTTP key input."""
import ctypes
import base64
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from .api_budget import REQUESTED_PROFILE
from .errors import InputError

def scope_digest(profile):
    scope={k:profile[k] for k in ('endpoint','model','protocol')}
    if scope!={k:REQUESTED_PROFILE[k] for k in scope}:raise InputError('凭证仅绑定已批准的Hubway/gpt-5.5目标')
    return hashlib.sha256(json.dumps(scope,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def dpapi(data,entropy,protect=True):
    if os.name!='nt':raise InputError('此凭证存储只支持Windows当前用户DPAPI')
    if not isinstance(data,bytes) or not 1<=len(data)<=32768:raise InputError('凭证数据长度不合法')
    class Blob(ctypes.Structure):
        _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]
    def blob(raw):
        array=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
        return Blob(len(raw),ctypes.cast(array,ctypes.POINTER(ctypes.c_ubyte))),array
    source,owned=blob(data);extra,extra_owned=blob(entropy);output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL;kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    try:
        # UI forbidden; intentionally no CRYPTPROTECT_LOCAL_MACHINE flag.
        if not fn(ctypes.byref(source),None,ctypes.byref(extra),None,None,1,ctypes.byref(output)):raise InputError('当前用户无法保护或解密凭证')
        if output.cbData>32768:raise InputError('DPAPI结果超限')
        return ctypes.string_at(output.pbData,output.cbData)
    finally:
        ctypes.memset(owned,0,len(owned));ctypes.memset(extra_owned,0,len(extra_owned))
        if output.pbData:
            ctypes.memset(output.pbData,0,output.cbData);kernel.LocalFree(ctypes.cast(output.pbData,ctypes.c_void_p))

class CredentialVault:
    def __init__(self,root):
        self.root=Path(root).absolute()
        if self.root.name!='credentials' or self.root.parent.name!='workflow_architect':raise InputError('凭证目录必须是独立workflow_architect/credentials')
        for p in (self.root,*self.root.parents):
            if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise InputError('凭证目录不允许链接或junction')
        self.path=self.root/(scope_digest(REQUESTED_PROFILE)+'.dpapi')
    def status(self):
        return {'local_credential_present':self.path.is_file(),'protection':'windows-current-user-dpapi',
                'credential_read':False,'network_enabled':False,'notice':'存在不代表密钥有效；不为状态检查解密。'}
    def _acl(self,create=False):
        if os.name!='nt':raise InputError('本机DPAPI凭证要求Windows')
        sid=subprocess.run(['whoami','/user','/fo','csv','/nh'],capture_output=True,text=True,check=True).stdout
        match=re.search(r'S-1-5-[0-9-]+',sid)
        if not match:raise InputError('无法核对当前用户SID')
        allowed={match.group(0),'S-1-5-18'}
        if create:
            if self.root.exists() and any(self.root.iterdir()) and not (self.root/'owner.json').is_file():raise InputError('拒绝改动非本插件凭证目录')
            self.root.mkdir(parents=True,exist_ok=True)
            result=subprocess.run(['icacls',str(self.root),'/inheritance:r','/grant:r','*'+match.group(0)+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True)
            if result.returncode:raise InputError('无法建立私有目录ACL')
        path=str(self.root).replace("'","''")
        script="$ErrorActionPreference='Stop'; $acl=[System.IO.Directory]::GetAccessControl('"+path+"'); @($acl.Access | ForEach-Object { @{sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value;allow=($_.AccessControlType -eq 'Allow');inherited=$_.IsInherited} }) | ConvertTo-Json -Compress"
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode('ascii')],capture_output=True,text=True)
        if result.returncode:raise InputError('无法验证凭证目录ACL')
        try:rows=json.loads(result.stdout)
        except Exception:raise InputError('凭证目录ACL响应不合法') from None
        rows=rows if isinstance(rows,list) else [rows]
        if not rows or not all(isinstance(row,dict) for row in rows):raise InputError('凭证目录ACL为空')
        if any(row['allow'] and (row['sid'] not in allowed or row['inherited']) for row in rows):raise InputError('凭证目录存在过宽读取权限，拒绝使用')
        return match.group(0)
    def store(self,key,rotate=False):
        CredentialVault(self.root)
        if self.path.is_symlink():raise InputError('凭证文件不允许链接')
        if not isinstance(key,str) or not 8<=len(key)<=4096 or any(ord(c)<33 or ord(c)>126 for c in key):raise InputError('密钥格式不合法')
        if self.path.exists() and not rotate:raise InputError('已存在凭证，须明确--rotate；不会静默覆盖')
        sid=self._acl(create=True)
        digest=scope_digest(REQUESTED_PROFILE)
        sealed=b'WAC1'+dpapi(json.dumps({'scope':digest,'key':key}).encode(),('WorkflowArchitect:'+digest).encode())
        temp=self.root/(uuid.uuid4().hex+'.pending')
        # New files inherit the already verified private ACL. No plaintext file.
        with temp.open('xb') as stream:stream.write(sealed);stream.flush();os.fsync(stream.fileno())
        backup=None
        if self.path.exists():
            backup=self.path.with_name(self.path.stem+'.previous-'+uuid.uuid4().hex+'.dpapi')
            with backup.open('xb') as stream:stream.write(self.path.read_bytes())
        os.replace(temp,self.path)
        (self.root/'owner.json').write_text(json.dumps({'app':'workflow_architect','version':1,'sid':sid}),encoding='utf-8')
        return {'stored':True,'protection':'windows-current-user-dpapi','rotated':backup is not None,'network_enabled':False}
    def read(self,scope):
        CredentialVault(self.root)
        if self.path.is_symlink():raise InputError('凭证文件不允许链接')
        digest=scope_digest(scope['profile']);self._acl()
        if not self.path.is_file() or self.path.stat().st_size>32768:raise InputError('本机凭证缺失或超限')
        raw=self.path.read_bytes()
        if not raw.startswith(b'WAC1'):raise InputError('凭证格式不合法')
        try:data=json.loads(dpapi(raw[4:],('WorkflowArchitect:'+digest).encode(),False))
        except Exception:raise InputError('当前用户无法解密此凭证') from None
        if data.get('scope')!=digest or not isinstance(data.get('key'),str):raise InputError('凭证目标绑定不匹配')
        return data['key']

def local_vault_status():
    """Status only: no create, credential decryption, environment or key reads."""
    try:
        import folder_paths
        root=Path(folder_paths.get_user_directory())/'workflow_architect'/'credentials'
        return CredentialVault(root).status()
    except Exception:
        return {'local_credential_present':False,'credential_read':False,'network_enabled':False,
                'status':'local-vault-unavailable','notice':'请在ComfyUI本机环境检查用户目录；不会搜索密钥。'}
