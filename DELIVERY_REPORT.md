# 实际交付与验收报告

本次交付是已运行的本地皇室战争AI研究平台。完整执行闭环已落地；官方客户端兼容、官方全部交互一致和真实设备感知闭环尚未达成。主引擎是固定社区快照而非官方服务器。

## 一键启动

在 `D:\研究生\Clash Royal` 运行 `.\start_demo.ps1`，打开 http://127.0.0.1:18765。详见docs/START_HERE.md。停止服务Ctrl+C，重新运行即可。安装/修复入口bootstrap.ps1，准确依赖requirements.lock；干净项目内.venv-repro已验证。

## 已有实际证据

| 验收项 | 结果 | 证据 |
|---|---|---|
| 本机资源/工具审计 | CPU/内存/GPU/Python/MSVC记录，训练实际CPU | outputs/platform/environment.json |
| 保留MVP | 12卡代码仍可用，26回归通过；原快照保留 | baseline/mvp_v0_1、outputs/platform-tests-delivery.log |
| 候选源码审计与运行 | C++构建/真实对局；Java构建/ZMQ/974tests；gym stub与KataCR运行障碍证据 | docs/SERVER_SELECTION、docs/audits、outputs/audit_candidates/summary_manifest.json |
| 主引擎卡牌/交互 | 173注册条目，740原生cases739pass+1expectedfail；8413断言8412pass+1expected | outputs/native_engine_smoke.json、native_engine_numeric_tests.xml、data/platform/interaction_tests.json |
| 经济/塔伤差分 | 校准1/28/tick、120s2x/240s3x；五法术对齐独立Java，Poison仍差8 | outputs/audit_candidates/backend_differential.json、native_engine_economy_patched.json |
| 服务与真正动作 | 最终模块HTTP手动扣费1、手牌变化、去重、恢复、分叉、加载、终局；自动71出牌 | outputs/platform/http_smoke.json、platform-http-delivery.log |
| 浏览器交互 | 手动短局到终局、保存/恢复/分叉/导出、AI候选与实际单位 | outputs/platform/ui_verified.png、outputs/sessions/04d594f8a850/replay.json |
| 平台测试 | 21方法通过，信息隔离/确定性/合法候选/模型真实对局/JSON数值生效 | outputs/platform-tests-delivery.log |
| 干净复现 | 项目内exactlock+editable+info+模型完整对局+21tests；原独立pipcheck记录保留 | outputs/bootstrap-repro-final.log、repro-tests-delivery.log、audit_candidates/repro/manifest.json |
| 数值数据与隔离 | 173行中171可调，25类适用字段；34原生配置检查与独立审查；实际战斗费用2/HP2200/攻击303/火球塔伤123 | data/platform/balance_v2.json、outputs/native_engine_numeric_stats.json、platform/numeric_integration.json、docs/audits/NUMERIC_CONFIG_REVIEW.md |
| 默认数值兼容 | 100局5314接受动作、每变体169994完整状态点零差异；仅局部样本兼容 | outputs/native_engine_numeric_replay_equivalence.json、platform/numeric_schema_migration.json |
| BC | 1344搜索样本，整局0..8train/9..11val，20epochs与checkpoint | outputs/training/bc、models/platform/learned.pt |
| PPO | 4096步骤27局，checkpoint与训练曲线；采样推理可实际下牌 | outputs/training/ppo、models/platform/ppo.pt、docs/audits/PPO_BEHAVIOR.md |
| 配对评估与消融 | 7策略各100局、700局，0crash/timeout/rejection；恢复式JSONL | outputs/evaluation/main/report.json、summary.csv、records.jsonl |
| 研究最低数量 | 32PDF/正文、20固定源码包/明确范围快照、12精读 | outputs/research/corpus_acceptance.json、docs/research/INDEX.md |
| 真实模型资料 | 30MB单位ONNX实际推理，97标签与许可核对；另对2张真实资料截图实际检测和标框 | outputs/research/vision_model_smoke.json、research/real_detection、docs/PIXEL_PATH.md |
| 中文教学 | 建模/调用链/改牌/优化定位/练习/学习路线/实验 | docs/START_HERE、MODELING、ARCHITECTURE、ENGINE_GUIDE、STRATEGY_MAP、TUTORIAL、LEARNING_PATH、EXPERIMENTS |

数字参数外置已实际进入原生工厂/战斗结算，普通与进化参数分别配置。Arrows波数与Cannon寿命也有实际状态检查。68个其他CardStats数值成员、36个效果私有类、子单位、塔/等级等尚未全部外置；详见BALANCE_CONFIG及unsupported_numeric_parameters.json。

