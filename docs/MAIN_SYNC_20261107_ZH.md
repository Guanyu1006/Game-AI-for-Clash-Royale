# 2026-11-07 主分支同步说明

本次将 `dev/main-start-20261020` 已有的全部 Git 更改同步到 `main`，共同开发基线统一为 `main`。原主分支与开发分支的提交历史均保留，没有重写历史。源码同步与实验验收分别记录；完整原版录像、连续拖牌人机和棋力提升仍未验收。

## 纳入的提交与变更

同步前远端主分支为 `2d4b0a6002be4bcb02c092290a03a08b4791517a`，开发分支为 `7afcd660b4cdb7e67c2486e8507adce12a42dae1`。主分支是开发分支的祖先，纳入以下四次已有提交，共涉及 150 个文件（新增 77,578 行、删除 202 行），另补充本次同步说明和入口文档。

| 已有提交 | 原提交标题 |
|---|---|
| `f2cdd13` | update |
| `a071245` | update |
| `9f1768a` | Save native replay progress and shared development handoff |
| `7afcd66` | update |

主要功能按代码职责说明如下：

- **统一科研工作台。** `workspace.py research` 启动私有 8770 页面；`research/system.json` 集中登记配置、数据和模型，`research/runs/` 保存独立实验。支持当前 GRU C 的离线训练、任务状态、保存重载和新进程 validation，相关实现位于 `royale_phase3/research.py`、`workbench_config.py` 与 `tools/cloud/research_*.py`。
- **固定版本原生运行链。** 增加软件 Android 的准备、恢复、资源和版本身份核验、原生连接、闭环采集与模型执行反馈，区分请求动作和原生实际消费。阶段三继续复用阶段二的固定版本协议与后端。
- **公开回放与媒体。** 支持公开对局 JSON 导入、播放、暂停、单步、保存画面与只读回看；补充 Windows Android 镜像、原生画面采集和媒体工具。保存媒体播放与新原生对局分别验收。
- **研究交接与检查。** 补充中文教程、可缩放代码地图、模型接入说明、依赖清单、审计摘要及云端回归测试。保留 `royale_ai`／`royale_lab` 近似基线、阶段二平台、阶段三研究代码各自的验收口径。

本次未改写 `baseline/`、`shared_assets/`、历史 Windows `requirements.lock` 或 `run_play.ps1`、`run_platform.ps1`、`run.ps1`、`phase2.ps1` 等入口。

## 本次重新验证

使用仓库内 `.venv-cloud/bin/python`，完整命令、返回码与结果摘要见[同步验证记录](audits/main_sync_20261107.json)。原始日志保留在本机 `outputs/cloud/main-sync-20261107T100809667889Z/`，该目录不进入 Git。

验证包含统一 `smoke --epochs 2`：环境与资产检查、云端／阶段三测试、数据检查、NumPy BC 与 GRU 各两轮实际训练、保存后加载、独立 validation、保存观测回放和近似沙盒检查；另运行默认保存模型的 `infer` 与 `infer-gru`。本次不启动新的原生对局，不用 test 调参，不据此宣称策略提升。

结果：smoke 的 11 个步骤全部通过；测试运行 473 项，失败 0、错误 0、跳过 2。默认 NumPy 模型的 3 个推理样例全部匹配保存解码，默认 GRU 完成 8 个样例推理；两者原生 RPC 均为 0。新训练与验证结果保存在本次独立目录，没有覆盖已有模型。

全量差异的 `git diff --check` 返回码为 2，未通过空白格式检查：`royale_phase3/data_contract.py`、`royale_phase3/research.py`、`tests_cloud/test_native_media.py`、`tools/cloud/native_media.py` 各有一处文件末尾空行。这些空行由既有开发分支提交带入。本次保留这些源文件和已有源码指纹，在验证记录中如实记录；新增说明单独检查无空白格式错误。

## 已保存的原生证据与未完成项

以下为同步前已保存的实测证据，本次仅核对引用和哈希，不作为新原生实验：

- [`research/STATUS.json`](../research/STATUS.json) 记录此前两轮 GRU C 离线训练、新进程 352 行 validation、零原生 RPC。
- 最新无画面 CLI 模型评估两局均自然终局，959 次决策、90 次确认出牌，一胜一负；每局约 180 秒、85 秒，不含冷启动。完整证明位于原 Codespace 的 `research/runs/headless-result-20261107t075733z/gameplay-proof.json`。这些执行复用历史模型和配置，保存战场采样与历史结果相同，不增加独立训练对局多样性，也不证明棋力提升。
- 网页提交到真实原生新局的完整流程尚未联合重验；完整原版终局录像和 Windows 连续鼠标拖牌人机尚未验收。现有约 273 秒真实视频仅覆盖初始化阶段，状态重绘仍只有数据审计与设计。

`research/STATUS.json` 以及各日期审计中的旧提交号、`committed_or_pushed=false`、失败状态和验证数量，均为当时的实验快照；本次保留原记录。本次 Git 同步状态以主分支提交历史为准，重新验证以本次验证记录为准。

## 运行与协作

已有 Codespace 在仓库根目录执行：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py doctor
.venv-cloud/bin/python tools/cloud/workspace.py test
.venv-cloud/bin/python tools/cloud/workspace.py research
```

在 Ports 打开私有 `8770`，访问 `/research`。新工作区按[Codespaces 指南](GITHUB_CODESPACES_ZH.md)准备环境，再读[当前科研系统指南](../research/README.md)核对本机资产。当前仅支持源码仓库运行，普通 wheel 不是含数据和运行时的完整发行包。

同步只包括 Git 管理的源码、配置、测试、文档及既有经审查小资产。虚拟环境、缓存、数据、模型、VM、APK／游戏资源、媒体及完整原始实验结果仍保留本机；全新克隆不会自动获得它们，不要强行加入 Git 或跨机复制进程绑定的 `connection.json`。

后续各自开发前，先保存自己的工作，再更新主分支：

```bash
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c dev/自己的任务名
```

分支有分叉时先核对差异；通过小提交和 PR 协作。一个原生实例继续只交给一个控制者。
