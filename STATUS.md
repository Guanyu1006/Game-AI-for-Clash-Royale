# 执行状态

2026-10-14：本轮本地研究平台建设与验收完成。持续目标以规格允许的社区研究后端为交付范围；不将官方全规则、客户端兼容或真实设备像素闭环列为完成。全部项目环境/缓存/源码/论文/模型/产物保存在当前目录。

## 已运行与验证

- 现有12卡MVP代码与baseline/mvp_v0_1保留，26项回归通过。
- 主引擎ClashRoyaleAi固定commit2f172925a15b；173注册=132非进化+41进化。最终pyd SHA256 6160b968f9dfde6ab9b047627596c5d50383b0af8728ed5036fc49239edccd53。740原生cases：739通过、1已知预期路径失败；8413断言8412通过。证据outputs/native_engine_numeric_tests.xml、data/platform/interaction_tests.json。
- 独立Java crforge编译/ZMQ真实终局/974tests通过；gym安装导入运行尝试暴露stub，KataCR视觉依赖障碍保存。候选详见docs/SERVER_SELECTION.md及docs/audits。
- 经济1/28每tick、120s2x/240s3x；五法术塔伤与独立Java参考对齐，Poison168vs160差8仍保留。塔等级/HP混合，不声称官方一致。
- 数值外置173行中171可调、25类适用字段，34原生配置检查、独立非法参数/原子性/进程锁审查通过。平台fresh-process JSON实际下牌费用2/HP2200/攻击303/火球塔伤123；旧checkpoint自定义数值下拒绝。证据outputs/platform/numeric_integration.json。
- 最终默认模块与旧v2：100局5314动作、每变体169994完整状态点零差异。无效字段收紧改变schema指纹后才迁移元数据，过程outputs/platform/numeric_schema_migration.json；保留旧配置与模块。
- 最终平台21tests及干净.venv-repro的21tests全部通过。主环境还跑26MVPtests；干净环境准确依赖安装/editable/nativeinfo通过，旧独立pipcheck证据保留。日志outputs/platform-tests-delivery.log、repro-tests-delivery.log、bootstrap-repro-final.log。
- 服务实际从start_demo.ps1启动，HTTP验证下牌扣费、手牌轮转、去重、保存/恢复/分叉、导出/加载、终局，搜索vs规则自动71公开出牌0拒绝；浏览器手动短局到终局及最终AI局势截图已保存。证据outputs/platform/http_smoke.json、ui_verified.png、outputs/sessions。
- BC1344样本/12整局/20epochs；PPO4096steps/27局，实际checkpoint加载完整对局。BC验证总体88.07%、WAIT100%、PLAY29.09%；PPO argmax几乎全等待的问题和采样修正有独立诊断，不隐藏失败。
- 7策略各100局，共700配对冻结局，0崩溃/超时/拒绝，4卡组、换边、多个种子、3类对手。search63胜、greedy50、BC48、PPO采样37、random34、无响应search60、PPO argmax0。回放/决策/失败局势/图表齐全；全部仅说明本近似引擎。
- coverage：173卡/51机制/740原生cases清单；官方轨迹一致验证0项。不能以注册或部署数量推出官方交互覆盖。
- 33论文记录，32PDF/正文，21项目记录，20固定源码包（3完整+17明确范围），12精读，BibTeX与方法比较；离线SHA校验通过。R2D2全文403及2.02GB专家集部分下载明示。
- 真实30MB ONNX/97类别的CPU推理与2张真实资料图检测/标框已运行；结果0框与1疑似误检，无配套标注且裁剪域不符，不声称准确度或设备闭环。模型metadata AGPL-3.0和类别不一致记录。
- 中文START_HERE、MODELING、ARCHITECTURE、ENGINE_GUIDE、BALANCE_CONFIG、STRATEGY_MAP、TUTORIAL、LEARNING_PATH、EXPERIMENTS、ROADMAP及资料导航已完成。

## 未达与继续方向

1. 官方全部机制/混合等级统一/客户端私服兼容未达；Golem双建筑夹角不动为已知预期失败；独立校准轨迹不足。
2. 世界模型重建不完整（衍生单位/弹道/状态/队列），候选与时序有限；搜索软预算，非全局最优。
3. 68个其他数值成员、36个效果类、子单位/塔/等级等尚未全部外置；详见data/platform/unsupported_numeric_parameters.json。
4. 真机HUD/阵营/HP/OCR/追踪/执行未闭合，无设备与官方真值轨迹。C输入路线仅真实接口和初步检测证据，A/B本地闭环已完成。
5. PPO critic未充分编码手牌/下一张；续训不恢复全部环境/RNG/中途rollout。优先世界模型/动作时机、独立校准、层次化网络/策略池，再推进视觉。

## 可直接继续的命令

在项目目录：`./start_demo.ps1`；`./run.ps1 -Action tests`；`./run.ps1 -Action research`；`./run.ps1 -Action evaluate`。具体训练、复现失败、JSON配置、重建原生步骤见docs/TUTORIAL.md、BALANCE_CONFIG.md、ENGINE_GUIDE.md。重训会写默认models/platform，先备份模型/曲线；变更评估配置必须指定新--output。

验收汇总：outputs/platform/acceptance.json；完整范围/结果：DELIVERY_REPORT.md。历史v1经济成果位于outputs/archive/pre-economy-v1，不能与本次v2结果混合。最新schema/编码故障的失败与修复证据也保留，不覆盖为“从未失败”。
