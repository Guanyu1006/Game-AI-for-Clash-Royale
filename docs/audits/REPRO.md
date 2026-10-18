# 干净 Python 环境复现验收

日期：2026-10-14。已在项目内新建 `.venv-repro`（Python 3.14.5），安装主环境当时的精确依赖快照，再 editable 安装本项目 0.2.0。所有 pip cache、TEMP/TMP、安装日志、wheel 临时目录在项目目录内。系统 Python 仅作为只读基础解释器；使用本项目已经从固定源码编译的 native `.pyd`，本项不是在空机器重新安装 MSVC 的验证。

结果：pip check、`royale_lab info`、learned vs ppo 完整 demo 和平台测试全部成功。短 demo 在 600 tick 达到终局，实际记录 11 次接受出牌。初次 19 个方法通过，加入 PPO 独立采样 RNG 检查后重新执行，共 20 个方法全部通过；checkpoint 子测试分别加载 learned/ppo 并执行终局，不能只用“文件存在”代替加载运行。

| 验收 | 证据 |
|---|---|
| 精确依赖安装 | `outputs/audit_candidates/requirements-repro.txt`、`repro_install.log` |
| 项目安装 | `outputs/audit_candidates/repro_project_install.log` |
| Python/依赖版本 | `outputs/audit_candidates/repro_environment.json`、`repro_freeze.txt` |
| 依赖一致性 | `outputs/audit_candidates/repro/pip_check.stdout.log` |
| 新版原生引擎 info | `outputs/audit_candidates/repro/info.stdout.log` |
| 学习模型闭环 demo | `outputs/audit_candidates/repro/learned_vs_ppo.stdout.log` |
| 20 个测试 | `outputs/audit_candidates/repro/tests_platform.stderr.log` |
| 命令、返回码、耗时、checkpoint SHA256 | `outputs/audit_candidates/repro/manifest.json` |

安装命令已执行：

```powershell
$env:PIP_CACHE_DIR = Join-Path (Get-Location) '.cache\pip'
$env:TEMP = Join-Path (Get-Location) '.tmp'
$env:TMP = $env:TEMP
.venv\Scripts\python.exe -m venv .venv-repro
.venv-repro\Scripts\python.exe -m pip install -r outputs\audit_candidates\requirements-repro.txt
.venv-repro\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
.venv\Scripts\python.exe tools\audit_repro_verify.py --with-checkpoints
```

验收脚本实际使用 `.venv-repro\Scripts\python.exe` 执行每一项，并保存 stdout/stderr 和真实退出码。直接复验也可运行：

```powershell
.venv-repro\Scripts\python.exe -m royale_lab info
.venv-repro\Scripts\python.exe -m royale_lab demo --policies learned ppo --max-ticks 600
.venv-repro\Scripts\python.exe -m unittest discover -s tests_platform -v
```

demo 使用 600 tick 短对局上限，只验证模型加载、观测到执行、反馈记录和终局流程；完整标准时长的评估以主平台实验报告为准。最早一次测试发生在旧版本模型被归档、新模型尚未生成时，只有两个 checkpoint 子测试因文件缺失失败。此日志保留为 `repro_tests_platform_before_checkpoints.log`；新 calibrated-v2 模型生成后，全部重新通过，没有隐藏第一次失败。

实际验证的 learned.pt 与 ppo.pt 各 156421 字节，校验值记录于 manifest。后续重新训练会改变 checkpoint SHA；需要重新执行验收并记录引擎版本，不能将本次结果移用到新模型。
