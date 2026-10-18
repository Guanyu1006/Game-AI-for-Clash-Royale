"""Add project Codex guidance and the remote extension without changing login state.
Run from the repository root: python configure_codex.py --apply
Dry-run is the default. Existing content is backed up before any replacement.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil

BEGIN='<!-- royale-codex-managed:start -->'
END='<!-- royale-codex-managed:end -->'
GUIDANCE='''# Royale Lab：Codex 开发约定

- 用中文解释变更、运行方法和验证结果。先读 README.md 与 docs/GITHUB_CODESPACES_ZH.md；涉及阶段三再读 CODEX_PHASE3_PROMPT.txt。
- 当前仓库同时保留 royale_ai/royale_lab 的近似基线、royale_phase2 固定版本平台、royale_phase3 研究代码。不要混淆各自的引擎、实验和验收结果。
- Codespaces 解释器是仓库内 .venv-cloud/bin/python。用显式路径调用，不能假定激活了交互终端就会影响工具启动的其他进程。
- 基本检查：.venv-cloud/bin/python tools/cloud/workspace.py doctor；测试：同入口 test；保存样例推理：infer、infer-gru；训练：train --epochs 2、train-gru --epochs 2；研究页面：serve。
- 全部项目文件、环境、缓存和结果保持在当前仓库内。新实验使用独立 outputs/ 子目录；保留已有模型、原始数据、历史证据和 baseline/。
- 保留本地 Windows 的 run_play.ps1、run_platform.ps1、run.ps1、phase2.ps1 等入口。不要用云端依赖覆盖已有 Windows 环境。
- shared_assets/ 是经审查并按哈希校验的小型可移植资产；restore 保留已有不同文件。不要批量改写哈希锁定的来源、权重或数据。
- 云端工作台是离线样例、CPU 训练和保存模型推理。原生 Android/Binder、游戏素材和官方交互等价没有完成 Codespaces 验收；未知观测保持未知，不编造数据或成功结论。
- 区分实现完成、实验达标与策略提升；训练/验证按整局划分，禁止利用 test 反复调参。模型训练完成不代表棋力提升。
- 修改后运行与变更相关的检查，保存必要日志和命令。新增依赖时更新适用的云端清单，保留历史 Windows requirements.lock。
- 登录由用户在官方 Codex 扩展中完成。不要读取、复制或输出 auth.json、API key、SSH 私钥。不要提交 .codex/、.env、环境、缓存或未审查的大文件。
- 遵守用户当前任务及已有授权。不要未经请求删除历史、重置工作区、强制推送或重写朋友的提交；遇到失败保留现场并报告可继续命令。
'''

def path_in(root,name):
    raw=root/name
    # Reject links before resolve(), including links to another place inside root.
    for part in [raw,*raw.parents]:
        if part==root:break
        if part.is_symlink():raise ValueError('Refusing symlink: '+str(part))
    result=raw.resolve();result.relative_to(root)
    return result

def merge_guidance(old):
    block=BEGIN+'\n'+GUIDANCE.rstrip()+'\n'+END
    if BEGIN in old or END in old:
        if old.count(BEGIN)!=1 or old.count(END)!=1 or old.index(END)<old.index(BEGIN):
            raise ValueError('Malformed existing managed AGENTS block; preserve it for manual review')
        return old[:old.index(BEGIN)]+block+old[old.index(END)+len(END):]
    return old.rstrip()+ ('\n\n' if old.strip() else '')+block+'\n'

def configure(root,apply=False):
    root=Path(root).resolve()
    if not path_in(root,'tools/cloud/workspace.py').is_file():raise ValueError('Run inside the Royale Lab repository root')
    config_path=path_in(root,'.devcontainer/devcontainer.json')
    config=json.loads(config_path.read_text(encoding='utf-8-sig'))
    extensions=config.setdefault('customizations',{}).setdefault('vscode',{}).setdefault('extensions',[])
    if not isinstance(extensions,list):raise ValueError('Extension configuration must be a list')
    if 'openai.chatgpt' not in extensions:extensions.append('openai.chatgpt')
    agents=path_in(root,'AGENTS.md');ignore=path_in(root,'.gitignore')
    old=agents.read_text(encoding='utf-8-sig') if agents.exists() else ''
    old_ignore=ignore.read_text(encoding='utf-8-sig') if ignore.exists() else ''
    new_ignore=old_ignore
    for entry in ('.codex/','.env','.env.*','/outputs/'):
        if entry not in new_ignore.splitlines():new_ignore=new_ignore.rstrip()+'\n'+entry+'\n'
    proposed={'.devcontainer/devcontainer.json':json.dumps(config,ensure_ascii=False,indent=2)+'\n','AGENTS.md':merge_guidance(old),'.gitignore':new_ignore}
    changes=[]
    # Validate the entire plan before mutating anything.
    for name,text in proposed.items():
        dest=path_in(root,name)
        if dest.exists() and not dest.is_file():raise ValueError('File/directory conflict: '+name)
        if not dest.exists() or dest.read_text(encoding='utf-8-sig')!=text:changes.append(name)
    backup=None
    if apply and changes:
        backup=path_in(root,'.tmp/codex-setup/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
        backup.mkdir(parents=True,exist_ok=False)
        for name in changes:
            dest=path_in(root,name)
            if dest.exists():
                saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dest,saved)
        for name in changes:
            dest=path_in(root,name);dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text(proposed[name],encoding='utf-8',newline='\n')
    return {'status':'applied' if apply else 'preview','root':str(root),'changed_files':changes,'backup':str(backup) if backup else None,
      'extension_installed':False,'codex_login_verified':False,'git_commit_or_push_performed':False,
      'next':'Install openai.chatgpt in the connected VS Code window, sign in with your own ChatGPT account, then check and commit/push the migration branch.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',default='.');p.add_argument('--apply',action='store_true');a=p.parse_args()
    print(json.dumps(configure(a.root,a.apply),ensure_ascii=False,indent=2))
