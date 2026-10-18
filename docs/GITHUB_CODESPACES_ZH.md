# 上传 GitHub、进入 Codespaces、和朋友一起开发

> **已经导入且测试通过的 Codespace：** 继续阅读 [Codex 配置与最后上传步骤](CODEX_CODESPACES_ZH.md)，无需重复导入旧 ZIP。下文保留首次迁移教程。

这份教程对应项目里的实际配置和命令。本地 Windows 原生启动方式保留；Codespaces 新入口提供测试、真实 CPU 数值训练、已保存模型推理和研究页面。原生 Android 对局没有经过 Codespaces 托管环境验收，不能把工作台页面当成新的原生对战。

## 1. 搬哪些东西

不要上传整个本地项目文件夹。使用生成的 `RoyaleLab_GitHub_20261018.zip`：

- 直接开发：四套 `royale_*` 源码、测试、工具、中文文档、原 MVP 源码、各阶段规格、本地 PowerShell 启动器。
- 统一环境：`.devcontainer/`、两份 `requirements-codespaces*.txt`、`.vscode/tasks.json`、Linux CI、云端入口。
- 可直接复现：`shared_assets/` 内的已审查数值缓存、模型、小型真实原生自采观测、版本清单和历史摘要。启动时按 SHA256 校验，恢复到被 Git 忽略的 `data/`、`models/`、`outputs/` 等原路径。
- 留在本地：虚拟环境、缓存、VM 磁盘、APK/libg/游戏资源、密钥、完整原始数据和大型媒体。它们没有被删除，清单见 `MIGRATION_EXCLUSIONS.json`。

这不是“所有大文件都已搬入 Git”。训练缓存与模型只提供有代表性的可运行子集，剩余历史保持原目录。应先用朋友的私有仓库；原生素材和来源数据的许可不能由项目源码许可替代。

## 2. 将上传包导入朋友的现有仓库

你已经连接朋友的 Codespace。先确认 VS Code 左下角是该 Codespace，终端位于仓库根目录。

在本地找到上传包，把 **ZIP 文件**拖到连接 Codespace 的 VS Code 文件区，等待上传完成。不要把本地 `.venv` 或整个文件夹拖过去。

先检查现有改动：

```bash
git status --short
```

如果有你或朋友尚未保存的改动，先提交或妥善保存，再继续。接着建立独立迁移分支，并在仓库内解压：

```bash
git switch -c setup/codespaces-20261018
mkdir -p .tmp/migration
python -m zipfile -e RoyaleLab_GitHub_20261018.zip .tmp/migration
```

先查看导入计划：

```bash
python .tmp/migration/RoyaleLab_GitHub/tools/cloud/import_repository.py --source .tmp/migration/RoyaleLab_GitHub --target .
```

已有的 README 等同名文件可能显示为 conflict。确认要采用迁移包中的版本后，再执行：

```bash
python .tmp/migration/RoyaleLab_GitHub/tools/cloud/import_repository.py --source .tmp/migration/RoyaleLab_GitHub --target . --apply --replace
```

导入器会校验包内文件哈希，拒绝危险路径，不改 `.git`；不同内容的旧文件先备份到 `.tmp/migration-backups/`。已有 Git 历史仍保留，源文件在迁移分支中可审查。

提交前查看列表与差异：

```bash
git status --short
git diff --stat
git add --all
git diff --cached --stat
git commit -m "Set up portable Codespaces development"
git push -u origin setup/codespaces-20261018
```

ZIP、`.tmp/`、环境、缓存和新生成结果都被忽略。不要使用 `git add -f` 强行加入它们。推送后在 GitHub 建立 pull request，请朋友审阅并合并；也可以先共同在此分支验证。

如果 push 返回403，你需要仓库的 **Write** 权限；能够打开仓库或运行 Codespace，并不自动包含推送权限。请朋友在仓库设置中确认 collaborator 权限，不要共享密码或 token。

## 3. 重建云端环境

导入完成后，在 VS Code 按 `Ctrl+Shift+P`，执行 **Codespaces: Rebuild Container**（也可能显示 Dev Containers: Rebuild Container）。

配置使用 Linux/Python3.12。初始化脚本会在仓库内创建 `.venv-cloud/`，安装 NumPy2.2.6 和官方 CPU Torch2.6.0，校验/恢复资产，然后运行检查。首次需要下载依赖，终端完成前不要启动第二次安装。历史 Windows `requirements.lock` 不改写，也不用于这次 Linux 安装。

完成后：

```bash
source .venv-cloud/bin/activate
python tools/cloud/workspace.py doctor
python tools/cloud/workspace.py test
```

若自动安装中断，可恢复运行：

```bash
bash tools/cloud/bootstrap.sh --ml
```

只需要轻量 NumPy 路线时，运行 `bash tools/cloud/bootstrap.sh`；GRU训练需 `--ml`。这只影响当前 Codespace，不修改本地 Windows 虚拟环境。

## 4. 先跑一次真实训练和推理

最小 NumPy BC：

```bash
python tools/cloud/workspace.py infer
python tools/cloud/workspace.py train --epochs 2
```

