# 真实轨迹导出与最小行为克隆

本模块负责把已经运行的原生回放证据变成可审计轨迹，再进行最小 NumPy 行为克隆。源码回放动作、初始化假设、旧近似模拟器输出均不能直接成为这里的训练输入。没有合格样本时命令返回失败，不创建 checkpoint。

阶段二最终实际产物：`outputs/phase2/learning_pipeline/export/export_manifest.json` 报告26,068行、18,440个`eligible_hard_e`；整局切分为train 13,431、validation 3,348、test 1,661，Parquet readback已验证。标准 hard-E checkpoint 位于 `model/checkpoint.json`；G 采用同一数据权重的 `model_aggressive` 与 `model_g_variant`，variant只在保存的推理元数据中加入 `inference_play_bias=4.0`，再由合法动作mask约束，未添加合成训练行。独立 held-out 与G的10局原生结果分别在 `heldout_evaluation.json` 和 `outputs/phase2/native/agent_eval/acceptance-008/report.json`。

## 当前实际证据

`outputs/phase2/learning_contracts/report.json` 记录了真实执行的空 Parquet 导出/读回、零样本拒训、11 项独立合同与数值梯度测试。观测扰动/数值测试没有运行游戏，没有训练或保存模型；其余切分测试读取实际冻结源数据及假设文件。

真实原生失败回放 `outputs/phase2/replays/development/ordinary-first/episode.json` 已通过本导出器处理。原始 798 个决策记录产生 745 个**隔离诊断行**：712 个采样 WAIT、33 个有唯一 CardPlay 消费确认的出牌；另排除 43 个空的非采样网格边界和 10 个未唯一确认消费的动作。原回放 `runtime_replayable=fail`、`outcome_consistent=fail`、`deterministic=not_run` 原样保留，两类可训练样本均为 0，实际训练 CLI 返回退出码 2。`outputs/phase2/trajectories/ordinary-first-failed-diagnostic/` 保存 Parquet、导出分母、逐项原因、可读样本和原始动作对照；`outputs/phase2/learning_contracts/real_episode_training_refusal.log` 保存拒训日志。

该旧失败 run 不会因后来修复转换器、终局比较或 backend 而自动变为通过。新证据来自新的实际运行目录；本轮 learning16/formal2 三轮证据已满足10,000 hard-E门，BC训练、留出重载和十局原生模型对战均有独立产物。旧失败仍只作为拒训/失败对照。

第二个真实开发 run `ordinary-feedback-fixed` 已额外导出并重读 755 行：712 个采样 WAIT 与全部 43 个出牌输入消费确认。其 parse/runtime/outcome 通过，重复性尚未运行，仍有 0 个默认合格样本。对于原生事件 ID 为 0 的英雄形态，导出保留 `raw_native_event_card=0` 和 `effective_form_identity=unknown`，只根据实际前后手牌、消费费用、参数和轮转证据确认输入根卡被消费；进化 alias 另保存实际 catalog hash 证据。这些证据不进入 actor 和合法 mask，也不把未知有效形态改写为已知。

## 文件与调用链

- `royale_phase2/trajectories.py`：冻结整局学习切分、原生目录适配、观测隔离、证据核验、合法性门槛、Parquet 导出和重读。
- `royale_phase2/learning.py`：只读取白名单 actor 的特征、小型 MLP、分项评估、无 pickle 权重存取与模型推理。
- `tools/phase2_export.py`：读取 root runner 的 `royale-phase2-native-reconstruction.v1` 目录，也接受 `royale-phase2-native-episode.v1` 规范输入。
- `tools/phase2_train_bc.py`：默认 `hard_e`，不允许零样本，默认至少 10,000 个有效决策。
- `tools/phase2_evaluate_model.py`：在新进程中重载权重并计算留出决策指标；它不运行游戏对局。
- `tools/phase2_export_verify.py`：复现空样本门槛与合同测试，可以额外核验真实失败导出的拒训。

`adapt_native_reconstruction()` 按请求 ID 把 `decisions.jsonl`、`states.jsonl`、`actions.json` 连接起来。原生 raw capture 留在 debug 文件，导出 actor 由 `visibility.project_actor()` 重新计算，并与 runner 保存的投影逐字典核对。`before_evidence` 支持带 SHA256 的 JSON 指针或 JSONL 行定位，确保附带的证据确实对应这个原生观测。

## 观测与标签边界

actor 只包含当前原生 tick、本人 owner、本人当前手牌/费用/圣水、皇冠，以及完成 reset 身份核验的公共塔血。卡牌参数、有效形态 ID、双方完整 cycle、敌方手牌/精确圣水、非塔实体、内部目标和未来 source journal 都不进入特征或 mask。重新加载时也检查白名单，遇到多出的私有字段拒绝处理。

