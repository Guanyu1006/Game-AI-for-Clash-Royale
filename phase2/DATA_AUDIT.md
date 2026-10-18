# 阶段二真实回放：来源、冻结样本与转换证据

本支线已取得真实稀疏动作数据，完成原始双表核对、100局正式组与20局开发组冻结、可追溯转换、Parquet回读和固定FirstLight转换器的纯代码预检。**尚未执行原生战斗，尚无有效训练决策样本**。结构解析、源码转换、原生执行、终局一致与独立轨迹对照是不同证据层级。

## 来源与固定版本

公开数据集为 [VanguardX101/IL_Replay](https://huggingface.co/datasets/VanguardX101/IL_Replay)，本地首次读取后固定 revision `059d43a02138a34b1b3009cc2acc7630fb99a638`。原始API、README、manifest、SHA256SUMS、compatibility和完整文件树均保存到 `third_party/phase2/dataset_metadata/IL_Replay/`，下载记录在 `outputs/phase2/data/download_log.jsonl` 与 `acquisition.json`。

发布者manifest登记252,238局、17,836,160动作、104份Parquet（52 replay分片、52 action分片），合计1,888,381,137字节。本阶段没有下载全部数据：先按发布体积选择最小 `replays/part-000051.parquet`，再取得同编号动作分片以逐事件核对。

| 已下载不可变文件 | 行数 | 字节 | 发布SHA256与本机核对 |
|---|---:|---:|---|
| replays/part-000051.parquet | 852局 | 2,737,912 | `290a5e5eb92dc604b2fd8dec7f971aa54ada372c76d686f00bf157002d7f8072`，一致 |
| actions/part-000051.parquet | 59,906动作 | 3,541,992 | `32c260266e8a6657b892fa7d2bd8b710cab639cf29e491d1df28da741eafc386`，一致 |

文件放在 `data/phase2/raw/IL_Replay/<revision>/`。原始文件不改写；转换数据另存。下载器每次检查发布SHA256，限制单分片预算8 MiB并检查磁盘余量；已完成分片校验后复用，未完成文件只能保留为 `.partial`，不能当作完成。当前重试从整个小分片重新下载，不宣称HTTP字节范围续传。

固定数据集card没有license字段，文件树没有LICENSE；**数据许可为未声明**。FirstLight源码Apache-2.0只适用于其源码，不自动许可这份数据或游戏二进制。发布者说明源于RoyaleAPI公开回放且已脱敏；SHA256核对证明本地与发布文件一致，不证明发布内容属于服务端原始状态。

## 实际schema与字段语义

实际Arrow schema完整保存在 `outputs/phase2/data/source_schema_audit.json`、`source_action_join.json`，不是根据网页猜测。

| 层 | 实际字段 | 转换时如何处理 |
|---|---|---|
| replay行 | schema_version、replay_tag、battle_type、game_mode、result、team_crowns、opponent_crowns、event_count、warning_count、payload_json、requested_player_tag | 保存原始行索引、分片hash、payload文本hash；JSON为普通文本，不执行pickle |
| payload | battle、events、replay、source、validation、collection_schema_version、schema_version | 每条原始事件完整保留；记录row/payload schema |
| 双方配置 | battle.team/opponent.players[].deck[].card_key/level/name、tower_card、final_tower_hitpoints、crowns | 同时保存双方配置用于重建；不把双方牌组作为actor观测 |
| 动作 | source_index、kind、side、card_key、card_play_number、replay_tick_20hz、time_seconds | 保留相对20Hz tick、原始顺序和来源事件序号；同tick不按玩家重新排序 |
| 形态 | deck_card_key_candidates、form_at_play | 牌组后缀是可用形态线索；全部57,432条下牌的form_at_play为unknown，不能直接当作已进化/英雄形态标签 |
| 技能来源 | ability_source_candidates、ability_source_authoritative | 2,474条技能事件的原始authoritative均为false：未确定技能来源卡/实例，不能把候选当确认来源 |
| 坐标 | raw_royaleapi_units、display_royaleapi_units、native_world_units、grid_cell_floor、subcell_offset_from_floor_center、inversion_applied、inside_18x32_arena | 各表示完整保留；额外提供按18,000×32,000缩放的归一化坐标，明确只是dataset给定native frame，不是已标定当前引擎坐标 |
| 参考终局 | result、双方crowns、final_tower_hitpoints、replay.duration | 作为参考标签单列；不注入引擎状态，不把duration自动当作已知原生终局tick |

这里必须区分两个名字相近的字段：原始 `ability_source_authoritative=false` 仅限定**技能来源候选的可靠性**，下牌事件通常为null；本项目新增 `source_authoritative=false` 表示**本流程没有以原生执行反馈确认这条收集事件**。新增字段不是声称原始文件有一个全局authoritative字段，也不是将全部原始事件判为假。原始值始终在 `raw_event` 和单独技能字段保留。

公共replay_tag是发布者重新生成的32位UUID hex。原始回放ID、URL、玩家/部落身份、对局日期和采集日期已经删除，无法靠这份数据恢复独立原录像链接。可追溯链止于**公开匿名ID + 固定revision + 原始分片hash + 行号 + payloadhash + 事件索引**，不能伪造为已取得官方原始ID。

未采集的字段包括：初始手牌、初始轮转、原始RNG seed、精确起始圣水、国王塔等级、原生构建号、平衡日期与确切竞技场。数据中塔兵种等级不自动等于国王塔等级。源码转换采用的默认值必须作为假设，不能写回这些源字段冒充真值。

## 852局全分母审计

`tools/phase2_data_prepare.py --audit-only` 实际解析852/852局；57,432次下牌、2,474次技能，共59,906事件。对应actions表59,906行的完整event_json、source_index、kind、side、卡牌、tick/seconds、坐标及候选字段与payload逐项一致，丢失/重复/孤立动作/不一致均为0。

卡组内登记等级按卡位计：16级10,073条，11级3,488条，15级67条，14级3条，13级1条，共13,632卡位。观察到16种 `-hero` 卡键和42种 `-ev1` 卡键，包括wizard-hero、berserker-hero、balloon-hero、elite-barbarians-ev1、princess-ev1等；这说明待匹配的机制需求，**不构成构建号或平衡日期证据**。不能把它们偷偷映射到第一阶段混合等级模拟器。

源模式包括Ranked631局、Grand Challenge182局及少量友谊/锦标赛/其他模式；结果为victory551、defeat301。没有draw样本不能说明本模式不会平局。全分母与各字段分布见 `source_schema_audit.json`。

## 正式100局与开发20局如何冻结

锁定清单为 `data/phase2/frozen_manifest.json`，SHA256：

`47823338241815c22182347f2c46daf5a353f52c0d2e63d37e55d903e4945627`

从852局中先构建泄漏group：相同匿名ID、完整内容hash、可交换双方并旋转180度的动作签名，以及按0.1秒/半格坐标量化后相同的近重复签名，均归一组。量化近重复是公开声明的有限启发式，不保证发现一切语义近重复。本分片检查到852个不同group，没有上述重复组；测试另构造了换边、微小坐标变化和重复ID，验证不会跨group拆分。

group按 `SHA256(revision + '|formal-freeze-v1|' + group_id)` 排序，前20组作development，随后100组作formal；每组只取匿名ID字典序首条。未根据解析成功、引擎成功、胜者、塔血或牌型筛选；其余732局保留为未选择ID。正式与开发ID/group交集均0。固定了来源分片和选择规则后，才运行FirstLight预检。

这是单个852行分片中的冻结基准，不能代表全部252,238局或所有游戏版本。120局包含胜85/负35、Ranked93局、Grand Challenge24局及其他3局；117局含hero键，全部120局含evolution键，102局记录技能；原始data_i为0的64局、为1的56局，能够检查双方真实视角转换。后续新增训练数据必须与formal group隔离；不能把这些formal测试局的裁片、换边或不同初始假设放进训练集。

## 两层转换及其证据范围

### 源数据层：不建立隐含初始状态

`royale_phase2/replays.py::parse_payload` 输出 `royale-phase2-source-episode.v1`：

- provenance：revision、分片hash、行索引、payloadhash、转换器版本/hash。
- match_config：原卡组/等级/塔兵种，未知seed/手牌/轮转/构建/平衡日期保持null或unknown。
- action_journal：原事件、相对tick、所有坐标、原始顺序；target_engine_tick、actual_execution_tick、engine_card_id与hand_slot保持null。
- reference_terminal：来源胜负/皇冠/塔血/时长独立于执行结果。
- quality：parse_valid=pass；runtime_replayable、deterministic、outcome_consistent=not_run；trajectory_validated=not_available。

源数据层的 `owner=0/1` 只是team/opponent枚举，**不是native owner**；每个对象都有owner_semantics，native_owner=null。不得直接把它提交到FirstLight，因为FirstLight根据data_i恢复真实上下侧后重新映射owner。

120局导出为 `data/phase2/converted/source-v1/episodes/*.json` 和 `episodes.parquet`，8,622条源动作导出为 `source_actions.parquet`，有可读JSONL小样本和对应raw payload。两份Parquet实际重新读取后逐行一致；文件hash与结果见 `outputs/phase2/data/conversion_readback.json`。示例：`data/phase2/converted/source-v1/episodes/47ab2b5f48514435b1288eb1d69c2ac0.json`。

`source_actions`中的actor_observation、legal_mask、reward都是null，training_decision_valid=false，execution_status=not_run。8,622是**源动作数量，不是有效训练样本数量**。未记录动作的间隔也没有被偷偷展开成人类WAIT标签。

### 固定FirstLight层：明确标记为重建假设

实际运行固定 [FirstLight_CR](https://gitlab.com/firstlight3/FirstLight_CR) commit `71944e3993b594b3cc89b38768b5e817dfbdbe0e` 的 `prepare_collected_replay`。另核读Jason commit `70a08af6807fa4d59a11c7d52bf6272a96afe30c` 的build_cache调用链。FirstLight转换器SHA256为 `49a3d698a6cb8c82315651c4469a8aaf7db5e6c249257717249c0cd2479e8a0f`；实际导入源码及competitive tables的hash保存在预检报告。

120/120成功产生可序列化配置/操作：formal100、development20；真实8,622事件对应8,242个下牌command、380个技能command，没有丢失、复制或改动源事件顺序。输出再次用上游TrainingReplayV1.from_mapping重新加载验证checksum与内容。这里仅执行公开Python转换代码，**零次native reset/step调用**。

转换规则与假设如下：

1. 依据source_fields.data_i恢复True Blue/True Red；data_i=0时team映射native owner1，data_i=1时team映射native owner0。源码会按该规则翻转坐标；不能在源数据层再手动旋转后又重复应用。
2. 以20Hz source command boundary加1 tick生成FirstLight的首次可观察状态tick。该+1只属于固定接口约定；没有套用Jason其他路径的部署延迟，也未证明能用于其他native构建。
3. 从完整公开出牌顺序选一组合法初始4+4轮转，使用固定NATIVE_DEAL_SEED与可重复的replay-tag选择；不是恢复原始隐藏手牌或RNG。选择依据不含胜者、皇冠或塔血，但仍是以全局公开序列约束的后验初始化假设，不能当独立初始状态真值或不加审查的早期actor特征。
4. 形态通过源牌组后缀映射availability mask；牌组存在进化形态不等于每次下牌都已进化。技能以候选/运行时唯一Ready实例解析，不捏造来源实例ID。
5. 上游把等级统一为源卡牌/塔兵种的众数，并用作国王塔等级。原始国王塔等级缺失；即使所有已知等级相同，完整配置忠实度仍unknown。
6. 上游operation中填充的wait用于推进引擎与补齐未行动一方，属于控制间隔，不是人类明确WAIT训练标签。

结果为 `outputs/phase2/data/firstlight_preflight.json`，逐局假设档案为 `data/phase2/converted/firstlight-hypothesis-v1/*.json`。档案包括原生card ID/owner、目标tick、配置、operations、开手/轮转假设、warnings与质量标记。文件名/字段里出现upstream TrainingReplay是其序列化类名，**不代表本项目已执行一场训练对局**。

FirstLight的目标为APK15.535.13/arm64与content15.535.86；Jason另提August150535029 x86_64 loader。数据本身没有构建指纹，不能凭转换通过认定这两类native runtime或数据版本相同。

## 7局等级变化的严格拒绝

`outputs/phase2/data/configuration_compatibility.json` 比较每个原卡位与塔兵种等级对上游统一等级：113局已知等级未变，7局改变（formal4、development3）。`royale_phase2.dataset.require_lossless_source_levels(source_episode, hypothesis)` 在改变时实际raise，主重建不得静默使用这7份统一等级配置。

| 匿名ID | 分组 |
|---|---|
| 3c5dbac27fef471a924ebdf4db6ec0ed | development |
| 8b035ceb4c7241d7ad8d9652a84975de | development |
| 841294fa6ff14914af326a36fb362eda | development |
| 7e0c94f5ff2b453595bfd76157bb56cc | formal |
| 78d40aae23a2409e9403aa99cd6f488a | formal |
| 3a25da4c0c9643808e90a73b5c28b38f | formal |
| 1691b04b10a6401f8e36e691908e8ffd | formal |

正式100局分母保持不变。此7局都是Ranked，均含hero/evolution键，结果为6胜1负；如果后续直接排除，会改变模式与结果分布，不能在113局上宣称整个120局都忠实。完整保留前/后分布在compatibility报告。

源码审查发现固定MatchConfig仅提供全局level_cap/minimum_card_level、统一king_tower_level；deck.sp仅输出d/el，没有已验证的逐卡等级serializer。模板support card里虽然出现l字段，也不能未经验证就假定可直接填入显示等级。因此没有伪造 `sp.l` 或生成声称可执行的保留等级配置。7局各自的源卡位/塔等级及差异单独保存到 `data/phase2/requirements/preserve_source_levels/<id>.json`，native_configuration=null；后续需要验证native decoder具体字段、稀有度相对等级/显示等级、双方塔配置，再以实际HP/level状态对照，才能产生新候选配置。原始hypothesis档案没有修改。

113个已知等级未变的hypothesis仍有构建、国王塔等级与初始化未知，因此strict_runtime_ready=0。该guard只是防止已知有损配置，不是通过原生兼容验收。

## 本机环境、命令与恢复

独立 `.venv-phase2-data` 使用已有Python3.11创建，初始数据审计安装 `pyarrow==19.0.1`；之后为真实轨迹/最小BC增加 `numpy==2.2.6`，当前两项均锁在 `tools/phase2_data_requirements.lock.txt` 与 `tools/phase2_learning_requirements.lock.txt`。原始环境报告保留其生成时的历史状态。没有执行未知pickle，不需要HF token，不安装到系统。pip缓存位于 `.cache/phase2/pip-data`，临时目录位于 `tmp/phase2-data`。

在项目根目录运行：

```powershell
$env:TEMP = 'D:\研究生\Clash Royal\tmp\phase2-data'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = 'D:\研究生\Clash Royal\.cache\phase2\pip-data'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_fetch.py --shard replays/part-000051.parquet --max-mib 8
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_fetch.py --shard actions/part-000051.parquet --max-mib 8
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_prepare.py --audit-only
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_prepare.py
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_firstlight_preflight.py
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_compatibility.py
.\.venv-phase2-data\Scripts\python.exe -B tools\phase2_data_verify.py
```

相同冻结条件可重复prepare；选择、源hash或转换器指纹变化时拒绝覆盖冻结清单，需要明确创建新的数据版本。写输出先用项目内partial文件再替换；manifest存在本身不证明完整导出，必须运行回读/校验命令。上游纯转换预检需要固定FirstLight代码已在 `third_party/phase2/firstlight`，不会自动取最新版或启动设备。

`source_converter_tests.json`记录25项实际通过检查，覆盖NaN/Inf拒绝、同tick顺序、时间不一致、逆序、越界不裁剪造假、重复事件保留报错、换边/近重复归组、跨组重复ID拒绝、120份实际JSON追溯、Parquet实际回读/hash、未生成actor样本及7局有损配置拒绝。它们测试转换边界，不证明游戏交互一致。

## 原生运行取得后的下一步

先在development20局上验证实际native构建/资源指纹、true-side坐标、+1tick边界、选初手策略、费用/手牌/实体执行确认和终局时长。known-level改变的3个开发样本必须保留失败记录并修复逐卡等级路径，不能调节血量或移动动作来匹配结果。所有初始化尝试和假设应单独存档；不要根据formal最终结果重新选seed。

验证规则冻结后，对formal100逐局实际执行并报告五维质量。无独立视频/状态锚点仍为trajectory_validated=not_available；同一程序多次一致不能替代独立真值。只有真正采样了动作前的玩家可见观测、合法mask、实际执行反馈与版本化reward后，才可创建训练决策Parquet。当前有效训练样本数为0，距离阶段二10,000样本/BC/原生完整对局验收仍需原生运行证据。
