# 配对数据、整局录像与对齐验收

本轮把三种证据分别保存：同版本可重放的规则 teacher 轨迹，带可见排名的真实人类录像，以及历史视觉片段。它们回答的问题不同，不能跨来源拼成不存在的“官方高手状态—动作真值”。

## 同版本状态配对：80 局全部通过

`outputs/arena-next-20261113/data-paired-v1/` 包含冻结的 `plan.json`、每局完整配置、固定引擎身份、随机种子、逐步实体原始状态、双方动作与消费确认、双方公开观测 NPZ、自然终局、独立 reset 重放审计。固定 20 Hz、每 10 tick 决策一次，WAIT 是该时刻 teacher 主动返回的等待；出牌标注只落在实际观测后立即提交的时间，拒绝未来窗口反标。

- 60 / 10 / 10 局整局划分，双方视角始终属于同一集合。
- 80 / 80 自然终局、80 / 80 独立确定性重放通过；44,414 联合决策、88,828 单方观测、11,699 实际出牌、77,129 WAIT，0 非法标签、0 引擎拒绝。
- 含压缩、原始状态保存和独立重放的总耗时 434.86 秒，102.13 联合决策/秒，折合 662.28 局/小时。这是本机此次端到端采集吞吐，不是裸模拟器速度。
- 15 种实际出牌，但 Xbow 全集只有 5 次（train 2 / validation 0 / test 3），不能据此宣称学会连弩；能力按钮标签为 0。teacher 是项目规则策略，棋力与真人高手数据分开验收。
- 私有原始状态仅供审计；训练数据严格白名单排除敌方手牌等隐藏信息。完整但哈希损坏的整局拒绝静默重采；中断未完成目录和旧半写 manifest 整体归档到 `interrupted/` 后按同一冻结计划重采。JSON 完成凭据采用原子替换。

复现使用新目录：

```bash
.venv-cloud/bin/python -m arena_lab.paired_data --output outputs/my-paired-run --train 60 --validation 10 --test 10 --seed-start 2026111300
```

另一个像素模式投影所用的 50 局采集经历容器重建，恢复时复用了 37 局 / 18,300 联合步，新采 13 局 / 6,176 联合步。原报告把全部 24,476 步除以恢复会话 103.53 秒，得到的约236步/秒仅是混合处理率，**不能当全新采集吞吐**。原报告保留，单独更正见 `outputs/arena-next-20261113/data-pixel-native8-v1/throughput-correction.json`；完整采集累计耗时未知。新版采集器分别统计新采、复用重审和各自耗时，上述80局的434.86秒全新采集证据不受影响。

原始数据索引 SHA256：`641bece650374eb5401d35b4b8130bab6610ab8f525f270236450d9e8054e5d1`。旧高手事件假设重建与此前测试集合没有混入此数据。

## 真实高段位录像：固定来源、可见排名与完整性分别审核