时序要求：采样处于 `pre_submit`，原生 transport 观测序号小于提交序号，观测 tick 严格小于实际 CardPlay tick。出牌标签为**原生输入请求落点**，不是法术最终命中位置，也不声称原始人类点击时刻已知。CardPlay 事件只确认对应 owner/card 在实际 tick 被消费。

WAIT 只能来自 `regular_grid` 的无本方动作区间。导出器核对完整 source journal，区间内存在本人出牌或技能就不能标 WAIT。空的非网格动作边界不补 WAIT。日志说明 `human_explicit_wait=false`，并分别保存采样网格间隔与实际 native advance 长度。未知技能不能转换成 WAIT。

奖励版本 `native-sparse-final-result.v1`：只有真实转移到 finalized 且原生结果明确映射到 owner0/1 时发 ±1，其他非终局转移为 0。未知终局值不会伪造成平局。BC 不使用 reward 作为 actor 特征。

## 预算预筛与合法动作子空间

`affordability_only_mask` 始终声明 `full_legality_verified=false`。它只检查当前 4 个槽与本人费用，不证明落点、命令就绪或技能合法。

最小严格子空间 `wait_or_arena_spell.v1` 只允许 WAIT、当前手牌里的普通 Fireball `28000000` 和 Arrows `28000001`。其落点域为原生请求整数 `x∈[0,17999]`、`y∈[0,31999]`；其他卡和技能继续完整重放，但对应训练标签隔离，不能改成 WAIT。

只有通过原生正例/负例、敌方私有字段扰动、本人命令就绪检查，并锁定实际版本与卡牌 catalog 的 proof manifest，才能将该子空间的 mask 标成已验证。验证针对声明的两个卡族规则，不声称逐一穷举所有连续坐标，更不代表整个游戏全部卡牌都有完整 placement mask。缺少 proof、hash 不匹配、版本不匹配或检查未通过时，`eligible_hard_e=false`。

上游 FirstLight 的 `battle_env.py::action_mask()` 不直接用作 actor mask：它会读取 rich context、有效形态、完整实体中的建筑 footprint，并输出 `effective_card_id/native_effective_card_id/form_code` 等字段；部分 placement profile 也明确是 approximate 或 blocked。

proof 文件契约：

```text
schema = royale-phase2-action-subspace-proof.v1
subspace = wait_or_arena_spell.v1
version_fingerprint_sha256, catalog_sha256
catalog_reference = {path,sha256}  # 真实catalog提取报告/源表，hash须相同
allowed_card_ids = [28000000, 28000001]
bounds = {x:[0,17999], y:[0,31999]}
actor_dependencies = own_current_hand, own_current_cost, own_elixir,
                     command_ready, public_tick
checks.native_positive/native_negative/enemy_private_perturbation/command_ready
  每项：status=pass, evidence=[{path,sha256}, ...]
```

这些是要求提供的字段，不是已经发生的原生测试结果。实际 proof 由原生测试模块生成，导出时重新检查文件和 hash。

## 质量分层与操作兼容

默认训练同时要求 parse/runtime/deterministic/outcome 四项 pass、source 等级保持、当前手牌完整、本人 command ready，以及执行前观测和唯一 native 消费证据。独立轨迹参考不存在时 `trajectory_validated=not_available` 可以保留，但记录为“原生重建且可用结果一致，缺独立时序真值”，从不命名为 official gold。

`eligible_native_label_bc` 还没有完整动作子空间合法性保证，只能显式选择诊断训练，不计入严格学习验收。`eligible_hard_e` 额外要求当前 label 位于有证明的公共子空间。失败轨迹仍可导出为 `quarantined_native_diagnostic`，方便找分歧，但默认训练忽略。

`configuration.actual_version_compatible=pass` 表示**操作层面的来源回放兼容**：实际资产指纹、无损配置、完整执行、自然终局、可用 source outcome 字段、三次独立 reset 确定性共同通过。来源 build 仍是 `unknown`，scope 明确为 `operational_source_reconstruction; not proof of identical official build`。不能用版本字符串相等或一次胜者相同替代这些证据。

确定性补充文件使用 `royale-phase2-repeatability.v1`，其中 `episodes[]` 每项提供 source ID、`version_fingerprint`、status、至少三个相同语义序列 hash、三个不同且 hash 已锁定的真实 run `episode.json` 引用、不同 reset generation 与初始化一致证据。导出器核对每个 run 的 source/version/实际原生运行；等级/runtime 是独立训练门槛，不与确定性维度混为一个状态。没有补充证据的首次运行仍是 `deterministic=not_run`。

## 整局切分与筛选偏差

