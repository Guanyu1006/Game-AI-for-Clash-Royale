# Codespaces 开发训练与回放操作指南

这份指南从仓库根目录开始，依次完成环境检查、测试、训练、重新加载、验证、回放和模拟器操作。当前开发分支是 `dev/main-start-20261020`。所有命令明确使用 `.venv-cloud/bin/python`，无需依赖终端是否已经激活虚拟环境。

本次配置完成了 Codespaces 的离线开发链路。保存观测回放使用阶段三的历史派生数据；可交互战场使用 `royale_ai` 的 Python 近似模拟器。Android 原生游戏和第一阶段 C++ 扩展各有依赖，本指南不会将它们冒充为云端已经连接的引擎。

新增的实际资源配置、严格原生连接、采集／训练／重新加载评估编排见 [原生闭环操作指南](NATIVE_LOOP_ZH.md)。原生运行与当前离线工具各自验收。

## 1 检查目录和分支

打开 VS Code 的 Terminal，在仓库根目录执行：

```bash
pwd
git branch --show-current
git status --short
```

应看到目录 `/workspaces/Game-AI-for-Clash-Royale` 和分支 `dev/main-start-20261020`。工作区可以保留待提交的开发改动；不要用 reset 或重新导入 ZIP 清理它们。刷新主分支信息可以运行 `git fetch origin`，再用 `git rev-list --left-right --count HEAD...origin/main` 检查差异。本次修复开始时两者均为 `2d4b0a6`。

## 2 建立或检查环境

当前 Codespace 已安装依赖。以后新建 Codespace、重建容器或修复依赖时执行：

```bash
bash tools/cloud/bootstrap.sh --ml --research
```

脚本创建或复用仓库内 `.venv-cloud/`，安装固定版本 NumPy、CPU Torch、PyArrow 和 Gymnasium，核验并恢复便携资产，然后运行检查。`--ml` 与 `--research` 可组合；只运行 NumPy 核心时可省略二者。脚本保留已有不同内容的恢复目标，不覆盖原模型或历史结果，不使用 Windows 的 `requirements.lock`。

安装后执行：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py doctor
.venv-cloud/bin/python -m pip check
```

`doctor` 应显示 NumPy、Torch、PyArrow、Gymnasium 可用，资产状态为 `pass`。没有 CUDA 时使用 CPU。`native_runtime_verified=false` 是原生运行范围的结果，不影响本指南的离线开发功能。

VS Code 如果选错解释器，执行 **Python: Select Interpreter** 并选择 `.venv-cloud/bin/python`。仓库 Cloud Tasks 也已使用这一显式路径。

## 3 先运行一次完整小规模验证

```bash
.venv-cloud/bin/python tools/cloud/workspace.py smoke --epochs 2
```

这是最直接的完整检查，会依次执行：

1. 环境与资产检查。
2. 便携测试，包含真实 HTTP 交互与逐局隔离回归。
3. 保存数据与原始证据可用性盘点。
4. NumPy 训练两轮、保存 checkpoint、重新加载推理、validation 评估。
5. GRU 训练两轮、保存 checkpoint、重新加载推理、validation 评估。
6. 保存观测回放读取。
7. 30 秒近似模拟器对局与快照保存。

每次自动创建独立的 `outputs/cloud/runs/<时间戳>-development-smoke/`。结束时应显示 `status: passed`、`not_run_steps: []`，并给出 `report_path`、`numpy_checkpoint` 和 `gru_checkpoint`。每一步的命令、退出码、stdout/stderr 和 SHA256 都保存在该目录。失败时保留目录并停止后续步骤，不删除旧结果；修复后重新运行会生成新目录。

这个检查实际执行小规模优化，不反复评价 test，也不进行大规模原生对局。训练损失和 validation 指标用于确认开发链路，不能直接解释为棋力提升。

上述训练、保存样例推理与规则模拟各自通过，不表示训练模型已经接管同一个模拟器。当前网页战场仍由人工／`GreedyPolicy` 控制，快照也尚未接入新训练入口；统一交互学习循环的结构与任务门槛见 [系统结构与闭环差距](SYSTEM_STRUCTURE_AND_LOOP_ZH.md)。

若只想检查代码和保存模型，不想重新训练，可以执行：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py test
.venv-cloud/bin/python tools/phase3.py acceptance --profile research
```

后者输出独立 `acceptance.json`，其中 `offline_passed=true` 只表示本次离线检查通过；原生运行和完整阶段三研究验收分别保留状态。

## 4 分步训练加载和验证

下面使用明确目录，便于第一次操作。目录已经存在时换一个新名字，不要删除旧实验重跑。命令中的 checkpoint 必须与刚才训练的目录对应。

### NumPy 数值模型

