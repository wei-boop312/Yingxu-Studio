"""Run personally in a local terminal. Never pass keys as CLI arguments."""
import argparse
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workflow_architect.credentials import CredentialVault

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--store',action='store_true');p.add_argument('--rotate',action='store_true');a=p.parse_args()
    vault=CredentialVault(a.root)
    if not a.store:print(vault.status());return
    if not sys.stdin.isatty():raise SystemExit('请在本人本机交互终端运行，不从管道/文件/聊天读取密钥')
    print('仅保存Windows当前用户加密凭证，不启用联网。请先撤销已暴露旧密钥。')
    key=getpass.getpass('输入新密钥（不回显）: ');again=getpass.getpass('再次输入: ')
    if key!=again:raise SystemExit('两次不一致，未保存')
    print(vault.store(key,rotate=a.rotate));key=again=None
if __name__=='__main__':main()