`data/phase2/learning/split_manifest.json` 对当前冻结 120 局按整个 `leakage_group` 预声明 train 80 / validation 21 / test 19。规则为 SHA256(`bc-split-v1|dataset_revision|leakage_group`) 前 64 位取模 100，70/15/15 分桶；文件不使用原生终局结果。其 SHA256 逻辑指纹为 `1157fe350c15e4caeaf66264efadf4dd13e26b29772da5fc720e523a335817af`。

原有 development/formal 重建身份独立保留，不能因学习切分而把已用于引擎校准的 formal 结果重新宣称为独立。换边、切片、初始化假设和既定近重复组必须同组。原冻结清单的近重复规则有范围限制，不声称排除了所有未知重复。

后来发现原 120 局只有 9 局满足“统一等级/无技能事件/包含这两种法术源标签”，且这 9 局的既定学习切分没有 test。因此从**已经下载的同一 852 局分片**中增加 65 个仅按公开 source 条件筛选的学习来源，不读取终局来筛选。`additional_source_manifest.json` 只含这 65 局，`combined_source_manifest.json` 包含原120+65=185 局；原 formal100 和原学习切分文件字节均保留。`extended_split_manifest.json` 为 train127/validation26/test32，所有原 group 分配不变。65 局纯转换实际通过，3859 个源出牌全部保留，没有生成原生训练轨迹。

`native_cohort_manifest.json` 在新增65局中预声明首轮 train10/validation3/test3：每个 split 先选 source action 数≥25，再按 SHA256(`phase2-native-learning-cohort-v1|revision|group`) 排序。没有缺额或阈值以下补齐，未使用 source/native 终局。每局计划3次独立reset、grid5；训练只导一个预声明的实际run，不能把三次重复复制成样本。该筛选明显排除了技能与混合等级，并偏向含两类法术的卡组，独立 test 只解释这个受限来源分布。

另有 `formal_training_cohort_manifest.json`：在原正式分母中依同一公开筛选/哈希规则冻结 train `91e21faee1ae4abbb424e759585bf2f0`（53源动作）与 validation `4038bd96de2c4976be40e487b5c3b636`（62动作）。两局是否真正合格由后续三次原生 reset 证据决定；清单本身不等于原生通过。它用于证明正式分母里确有可训练子集，不能只靠扩展来源替代这项证据。

导出报告保留所选清单的完整分母（原120或联合185）、尝试文件、失败文件、未尝试 ID、逐局排除原因，以及过滤前后 WAIT/出牌/卡牌分布。没有形成原生文件的候选也记录 `failed_inputs`、`attempted_source_ids` 和 `unavailable_episode_inputs`，不伪造状态行。两法术子空间、动作确认和 outcome 过滤都会明显改变样本分布；模型结果不能代表全部卡组、建筑、英雄或进化机制。

## 训练、重载与运行命令

环境：`.venv-phase2-data` 的 Python 3.11、NumPy 2.2.6、PyArrow 19.0.1；锁文件 `tools/phase2_learning_requirements.lock.txt`。项目内安装时把 PIP_CACHE_DIR、TEMP、TMP 指向 `.cache/phase2/pip-data`、`tmp/phase2-data`，不安装全局依赖。