```bash
.venv-cloud/bin/python tools/cloud/workspace.py train --epochs 2 --output outputs/cloud/runs/my-first-numpy
.venv-cloud/bin/python tools/cloud/workspace.py infer --checkpoint outputs/cloud/runs/my-first-numpy/checkpoint.json
.venv-cloud/bin/python tools/cloud/workspace.py evaluate --checkpoint outputs/cloud/runs/my-first-numpy/checkpoint.json --split validation
```

查看 `my-first-numpy/report.json`、`progress.json`、`checkpoint.json` 和 `weights.npz`。报告中的 `checkpoint_roundtrip_exact=true` 表示保存和重新读取后的参数完全一致；validation 单独生成新报告。NumPy 云端 checkpoint 使用保存的公开特征和 recorded mask，不伪装成重新通过原生合法性验收的模型。

### GRU 序列模型

```bash
.venv-cloud/bin/python tools/cloud/workspace.py train-gru --epochs 2 --output outputs/cloud/runs/my-first-gru
.venv-cloud/bin/python tools/cloud/workspace.py infer-gru --checkpoint outputs/cloud/runs/my-first-gru/epoch-0002.json
.venv-cloud/bin/python tools/cloud/workspace.py evaluate-gru --checkpoint outputs/cloud/runs/my-first-gru/epoch-0002.json --split validation
```

查看 `configuration.json`、`epoch-0002.json`、对应 NPZ、`latest.json` 和 `report.json`。GRU 的模型、优化器、RNG 和最佳 validation 权重分开保存；新进程加载时核对权重哈希、版本和观测 profile。

如果要练习中断恢复，使用另一个新目录，先在预定两轮中的第一轮后停止：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py train-gru --epochs 2 --stop-after-epochs 1 --output outputs/cloud/runs/my-resume-gru
.venv-cloud/bin/python tools/cloud/workspace.py train-gru --epochs 2 --resume --output outputs/cloud/runs/my-resume-gru
```

恢复时保持原计划的 epochs、seed、数据和配置一致；不能通过改变总轮数冒充同一个可恢复实验。`completed=false` 是可恢复的中间状态，第二条完成后应为 `completed=true`、`resumed=true`。

想看最近结果：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py runs
```

当前 pilot 是 5 个保存整局组、1,618 行和 111 个保存确认出牌标签。train/validation/test 按整局为 3/1/1 组，双方视角放在同组。fair 输入没有已认证的非塔单位信息，不能把全部零实体块解释为战场为空。

## 5 打开研究工作台

```bash
.venv-cloud/bin/python tools/cloud/workspace.py serve --host 127.0.0.1 --port 8770
```

保持这个终端运行。在 VS Code 底部 **Ports / 端口** 面板找到 8770，点击浏览器图标，保持端口可见性为 **Private**。若没有自动出现，手动添加 8770。终端里显示的 `127.0.0.1` 属于远程容器，从本机浏览器访问时使用 Ports 生成的转发地址。

首页可以运行保存 NumPy / GRU 模型、查看最近结果；也可以粘贴自己训练的 checkpoint 路径，再点击对应加载按钮。点击 **打开保存观测回放与可交互模拟器** 进入同一服务的 `/research` 页面。

### 保存观测回放

1. 在左侧选择对局和 owner 视角，点击“载入并重置”。
2. 拖动帧滑条，或点击上一帧、下一帧、回到首帧。
3. 检查当前 tick、圣水、手牌、塔 HP 与已发生下牌历史。
4. 打开“当前帧标签与后续转移”，核对请求坐标、计划 tick 和保存执行 tick。

标签与当前 actor 输入分开展示。终止标记描述下一转移，不伪造未提供的原生终局画面。非塔单位、最终实际部署点与原生 PNG 缺失时保持 unknown；塔位图只是结构化示意。倒退和切换对局不会保留后一帧历史。

只在终端查看某帧也可以：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py replay --episode-id native-330000 --owner 0 --index 0
```

### 可交互近似模拟器

1. 在右侧选择随机种子和时限，点击“新建 / 重置”。
2. 选择手牌后点击战场下牌。你控制下方蓝方，普通部队要求 `y>=17`；法术可选择整个场地。
3. 用“等待并推进”或“双方规则策略推进”观察单位移动、交战和塔血变化。
4. 查看最近动作反馈中的 affordable、legal、consumed，区分圣水不足、区域非法和实际执行。
5. 点击“保存本局快照”，复制返回的 `run_id`。在“回看当前 / 已保存快照”中输入它，读取首帧，再拖动滑条。

保存内容在 `outputs/cloud/runs/<时间戳>-sandbox-*/`。停止并重启工作台后，粘贴同一个 `run_id` 仍可读取；读取时核验文件 SHA256。回看快照不会回滚正在进行的对局，“返回活动对局”可恢复实时显示。

这里复用的是现有 `royale_ai.simulator.Arena`，坐标为 18×32 tile。保存的原生动作使用 18000×32000 单位，两个接口不混用。模拟器的训练、战果与第二阶段原生机制验收分开。

无人交互的快速模拟：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py simulate --seconds 30 --seed 19
```

