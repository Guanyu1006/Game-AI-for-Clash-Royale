# 当前 Codespace：完成 Codex 配置和 GitHub 上传

目标仓库：https://github.com/Guanyu1006/Game-AI-for-Clash-Royale

2026-10-19 已有证据：用户提供的 Linux Codespaces 日志显示 doctor 成功、572个资产通过校验、51项测试通过、3个保存样例推理匹配。该日志不证明正式训练、Git推送或原生对局已完成。当前本地会话与远程Codespace是不同执行环境，登录状态不会自动继承。

## 1. 在已经连接 Codespace 的 VS Code 中完成配置

不要重新导入整个旧ZIP，也不要删除980个改动。将本次提供的 configure_codex.py 单文件拖进远程仓库根目录（与README.md同级）。如果已有同名文件且不是本次脚本，先保留副本。

终端若显示单独的 >，先按 Ctrl+C。只复制代码框中的命令，不复制三个反引号或终端提示符。

```bash
cd /workspaces/Game-AI-for-Clash-Royale
python configure_codex.py --apply
code --install-extension openai.chatgpt
```

配置脚本只合并官方扩展ID、AGENTS.md项目指引与必要忽略项；旧内容保留，替换前备份到 .tmp/codex-setup/。它不登录、不读取凭据、不提交或推送。再次执行没有新增变更时不会继续改写。

如果 code 命令不可用，在左侧扩展面板搜索 @id:openai.chatgpt，确认OpenAI发布，点击在当前Codespace中安装。等待完成；需要时执行 Developer: Reload Window。Ctrl+Shift+P → Codex: Open Codex Sidebar → Sign in with ChatGPT。必须由本人完成浏览器登录，不要把账号文件、登录码或API key发给他人。

终端没有 codex 命令不妨碍使用IDE扩展。不需要为此重新装Python，也不必改用其他开发环境。

## 2. 在云端完成实际验证

使用显式解释器，不依赖终端是否激活。逐条运行，某条报错时保存输出，修好再继续：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py restore
.venv-cloud/bin/python tools/cloud/workspace.py doctor
.venv-cloud/bin/python tools/cloud/workspace.py test
.venv-cloud/bin/python tools/cloud/workspace.py infer
.venv-cloud/bin/python tools/cloud/workspace.py infer-gru
.venv-cloud/bin/python tools/cloud/workspace.py train --epochs 2
.venv-cloud/bin/python tools/cloud/workspace.py train-gru --epochs 2
```

若.venv-cloud缺失或依赖损坏，运行 bash tools/cloud/bootstrap.sh --ml 后重试。不要在运行中的训练旁反复重装依赖。新结果在 outputs/cloud/ 下；输出被忽略，避免训练日志和模型无意进入源码提交。

研究页面：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py serve
```

Ports → 8770打开；默认保持Private。它展示保存样例与真实模型推理，并不连接原生游戏。结束服务按Ctrl+C。要执行Git命令，可以另开一个终端。

## 3. 检查当前分支并提交上传

```bash
git remote -v
git branch --show-current
git status --short
```

核对 origin 指向目标仓库，当前分支是 setup/codespaces-20261018（与你的截图一致）。如果已经切换到main，不要直接在main提交：先用 git switch -c setup/codespaces-20261019 建立迁移分支，后续push使用这个实际分支名。

源码、docs、tests、shared_assets、AGENTS.md、configure_codex.py和.devcontainer应纳入提交；.venv、.cache、.tmp、.codex、outputs、ZIP、APK、VM、密钥不应纳入。普通的 git add 不会绕过忽略规则，不要使用 -f。如果之前已经强制暂存这些文件，应先取消暂存（不会删除工作文件），再检查。

```bash
git add --all
git diff --cached --stat
```

查看列表是否符合上述范围。确认后提交：

```bash
git commit -m "Set up shared Codespaces development and Codex guidance"
git push -u origin HEAD
```

若没有新改动且提示 nothing to commit，说明无需重复提交，仍可执行push。若提示缺少Git作者身份，在当前仓库设置自己的身份：

```bash
git config user.name "你的GitHub用户名"
git config user.email "你在GitHub设置中确认的提交邮箱"
```

不要直接复制占位身份。没有使用 --global，不影响其他项目。403表示当前账号没有Write权限或当前授权不包含该仓库；请朋友为自己的账号授予协作者Write权限。不要共享密码或token。push拒绝且提示远程已有新提交时，先保存现场，与朋友核对远程改动，不要使用 --force。

## 4. 严格确认已上传

```bash
git status -sb
git rev-parse HEAD
git ls-remote --heads origin "$(git branch --show-current)"
```

本地没有未提交文件、后两条的哈希相同，表明当前提交已上传对应分支。到GitHub页面选择该迁移分支，应能看到源码目录、shared_assets、AGENTS.md和.devcontainer。创建pull request让朋友审阅合并。分支上传与合并到main是两个步骤；不需要强制覆盖main。

合并后在当前云端先确认工作区干净，再切换main并 git pull --ff-only；之后 Ctrl+Shift+P → Codespaces: Rebuild Container。初始化结束再运行doctor和test。只有重建和实际命令成功后，才把该环境标记为已验证。

## 5. 两个人如何用

共用当前云端工作区：Live Share邀请朋友，按需要共享终端和8770服务。分别研究：各自在分支/Codespace工作，用提交和PR同步。GitHub权限、Codespaces访问、Live Share会话、Codex账号是四个不同层面的配置，各自使用自己的账号。不同时指挥两个agent修改同一份文件。

在Codex聊天框（不是终端）发送：

> 请读取AGENTS.md、README.md和docs/GITHUB_CODESPACES_ZH.md。执行pwd并确认工作区位于/workspaces/Game-AI-for-Clash-Royale。使用.venv-cloud/bin/python运行doctor、test和infer，基于实际输出解释当前系统。仅完成本次检查，不自动展开全部阶段三任务；保留模型、基线与历史证据。

Codex应实际显示文件读取与命令运行，而不只是给出建议。它应知道当前云端支持离线训练/推理，原生Android运行条件尚未验收。默认工作区权限可用于日常编辑和测试。

## 官方说明

- Codex扩展：https://learn.chatgpt.com/docs/codex/ide
- 扩展ID与设置：https://learn.chatgpt.com/docs/ide/settings
- 登录：https://learn.chatgpt.com/docs/auth
- AGENTS.md：https://learn.chatgpt.com/docs/agent-configuration/agents-md
- VS Code安装扩展命令：https://code.visualstudio.com/docs/configure/command-line
- Codespaces版本管理：https://docs.github.com/en/codespaces/developing-in-a-codespace/using-source-control-in-your-codespace