最终pyd SHA256为`6160b968f9dfde6ab9b047627596c5d50383b0af8728ed5036fc49239edccd53`；默认cards哈希`9095f8eb1cd2fafa6115afb342b4888c6ebe23436e97e908919a1b8cee21129e`。收紧原本被接受但未使用的字段后，先独立复核及100局逐状态比较，再迁移实验元数据。旧配置/失败审查和旧模块均已归档。此次评估命令为恢复校验与汇总，`elapsed_this_run_seconds`不能当成完成700局所需时间。

## 实际策略成绩（同冻结场景）

| 策略 | 胜/平/负 | 实际接受动作 | 决策p95毫秒 |
|---|---:|---:|---:|
| random | 34/0/66 | 2706 | 0.026 |
| greedy | 50/0/50 | 2753 | 0.095 |
| search | 63/0/37 | 3033 | 16.50 |
| learned BC | 48/0/52 | 2215 | 0.72 |
| PPO argmax诊断 | 0/0/100 | 1 | 1.51 |
| search无对手响应 | 60/0/40 | 2925 | 6.28 |
| PPO概率采样 | 37/0/63 | 2886 | 1.97 |

场景包含4卡组、多个种子、成对换边、随机/规则/无响应搜索3类对手，最多3000tick；常规时间后领先或国王塔摧毁可提前终局。搜索40tick/6候选/15ms软预算，4worker。p95超过15ms说明预算并非硬实时保证。

同场景搜索相对规则得分+0.13，有响应相对无响应+0.03。样本仍有限，详见EXPERIMENTS的统计区间；不宣称显著或全局最优。每局replay、压缩决策记录与失败局势均已保存。

BC总体验证88.07%，WAIT100%，PLAY29.09%；高总体准确率不能推断强度。PPO训练有731次实际出牌，并非训练时全等待。argmax失败是WAIT单项概率大于任一分散落点，而下牌总概率更高；采样模式恢复执行。两个结果均保留，不用有利配置掩盖失败。

## 范围与剩余差距

- 173=132非进化+41进化，不是173种独立官方基础卡；全部部署/快照通过不等于全部交互一致。51机制行是显式研究检查分母；官方轨迹一致验证0项。
- 混合日期/等级：公主塔2534/王塔4008与单位注释level11不统一；进化、英雄和部分特殊效果为近似。Golem双建筑夹角不移动为真实已知expectedfail。
- 世界模型重建丢弃部分衍生负ID单位/弹道/状态计时/队列；4卡轮转不是冠军全部规则。有限候选、短窗口搜索没有全局最优保证。
- 屏幕检测ONNX只输出bbox/class/conf；阵营、手牌、圣水、HP、追踪、执行设备尚未闭合。2张真实资料图分别0框和1个疑似塔皮肤误检，裁剪域不匹配且无对应标注，不能计算准确率或mAP；真实截图准确率/官方游戏胜率均未验证。
- R2D2全文HTTP403；专家数据约2.02GB未全量下载，保存两小样本并给恢复方案；17源码包明确是范围快照而非完整资产。模型自身metadata许可AGPL-3.0，不能因仓库MIT而忽略。
- 续训保存model/optimizer/steps，但不恢复完整环境/RNG/中途rollout；no-op续训bug已修复并保留前后证据。网络critic不充分表示手牌/下一张，是后续改进点。

## 可直接继续

测试 `.\run.ps1 -Action tests`；研究完整性 `.\run.ps1 -Action research`；重复主评估 `.\run.ps1 -Action evaluate`；补新配置用独立--output目录。继续PPO `.\.venv\Scripts\python.exe -m royale_lab train-ppo --steps 8192 --resume`；复现失误 `.\.venv\Scripts\python.exe -m royale_lab replay outputs/evaluation/main/search/000.replay.json`。训练入口默认会写models/platform，重训前先备份已有模型/曲线；不同实验模型必须保留独立目录与元数据。改引擎先停止服务，build_native.ps1 -Tests，再重训/评估。

优先路线：修世界模型重建/候选时机 → 增加独立轨迹校准与剩余参数外置 → critic/层次化动作与策略池 → 真实视觉标注/设备路径。详细难度/依赖见ROADMAP。本次达到规格允许的本地研究后端交付；完全复刻和官方客户端兼容不在已完成项。

最终机器可读验收与文件SHA256：outputs/platform/acceptance.json。中文路径子进程UTF-8故障已修复，失败日志留在outputs/archive/repro-utf8-failure.log。