这里实际执行优化并保存新权重和曲线；输入是旧平台合格导出中的公开数值缓存，train/validation 保留原整局分组。只有52个出牌标签，样本总行数不代表强策略；不会在这里反复评分test或重建全部原生证明。

第三阶段 GRU：

```bash
python tools/cloud/workspace.py infer-gru
python tools/cloud/workspace.py train-gru --epochs 2
```

输入是已保存的5局原生规则teacher自采 pilot，双方视角按整局分组；它不是专家数据，也没有独立官方时序真值。默认公平输入仍排除可见性未知的非塔实体。模型跑通、预测指标和棋力是三种不同结论。

每次训练生成独立的 `outputs/cloud/runs/<时间戳>/`，不覆盖原模型。看 `report.json`、`progress.json` 或阶段三 checkpoint/曲线。想重复使用固定输出目录时，先换一个新名字；不要删旧结果来冒充首次实验。

也可以运行保留的第一阶段教学模拟器：

```bash
python -m royale_ai simulate --policy greedy --opponent greedy --seconds 30 --log outputs/cloud/mvp-demo.jsonl --html outputs/cloud/mvp-demo.html
```

这是近似沙盒，不是固定原生后端；其结果不能算作原生对局验收。

## 5. 打开研究页面

```bash
python tools/cloud/workspace.py serve
```

在 VS Code 底部 **Ports/端口** 中找到 `8770`，点浏览器图标。保持端口可见性为 **Private**。页面可以查看数据数量、实际运行保存的 NumPy/GRU 推理，并显示训练命令。页面明确标注原生对局未连接。

若端口占用，先停止此前的页面进程，或用 `--port 8771` 并在 Ports 中添加该端口。结束服务按 `Ctrl+C`。

快捷入口：`Ctrl+Shift+P` → `Tasks: Run Task` → `Cloud: doctor / tests / train NumPy BC / research page`。如果解释器没有自动选中，执行 `Python: Select Interpreter`，选 `.venv-cloud/bin/python`。

## 6. 邀请朋友一起操作

由拥有当前 Codespace 的人，在这个云端窗口启用 **Live Share**，点击 Share，将邀请链接私下发给朋友。朋友用自己的账号加入。双方看到同一套云端文件。

要一起运行命令，在 Live Share 共享终端，并按需要给予读写权限。先约定当前谁执行训练，避免重复任务占满小机器。网页优先从 Live Share 的 Shared Servers/共享服务打开；Codespaces 的私有端口链接不自动给每个收件人访问权限，不要为了方便直接公开项目服务。

实时结对：共用一个 Codespace/Live Share 会话。分别研究：各自开分支或 Codespace，使用 Git 提交/PR 同步；两台 Codespace 不会自动成为同一实例。Live Share 邀请也不自动赋予仓库推送权限。

## 7. 本地原生运行怎么保留

完整本地目录继续使用：

```powershell
Set-Location -LiteralPath 'D:\研究生\Clash Royal'
.\run_play.ps1
```

原 `run_platform.ps1`、`run.ps1`、`phase2.ps1` 等入口保留。不要在原本地项目执行云端初始化来重装它的现有环境。云端上传包没有VM、APK和游戏资源，无法仅凭 ZIP 启动原生画面。

云端可做一次只读能力检查：

```bash
python tools/cloud/workspace.py native-check
```

它检查系统架构、Binder节点和Docker可访问性，不安装内核模块、不创建特权容器、不连原生RPC。即使设备存在，也不会写成“原生已通过”。下一阶段需要在具备权限的Linux云主机上完成固定资源身份、reset/下牌/自然终局/恢复和画面对齐验证。Codespaces宿主权限有限，不能预先承诺它能承担该运行时。

## 8. 免费额度和每天结束

个人GitHub Free当前含120核小时/月（2核约60运行小时）与15GB-month存储。运行时消耗计算额度；停止后已有文件仍消耗存储，长期不用时在提交推送后删除实例。Codespaces默认闲置30分钟可自动停止，不应当作持续采集服务器。Live Share主持人关闭连接后会话结束。

每天结束：保存报告 → 提交/推送代码 → 停止Live Share → 在GitHub Codespaces页面停止实例。不要只关闭网页就假设计算已停止。

## 9. 实测与限制在哪里看

本次包的文件清单、CRC、敏感模式扫描和本地迁移验证放在ZIP旁的 `*.verification.json`。Windows本地检查、Python版本、真实训练和HTTP检查分别记录；没有GitHub托管Codespace的实际运行证据时会明确写 `hosted_codespaces_verified=false`。不能把Linux CI配置存在当成CI已经通过。

包内GitHub Actions提供手动/PR的轻量Linux检查（默认不装Torch，不跑原生）。进入仓库 Actions 运行 Portable CPU checks 后，才有你们账号里的托管Linux结果。把失败日志保存下来继续修，不把未运行状态写成passed。

官方步骤：
- [Dev container配置](https://docs.github.com/en/codespaces/setting-up-your-project-for-codespaces/adding-a-dev-container-configuration/introduction-to-dev-containers)
- [Codespaces共同协作](https://docs.github.com/en/codespaces/developing-in-a-codespace/working-collaboratively-in-a-codespace)
- [端口转发](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
- [免费额度](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)
- [CPU Torch版本安装](https://pytorch.org/get-started/previous-versions/)
