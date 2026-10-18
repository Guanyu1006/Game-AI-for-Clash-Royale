# 两个人怎样远程协作这个项目

建议先采用 **GitHub 个人私有仓库 + 一个 Codespace + VS Code Live Share**：GitHub 保存共同代码，Codespaces 提供同一套云端开发环境，Live Share 让两个人同时编辑、讨论和调试。原生游戏目前继续在现有 Windows 主机上串行运行，运行结果再由两个人共同查看。

这是一份操作建议。截至 2026-10-18 已核对官方说明；本次没有创建账号、仓库或云服务，没有安装云端环境，也没有实际上传文件。当前项目的 Codespaces 迁移仍待验证。

## 免费额度够做什么

GitHub 个人 Free 账号目前包含每月 **120 核时、15 GB-month 存储**；2 核机器每运行 1 小时消耗 2 核时，因此约对应 60 小时。免费额度按创建者账号结算，同一个 Codespace 的协作者共同使用这台环境。[GitHub Codespaces 计费说明](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)

结束工作后需要停止 Codespace，停止只暂停计算消耗；实例仍存在就继续累计存储。把代码提交推送后，删除不用的实例才能释放其存储占用。没有有效付款方式的个人账号耗尽额度后会被阻止继续使用；如果账号已有付款方式，需要另检查预算设置。[额度、存储与预算规则](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)

这个方案适合写 Python 策略、审阅代码、处理小数据和做 CPU 小实验。它不提供已经验证的本项目原生运行环境，也不应按永久免费 GPU 训练服务器来规划。

## 先把工作分成两条线

| 工作 | 放在哪里 | 两个人怎样合作 |
|---|---|---|
| 策略、训练器、数据契约、教程 | 云端 Codespace | Live Share 共同编辑，或各自分支后合并 |
| 小型已脱敏数据、CPU 测试 | 云端 | 同一解释器运行，提交配置与摘要 |
| Windows / QEMU / WHPX / Android 原生对局 | 当前项目主机 | 指定一个控制者启动；另一人看共享网页和日志 |
| 大模型训练、大量采集 | 后续单独规划 | 先核算资源，不计入当前免费云端承诺 |

现在的原生环境依赖 Windows 虚拟化与项目 VM、Android、探针和资源。它还没有在 Codespaces 中迁移通过，不能把整个目录搬上去就认为可运行。云端统一代码与开发环境，不自动等于原生游戏也已经搬到云端。

## 第一次建立共同工作区

1. 两个人各自使用自己的 GitHub 账号。一人创建**个人名下的私有仓库**，在仓库设置中邀请另一人为 collaborator。不要共用账号或密码。
2. 从项目中整理一个小型、可审查的代码集再上传。优先保留 `royale_phase3/`、必要的第二阶段适配源码、`tests_phase3/`、`phase3/`、许可证和经过审查的依赖配置。
3. 仓库拥有者打开仓库的 Code → Codespaces → 新建 Codespace，先选最小的 2 核配置。
4. 在该 Codespace 内安装或启用 Live Share 扩展，打开 Live Share 面板并选择 Share，把邀请链接私下发给同学。同学通过自己的账号加入。官方支持在 VS Code 浏览器版或桌面版中使用这一方式。[Codespaces 协作说明](https://docs.github.com/en/codespaces/developing-in-a-codespace/working-collaboratively-in-a-codespace)
5. 先运行一个不需要游戏资源的最小检查，确认双方看到同一份代码、同一个解释器和同一份输出：

```bash
python --version
python -m unittest tests_phase3.test_observation -v
```

这是云端候选验证命令，不代表本次已经在 Codespaces 实跑。该观察测试只用 Python 标准库；belief、训练和原生测试另有数据、依赖和资源要求。

6. 接着固定 Python 与依赖版本，做成仓库的 dev container 配置，并实际重建验证。保留安装日志和首个 smoke 报告。不要把本机 `.venv` 复制到 Linux；也不要默认根目录 `pyproject.toml` 能替代第二、三阶段全部运行依赖。

## 什么文件应保留在本地

不要直接上传整个 `D:\研究生\Clash Royal`，也不要直接执行未检查的 `git add .`。当前目录包含大量运行环境和证据，且现有 `.gitignore` 对 `.venv-phase2-*` 等目录未必已经完整覆盖。

以下内容先留在原主机：

- `.venv*`、`.cache/`、`.tmp/`、下载缓存、模型缓存；云端应按锁定配置重新安装。
- VM 磁盘、APK、游戏资源包、运行时镜像及 `third_party` 中大体积二进制；源码许可也应分别检查。
- SSH 私钥、token、`.env`、登录信息与机器专属连接配置。
- 完整截图、视频、原始对局与大型 checkpoint；先共享需要的小样本、manifest、hash 和指标摘要。

首次上传前检查 `git status --short` 和实际待提交文件列表，确认仓库内容是计划共享的源码。GitHub 私有仓库仍是外部存储；这里没有自动完成上传授权或外传操作。

## 两个人一起观察原生 AI 演示

负责原生运行的人在 Windows 项目目录启动：

```powershell
.\run_play.ps1
```

等待页面可用后，在**这台 Windows 主机的 VS Code** 中开启 Live Share，再通过 Shared Servers / Share Server 共享端口 `8770`。同学从该会话的 Shared Servers 打开映射地址；若本机端口冲突，使用 Live Share 显示的实际地址。官方说明支持把指定的本地服务端口映射给会话成员。[Live Share 共享服务](https://learn.microsoft.com/en-us/visualstudio/liveshare/use/share-server-visual-studio-code)

云端 Codespace 的 Live Share 会话不会自动把 Windows 电脑上的 `8770` 带进去。进行原生演示时，要使用 Windows 宿主发出的会话，或者以后另验证经过认证的网络连接方式。

只共享网页端口；原生 RPC、ADB、SSH 和 VM 管理端口继续由宿主控制。两个人可以看同一场、讨论下一步，也可以轮流在网页下牌。**共享网页没有增加第二个独立游戏实例：不能各自启动一场，也不要同时点击控制。** 网页当前也没有完善的多人权限与操作权仲裁；建议明确轮流由谁操作，先暂停再交接。

## 日常共同开发怎么安排

两人实时结对时，在同一个 Codespace 会话工作即可；两人分别研究不同模块时，各开分支，再通过 pull request 合并。仓库是共同的代码事实来源，Live Share 是当前协作会话，两者职责不同。

每次准备原生实验时，先确定要运行的 commit 和配置。Windows 主机切到该 commit 后，由唯一控制者运行并保存输出；实验中不要一边改共享源码，一边把结果当作同一冻结版本。把“运行过哪个 commit、配置 hash、数据 hash、结果目录、成功/失败”写进实验摘要。大型产物保留本地，只把适合共享的摘要与清单提交到仓库。

每天结束：提交代码 → 推送 → 停止 Live Share → 停止 Codespace。删除实例前再次确认代码已经推送；下一次用同一仓库和固定配置重建环境。原生运行结束后通过平台退出，再按现有教程关闭运行时。

## 现在最适合你们的选择

先用一个 2 核 Codespace 共同写代码、跑小测试，同时保留一台本地原生主机做实际对局。这能解决“环境和代码不统一”的主要问题，也保留当前已验证的游戏运行链路。等协作流程稳定、原生 Linux/云端迁移独立通过后，再考虑把游戏执行也搬上服务器。