服务进程内所有浏览器共享回放和模拟器会话。两人协作时先约定操作人；需要隔离可以在另一个终端使用 `serve --port 8771`，再转发该端口。

## 6 修改代码与确认结果

| 修改内容 | 入口 | 验证方法 |
|---|---|---|
| 云端命令和 HTTP | `tools/cloud/workspace.py` | `workspace.py test`；重点看 `tests_cloud/test_workbench_http.py` |
| 数值训练与模型文件 | `tools/cloud/train_cache.py` | `train` → 新 checkpoint `infer` → `evaluate`；参数回读与篡改拒绝回归 |
| 阶段三序列模型 | `royale_phase3/learning.py`、`data_contract.py` | `train-gru` → `infer-gru` → `evaluate-gru`；整局切分与恢复一致性 |
| 保存观测解码与回放 | `tools/cloud/replay.py` | seek 前后历史隔离、切换 owner、未知值和标签边界 |
| 近似模拟器适配 | `tools/cloud/simulator.py`，底层 `royale_ai/` | 实际扣费、非法落点、移动、终局、reset、保存后跨会话回放 |
| 工作台页面 | `tools/cloud/workbench.html`、`research.html` | HTTP 回归后，通过 Ports 在浏览器实操；检查页面错误提示 |

小改动后先跑相关测试；共享接口或训练流程改动后运行 `workspace.py smoke --epochs 2`。所有新实验用新输出目录，原始数据、模型和审计摘要保持原样。

当前环境还完成了真实 Chromium 页面检查：保存模型推理、切视角与倒退回放、点击下牌、终局重置、保存后跨服务重启读取均通过，页面 JavaScript 错误数为 0。中文字体和浏览器所需动态库仅放在仓库缓存中，没有安装到系统。复查当前环境的浏览器流程可运行：

```bash
.venv-cloud/bin/python tools/cloud/browser_check.py
```

这是可选开发检查；新建环境若要运行它，需单独安装 `requirements-codespaces-browser.txt` 和 Chromium，并提供相应动态库与中文字体。通过 Ports 使用自己电脑上的浏览器不需要在 Codespace 再配置自动化浏览器。截图与五项检查见 [浏览器验证报告](../outputs/cloud/runs/20261020T101021.969322Z-browser-check/report.json)。

阶段三 CLI 现使用当前解释器，子命令帮助可正常转发，例如：

```bash
.venv-cloud/bin/python tools/phase3.py train --help
.venv-cloud/bin/python tools/phase3.py evaluate --help
.venv-cloud/bin/python tools/phase3.py acceptance --help
```

`baseline-audit` 的 Windows 解释器硬编码已修复，但完整历史 Parquet / 原始证据不齐时仍不能完成基线重审。`test --group extended` 专门保留依赖历史原生夹具、CSV 与外部样例的检查，缺少这些条件时会失败；默认便携组的通过不替代它。

## 7 常见问题与恢复

| 现象 | 处理 |
|---|---|
| `No module named torch` 或 PyArrow 缺失 | 使用仓库显式解释器，重跑 `bash tools/cloud/bootstrap.sh --ml --research`；不要修改 Windows 锁文件 |
| 输出目录已存在 | 换新目录名；GRU 仅在配置一致时用 `--resume`，保留旧结果 |
| 端口已占用 | 停止自己此前的服务，或改用 `--port 8771` 并转发新端口 |
| 页面显示 unknown 单位或没有原生 PNG | 保存 fair 数据没有这些字段；这是观测范围，不能填假值 |
| `data-check` 显示原始引用缺失或哈希不同 | 保存派生数据仍可按自身哈希检查；来源重导出另行修复，不批量更新历史哈希 |
| Linux 上执行原生 `play` 显示 unsupported | 使用当前 `/research` 近似沙盒；真实 Android 与游戏资源是独立的部署条件 |
| 工作台已关闭 | 重跑 `serve`，按原 `run_id` 查看保存模拟器快照 |

结束工作时停止自己的服务（终端 `Ctrl+C`），检查 `git status --short` 与 `git diff --check`。按你们约定审阅、提交和推送开发分支；不要提交环境、缓存、凭据或未审查的大结果。Codespace 自身是否继续运行，在 GitHub Codespaces 页面单独确认。

本次运行的完整小规模结果见 [开发链路报告](../outputs/cloud/runs/20261020T100921.745225Z-development-smoke/report.json)，离线验收见 [acceptance.json](../outputs/cloud/runs/20261020T100134.648264Z-phase3-acceptance/acceptance.json)，真实中断恢复及权重逐项比较见 [恢复验证报告](../outputs/cloud/runs/20261020T100634.916944Z-resume-verification/report.json)。可随代码保存的结果摘要见 [remote_development_20261020.json](audits/remote_development_20261020.json)。早先问题与来源范围保留在 [平台审计](PLATFORM_AUDIT_20261020_ZH.md)，它记录的是修复前基线。
