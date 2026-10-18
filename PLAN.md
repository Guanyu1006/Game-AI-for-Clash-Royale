# 执行计划与验收

1. 环境与候选审计：记录硬件、已有工具、来源固定 commit、实际 battle loop；尝试本机编译/安装三个候选。证据写 outputs/* 和 docs/audits/*。
2. 后端选择与适配：优先构建开源 C++ 引擎，通过统一 BattleBackend 暴露 reset/step/observe/legal/apply/snapshot/restore。必须有真实动作反馈、确定性与信息边界测试。
3. 本地服务与交互 UI：连接同一个引擎，提供人/AI模式、卡组选择、逐帧、快照、恢复、分叉与日志。启动并通过 API 集成/浏览器验证。
4. 策略与训练：随机/规则/预算前瞻/学习策略，短程 RL checkpoint 保存加载实际运行，配对100局及消融。运行命令写实际完成的验收脚本。
5. 覆盖与资料：逐卡/机制明确近似或未验证，关键交互测试；30论文/15项目/10精读并下载、归档、总结。
6. 文档与干净环境验收：bootstrap/启动/测试/训练/评估入口，完整调用链、优化地图、失败和限制。验证后更新 STATUS 和最终交付报告。

阶段失败先修复；新发现改变路线时更新决策证据，不重写历史为成功。

## 实际验收入口

```powershell
.\bootstrap.ps1
.\start_demo.ps1
.\run.ps1 -Action tests
.\.venv\Scripts\python.exe -m royale_lab demo --policies search greedy
.\.venv\Scripts\python.exe -m royale_lab train-bc --episodes 12 --epochs 20
.\.venv\Scripts\python.exe -m royale_lab train-ppo --steps 4096
.\.venv\Scripts\python.exe -m royale_lab evaluate --games 100 --workers 4
.\.venv\Scripts\python.exe -B tools\research_verify.py
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_native.ps1 -Tests
.\.venv\Scripts\python.exe tools\native_smoke.py
.\.venv\Scripts\python.exe tools\native_coverage.py
```

七策略各100局共700局，同seed/deck/side/opponent，包括PPO采样与argmax诊断。旧v1经济成果归档outputs/archive/pre-economy-v1；v2输出outputs/evaluation/main，JSONL按局续跑。干净.venv-repro exactlock安装与21项平台测试已验证。完整验收结果写DELIVERY_REPORT.md；未达项不能改写为完成。

数值配置验收：`python tools/balance_integration.py`；实际HTTP验收（先启动服务）：`python tools/platform_verify.py`；全部交付证据汇总：`python tools/acceptance_manifest.py`。数值schema指纹迁移仅在100局完整状态零差异后执行，过程保存在outputs/platform/numeric_schema_migration.json。