来源为 [AlphaClash replay corpus](https://huggingface.co/datasets/josefbednar/alphaclash-replays/tree/cfcf165fa6fb0499aa75b49fc5936efec556ca61)，固定 revision `cfcf165fa6fb0499aa75b49fc5936efec556ca61`。已取得 80 + 11 局的原始 MP4、同名 JSON 和双方卡组 PNG，共 273 文件、3,824,872,454 bytes；逐一检查固定字节数和 LFS SHA256 或 Git blob SHA1。来源许可为 `game-footage`，游戏内容权利属于 Supercell，原媒体保留在忽略目录，未随项目源码重新发布。

原 80 局中 59 局能保守识别至少一方屏幕上的 Rank 字样，再补 11 局形成固定 70 局质量子集，50 / 10 / 10 整局划分。70 / 70 至少一方排名可读，67 / 70 双方可读，共 137 个可读玩家视角：51 个排名≤100、86 个101–200。此处是录制时屏幕显示排名，未把当前榜单身份或现在段位嫁接进旧录像。字体模板取自 5 张已目视检查的图片，包含源集合划分，所以排名读字的审计不是独立 OCR 泛化测试；策略测试仍按整局独立。

固定子集有 288,659 原始编码帧，3,036,022,755 bytes 原文件，全部容器和边界字段复核完成，没有跨集合相同 MP4 哈希。排名、完整录制和原生状态真值是不同门槛。现已目视检查全部 70 局的真实解码首尾画面（14 张对照表），并给 10 局开场已淡出的录像补取早 1.5 秒画面再次检查（2 张对照表）：70 / 70 含开场玩家演出和终局证据；68 局录到红蓝结果横幅，另外 2 局录到加时决胜皇冠与塔摧毁，未录到最后的横幅。`arena_lab/manifests/alphaclash-completion70-v1.json` 将每局目视判断与原图 SHA256 绑定，`data-alphaclash-completion-v1/completion-audit.json` 重验哈希后统计。该项验证开场到终局的视觉覆盖；没有声称逐帧检查所有内部内容、精确 tick、最终皇冠或胜负标签已全部验真。

数据卡所链接 AlphaClash GitHub 仓库在本次插件和网页核查都返回404，无法取得其原录制器结束检测源码；因此本次采用实际解码画面逐局检查，没有凭不可见的来源代码作保证。

```bash
# 新机器直接取得固定的 70 局：不需要先下载原80和补11。
.venv-cloud/bin/python -m arena_lab.alpha_data --plan arena_lab/manifests/alphaclash-ranked70-v1.json --output outputs/my-alpha70 --workers 3
.venv-cloud/bin/python -m arena_lab.alpha_ranks --corpus outputs/my-alpha70 --plan arena_lab/manifests/alphaclash-ranked70-v1.json --output outputs/my-alpha70/rank-audit.json
.venv-cloud/bin/python -m arena_lab.alpha_completion --annotations arena_lab/manifests/alphaclash-completion70-v1.json --corpus outputs/my-alpha70 --output outputs/my-alpha70-review

# 当前机器复用两个缓存目录做同一70局审计，不复制3 GB视频。
.venv-cloud/bin/python -m arena_lab.alpha_data --plan arena_lab/manifests/alphaclash-ranked70-v1.json --output outputs/my-alpha70-audit --cached-corpus outputs/arena-next-20261113/data-alphaclash --cached-corpus outputs/arena-next-20261113/data-alphaclash-supplement-v1 --skip-sample-decode
```

这批视频的精确客户端 build、完整初始状态、随机状态、权威逐时刻实体、玩家原始动作标签均未知。卡牌等级、卡组等是来源视觉识别元数据，进化/英雄配置尚未严格核验。`same_version_full_config_human_games=0` 与 `games_with_native_action_labels=0` 明确保留；不能把这 70 局和上面的同版本 80 局合称 150 局同版本高手监督数据。

## 已纠正录像时钟解释

来源 README 把 4 倍速写为视频时间除以 4，这与实图时钟相反。首局视频第 600 / 1200 / 2400 帧的屏幕分别是 2:36 / 1:55 / 0:34：稳定 4 倍回放期间，游戏时间增量约为视频 PTS 增量乘以 4。`duration_s` 是采集墙钟而非容器时长，视频实际平均帧率也非恒定 capture_fps；不能以 `frame_id/60` 制造准确游戏 tick。现阶段保留原始 PTS、加速切换和不确定性，未把视频时钟强接到模拟器事件。

首局 `battle_end_s=82.19` 仍处于 0:00 决胜动画，而容器真正尾帧才出现终局红蓝结果横幅。这说明 sidecar 的边界只能作为抽帧建议，不能当自然终局的独立证明。

## 其他已实取来源

TV Royale 固定 `f3a750881e008e0daf237afd45736231b51a955a` 的一个 488,889,400-byte Parquet 已真实解码 589 帧；589 / 589 与来源 BGR MD5 相符，0 重复像素。它是 arena 1 素材，无高手身份、精确帧时刻和原生配置，因此用于视觉研究而不冒充高手整局。

wty 固定 `ce86e10bedcf97762c3d207633f035cd12be8936` 的两份 OYASSU 文件经受限解析转为数值记录，实际只有 159 / 216 条，OCR 时段分别 4–36 / 4–47 秒、5 / 7 次视觉出牌、934 / 1,585 条推断实体。这是历史短片段，未把文件名当整局证明；原始对象文件没有直接交给训练进程反序列化。

## 验证与当前作用

`tests_cloud/test_arena_data_sources.py` 与 `test_arena_alignment.py` 检查路径逃逸、同名缓存冲突、同字节数内容篡改、同局跨集合、动作时间错位、未知消费、截图版本漂移，以及中断恢复不覆盖原证据。16 项通过记录在 `outputs/arena-next-20261113/data-source-tests-v4.log`。视觉覆盖注释的图像被改写、集合缺局或终局证据未知时均拒绝通过。

严格同版本轨迹已经足以验收状态对齐与时序学习；真实录像提供可审查的高段位像素和视觉任务素材。下一步使真人模仿可验收，需要从同一段视频恢复动作时刻、卡牌及落点，并逐项对照真实画面校准；无法精确恢复的字段保持缺失掩码，不能通过模拟重建填成真值。

## 像素策略的公开静态落点

`arena_lab/visual_geometry_v1.json` 是 33,984-byte 冻结资产，绑定引擎二进制 SHA、地图、规则、公开初始六塔位置/半径/占地和导出源码 SHA。导出器给 `point_grid` 的最小对象只有公开几何与六塔存活标记，根本没有手牌、圣水、tick 或队列；不会把初始出牌锁定误烘焙成全部禁止。运行中的像素投影直接读此文件，不查询当局 oracle。完整几何与内容哈希进入训练身份，部署从模型内嵌几何重建，防止磁盘资产更新改变已训练动作空间。

普通兵在两个座位旋转后的静态合法格各为247，国王塔半开边界存在5格方向差异，因此取两侧交集242格；Cannon为240格，Fireball/Arrows为576格。普通兵和建筑仍限己方前15行。它是保守候选掩码：动态建筑占位、塔被摧毁后的扩展、出牌锁定与待执行命令仍未知，真正执行以原生消费回执为准。

实际第一局双方 tick 90 / 100 / 110 的9个 MiniPekka/Musketeer 样本，旧270格中的23个静态非法格全部被去除，新候选剩242格且没有静态非法格；额外保守去除5格来自方向交集。回归证据在 `outputs/arena-next-20261113/data-static-geometry-audit-v1.json`，8项输入隔离/哈希绑定/嵌入重建测试记录在 `data-static-geometry-tests-v1.log`。旧投影 NPZ 和索引保留，必须另建目录重新投影后再训练。

```bash
.venv-cloud/bin/python -m arena_lab.visual_geometry --output outputs/my-geometry-review.json
.venv-cloud/bin/python -m unittest tests_cloud.test_arena_pixel_policy -v
```

资产文件 SHA256 为 `7e791a4c999090a95457542f008f20fd2a6256ae12efab98271743adb55e3b51`。独立重复导出的规范化内容一致；导出命令拒绝覆盖已有资产。
