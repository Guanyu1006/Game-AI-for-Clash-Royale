<!-- royale-codex-managed:start -->
# Royale Lab：Codex 开发约定

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
<!-- royale-codex-managed:end -->