已实际执行：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONUTF8='1'
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_export.py --freeze-split-only
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_export.py --input outputs/phase2/replays/development/ordinary-first/episode.json --version-manifest data/phase2/version_manifest.json --output outputs/phase2/trajectories/ordinary-first-failed-diagnostic
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_export_verify.py --native-export outputs/phase2/trajectories/ordinary-first-failed-diagnostic
```

以下是合格数据到齐后的命令模板，不能把模板当作已完成训练证据。`PROOF.json`、`REPEATS.json` 需替换为实际原生测试文件：

```powershell
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_export.py --input-dir outputs/phase2/replays/learning_selected_run --split-manifest data/phase2/learning/extended_split_manifest.json --version-manifest data/phase2/version_manifest.json --subspace-proof PROOF.json --quality-supplement REPEATS.json --output outputs/phase2/trajectories/formal
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_train_bc.py --export outputs/phase2/trajectories/formal --version-manifest data/phase2/version_manifest.json --output outputs/phase2/models/bc_hard_e
& .\.venv-phase2-data\Scripts\python.exe -B tools/phase2_evaluate_model.py --checkpoint outputs/phase2/models/bc_hard_e/checkpoint.json --export outputs/phase2/trajectories/formal --version-manifest data/phase2/version_manifest.json --split test
```

模型为一层 tanh MLP、5 类槽位头、两维 sigmoid 请求坐标回归头；位置损失仅用于真实出牌样本。卡牌词表只从训练整局构建，验证/测试未知卡用 UNK。训练记录类权重、随机种子、学习曲线、WAIT/出牌 precision/recall、槽/卡牌准确率、原生坐标 MAE、mask label coverage 和质量分布；同时披露仅 WAIT 合法的强制样本数，训练/验证若完全没有真实出牌标签则拒绝展示一个仅学 WAIT 的模型。用验证损失选 checkpoint，不用 test 选参数。

`weights.npz` 仅保存 float32 数组，加载设置 `allow_pickle=False` 并检查 tensor 名称、形状、有限值和文件 hash。checkpoint 保存数据/切分/版本/源码 hash。`NumpyBCPolicy.load(checkpoint, version_fingerprint=...)` 在不同实际资产版本上拒绝加载；`predict(actor, legal_mask=...)` 会重新核验硬子空间 proof 和 public-only mask，而非相信调用者传来的任意 True 槽。

推理时的瞬时三张手牌与训练门槛不同：训练仍隔离不完整观测，推理在不完整手牌时只允许 WAIT，让原生时间推进到下一观测。原先直接异常的过强前置条件已经修复。`tools/phase2_evaluate_model_boundary.py` 用真实 formal capture（owner1/tick1440/3手牌）及明确未训练的数值张量，实际验证两个 readiness 输入都产生合法 WAIT，伪造出牌槽被拒绝；这不是训练后模型或原生 agent 对局证据。

正式来源 `91e21fae…` 的第26个动作还揭示消费归因的另一边界：Cannon 进化出牌后可能在实际下一 tick 先变成3手牌/5张cycle，随后才补位。现行导出器原先要求同槽立即出现替补，仍拒绝这条旧反馈。独立的**未接入候选** `tools/phase2_export_transient_candidate.py` 从原始 states/wire、精确 scheduler receipt、参数、费用、唯一事件、其余手牌整行与 cycle 增量，以及锁定 CSV/name join 重推这条消费证据；`pending_draw_adapter_candidate.json` 保存实际只读检查与反例。该报告不改变旧 run 的 runtime fail，不提供训练行，也不代替新 backend 窗口的真实验证。

## 已有三轮产物的自动流水线

`tools/phase2_learning_pipeline.py` **不会启动、连接或等待 native RPC**。它读取三轮 queue 和真实 episode 引用，验证来源、grid5、版本、proof、三次独立 reset 以及预声明切分。只有 `r1` 提供训练轨迹，`r2/r3` 仅用于重复性。可用多次 `--repeatability` 合并互不重叠的16局与正式2局报告。

候选局缺少某轮结果、无法形成 states、重复性失败或其重复报告引用了错误轮次时，按来源隔离，并在 `round_failures`、`failed_repeat_sources`、`validated_repeatability.json` 和导出 `failed_inputs` 保存完整原因。派生重复报告只能保留经过引用核验的既有 pass，不能推断新的 pass。合格子集达到10,000个真实 hard-E 决策、train/validation/test 都保持整组隔离且各有真实出牌标签，便可训练；不能因其他候选失败而拒绝整个子集。相反，跨清单额外/重复来源、错误 grid 或版本、公共 proof 不成立仍是完整性错误。独立的验收 D 仍要求至少10个来源各有3次真实 reset pass，不能用训练数量替代。

重复性 pass 会实际重读三轮 `episode.files`、重算完整暴露的原生语义序列，并比对原报告 hash、真实 reset generation 与初始化；仅有未修改的 episode.json 和一个旧 pass 字符串不够。导出读取和模型训练的新进程也按来源重核，第二轮 states 被删除或改动会使该来源失败。16项合同/数值检查（含此反例）已实际通过，日志 `outputs/phase2/learning_contracts/pipeline_isolation_tests.log`；它们不声称产生了任何合格原生样本。

数据合格后它实际调用训练 CLI，在另一个进程重载模型并评估 test，保存命令、退出码、日志及文件 hash；输入与实现完全相同时可复用已经验证的 checkpoint。`--additional-cohort-manifest` 可加入上面的正式2局，搭配 `--additional-r1/2/3`。这仍不替代原生十局模型对战。

默认产物位置为 `outputs/phase2/learning_pipeline/export/export_manifest.json`、`outputs/phase2/learning_pipeline/model/checkpoint.json`、`outputs/phase2/learning_pipeline/heldout_evaluation.json` 及同目录 `pipeline_state.json`。改变输入/实现后需选新输出目录；已有 identity 不匹配时单独写 `pipeline_refusal.json`，保留旧状态和 checkpoint。

准备状态已实际测试：`--help` 正常；将 r1 指向明确不存在的测试目录时立即拒绝，`native_rpc_opened=false`，没有等待服务器。证据 `outputs/phase2/learning_contracts/pipeline_not_ready_probe/pipeline_state.json`。

新进程重载后的离线指标与真实原生十局 agent 对战是两项独立验证。后者需由 native controller 在每个决策点重新观察、重新生成就绪 mask、真正提交动作、确认 CardPlay 并到达自然终局；本模块不会用离线预测数字代替它。
