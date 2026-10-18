# 从零操作原生战斗研究平台

**第一次体验，现在先运行 `run_play.ps1`，按 [人机演示入门教程](PLAY_GUIDE_ZH.md) 操作。** 新入口把“你在下方手动出牌 → 上方 AI 读取新状态并响应 → 暂停看输入、理由与执行反馈”放在一个页面，默认 Python 规则，提供教学回合和异步实时画面。本文继续作为完整研究观察台、来源回放与历史基线参考；新入口的实测范围见入门教程末节。

更新：2026-10-18。本文面向第一次接触项目的同学。目标是先观察一场对局的输入、状态、画面和结果，再替换 AI 逻辑。策略强弱不是本教程的验收目标。

完整研究观察台入口为 `run_platform.ps1`，地址为 `http://127.0.0.1:8770`。Archive、Replay、保存模型 Agent 和自定义 oracle 插件均已有实际运行证据；第 12 节逐项记录版本、完整局/短程范围及残留问题，命令清单本身不代替运行证明。第一次手动体验优先使用上方 `run_play.ps1` 入门入口。

新增的 `battlefield` 输入把当前采集到的战场实体与已发生下牌历史交给自定义策略。默认 `InformationMode=auto`：有 `PolicyFile` 时选择 `battlefield`，使用旧 BC 时仍选择 `player`。第 3、5.5 节说明实际输入和新示例；新增能力的原生短程运行证据见第 12.1 节。

**当前推荐阅读顺序：第 3 节理解输入 → 5.1 检查环境 → 5.2 观察已有画面 → 5.3 运行来源重建 → 5.5 运行读取单位和下牌历史的新策略 → 5.6 退出。** 第 5.4 节是保留的旧 BC 基线演示，可以选读；只执行其中不带 `PolicyFile` 的 Agent 命令，旧模型仍不会使用非塔实体和下牌历史。新策略的本次验证为短程闭环通过，尚无新的完整局评估。

每次切换 Archive、Replay、Agent 或重新启动命令后，请刷新浏览器页面，再操作按钮。同一时间只运行一个控制台。

上次操作验收曾保留 8770 的 Archive 观察台并关闭虚拟机；当前是否仍在运行，请以页面和 Doctor 为准。若观察台已退出，再按第 5 节启动。完整操作验收见 `outputs/platform_next/platform_acceptance.json`，其中 `complete_all_official_interactions=false`，核心操作通过不代表所有官方交互完成。

第一次使用先读第 1–4 节理解范围，再跟第 5 节动手。若今天只准备一个 15 分钟展示：

1. Archive 里播放/暂停/拖动时间轴，展示“双方信息、全部实体、完整原始 JSON”。
2. 关闭 Archive，启动 Replay，单步 1 tick、推进 20 tick，指认一条原生消费事件。
3. 关闭 Replay，按第 5.5 节启动 battlefield Agent，展开实际策略输入与动作。先完成短程观察；若继续到终局，应把这次新结果单独保存。
4. 打开对应新 session 的 `report.json`，说明请求、消费、自然终局与未验证项。
5. 结束控制台，再执行 Stop。演示时不要临时重新训练或并发跑批次。

## 1. 先回答：距离完善平台还有多远

已有平台能在本机固定版本原生程序里创建战斗、实际下牌、推进、结束，能读取一批原生状态，保存真实回放重建与模型执行证据，并显示原生游戏场景。第一阶段近似模拟器仍保留。

你希望的“先把平台搭好，再研究 AI”很合理。目前主要差距集中在环境、遥测、动作和回放质量，而不是网络规模：

| 能力 | 现在可以做到 | 尚需补齐 |
|---|---|---|
| 看到研究信息 | 读取 `cr-minimal.v4` 当前暴露的全部双方玩家、实体和消费事件 | 完整原生内存、每次攻击/伤害归因、完整投射物与状态效果参数 |
| AI 获得观测 | 旧 BC 使用保守玩家观测；自定义策略可选含实体与已发生事件的 battlefield，或完整已采集 oracle | 隐身/地下/特殊形态可见性核验、完整交互因果和与玩家视角等价的证明 |
| 实际下牌 | 原生适配器可登记卡牌与技能请求，读取反馈 | 全卡牌落点合法性、通用技能消费确认、全动作 Agent 合约 |
| 重建真实回放 | 固定来源转换、初始化假设与日志，保留全部失败 | 来源版本/种子/完整初始牌序缺失，部分等级与技能无法严格复现 |
| 看游戏画面 | 配套原生场景、保存帧和录屏 | 完整官方在线客户端体验、所有机制的独立官方时序对照 |
| 批量训练 | 单实例队列、失败恢复、轨迹筛选、最小 BC 和真实模型局 | 多实例隔离、完整状态快照与快速分支搜索 |

“暴露全部现有信息”可以通过观察台完成；“知道游戏内部一切、准确恢复任意官方对局”仍需下一阶段的原生遥测与参考数据工作。观察台必须把未知标出来。看到血量下降，只能确定这两次采样之间发生了变化，不能自动断言是哪一次攻击造成的。

机器可读审计在 `outputs/platform_next/audit/platform_gap_audit.json`。其中的结论来自当前源码和保存日志的重新读取，不把新页面出现当成新增物理机制验证。

## 2. 整个系统怎样工作

```text
Windows：统一入口、Python、浏览器观察台
                  │ 本机控制通信
项目内 QEMU Ubuntu 虚拟机
                  │
Redroid Android 容器 + ARM 翻译层
                  │
固定游戏 libg.so + 固定资源 + 原生 probe
                  │
战斗状态、CardPlay 消费事件、原生 framebuffer
```

原生 `libg.so` 负责战斗计算。Python 负责配置、时间推进、动作调度、观测投影、策略与记录。游戏画面由配套原生 renderer 生成。本轮已验证场景可显示战场、手牌与圣水 HUD；旁路面板提供精确数值和额外研究字段，尚未核验所有菜单、模式与 UI。这里不需要官方账号或官方在线天梯。

当前 BC 模型和 Python 插件读取**结构化观测**，不从截图识别手牌或单位。原生画面用于人类观看和调试，状态日志直接驱动策略输入。以后研究“只看截图”的视觉策略，需要单独设计视觉感知、时序对齐与评估；它是独立研究方向。

当前客户端是 `nullsroyale.rel.free` build **15.535.13**、content **15.535.86**。每秒 20 个原生 tick，一个 tick 是 50ms。普通回放首个可操作采样常为 tick90；这属于本地重建初始化约定。

来源回放没有可靠的官方 build、平衡日期、隐藏 seed 和完整初始牌序。这些仍是 `unknown`。固定本地版本可以稳定复现同一个本地实验，但不等于证明它与任何官方版本全部交互相同。

控制链的一个循环：

```text
取得动作前状态
  → 给策略选定观测
  → 策略返回 WAIT 或 卡牌/牌槽/坐标
  → 验证允许范围并登记请求
  → 原生引擎推进指定 tick
  → 读取原生消费事件和新状态
  → 记录反馈、画面和终局
```

必须分清三件事：模型输出建议、原生调度器接收/注入、游戏实际消费卡牌。第三件事才是可靠的下牌执行证据。

## 3. 三种输入与 auto 怎样选择

研究者排查问题时，需要尽量看到引擎信息；研究公平玩家策略时，需要限定玩家当时能知道的信息。两者可以同时存在，但必须分开记录。

### 3.1 Research oracle：研究调试视图

它可以展示 probe 当前返回的全部数据，包括对手手牌、精确圣水和轮转。这适合调试规则、检查动作是否执行、设计带全状态信息的实验。若把这类信息输入策略，实验必须明确标记为 oracle 策略。

当前原生快照包含：

| 区域 | 字段 | 如何读 |
|---|---|---|
| 对局 | generation、tick、finalized、result、crowns | generation 是本次 reset 身份；result 终局前为 null |
| 双方玩家 | owner、elixir、hand、cycle、abilities、evolution | 原生 owner 是 0/1，不等于所有来源文件中的 team 顺序 |
| 当前手牌 | slot、root card、cost、packed parameter、effective command card | 前三项可供界面解释；packed 值是原生命令编码，不是普通卡牌特征 |
| 技能 | 按钮状态、冷却、次数、相关单位 ID | 看见 controller 变化不等于已确认某次技能消费 |
| 进化 | 牌组槽、卡牌、能否进化、所需/剩余轮转、ready | 保留原始值，不从标题猜形态 |
| 实体 | ID、owner、card、x、y、HP、maxHP、target、attack cooldown、kind、deploy remaining | 单位、塔、投射物和区域效果共用最小列表 |
| 消费事件 | sequence、tick、owner、effective card ID、cost | 这是原生 CardPlay 消费事件，不是完整攻击/伤害事件流 |

原生实体每行 11 项：

```text
[id, owner, card, x, y, hp, max_hp, target_id,
 attack_cooldown_ms, kind, deployment_remaining_ms]
```

读数时注意：

- 圣水原始值 `10000` = 1 费。例如 `76020` = 7.602 费。
- 坐标 x 为 `0..17999`，y 为 `0..31999`，来源转换使用每格 1000 单位。不要用肉眼猜上下翻转规则。
- 冷却和部署剩余被向上取整到 50ms，异常范围返回 null。null 表示未知/不适用，不能当零。
- `abilities=[]` 或 `evolution=[]` 不能单独证明没有技能/进化：minimal 没有一并导出底层 runtime validity flags。技能按钮的原始数字也尚未完整绑定语义。
- `kind` 是粗分类。最小 probe 把非投射物、有 HP 且 card=-1 的对象叫 building，其他有 HP 对象叫 troop；它并非完整单位原型分类表。
- 生成子单位可能沿用父卡 root，部分有效形态 ID 为 0。不能把 `card` 一列当成所有单位的唯一真实种类。
- 快照间出现 HP 变化、对象消失、目标改变，只是观测到的变化。没有相应原生事件时，不能制造攻击归因、击杀者或技能成功标签。
- 当前 minimal capture 对对象数大于 256、错误 generation、事件游标丢失等情况明确拒绝；失败不是“场上没有单位”。

### 3.2 player：旧 BC 的保守输入

现有 BC 模型只接收：当前 tick、己方牌槽/卡牌/费用、己方精确圣水、皇冠、通过 reset 身份核验的六塔血量。它没有接收非塔单位、对手手牌、精确圣水或完整轮转。

这个接口有意保守，所以比人类看到的信息少。目前不能因为研究调试面板看见了野猪，就认为现有模型也看见了野猪。

新自定义策略已有下节的战场输入。研究公平玩家策略仍需补可见性核验；现有三种输入不能仅按字段数量比较：

1. oracle 策略直接使用当前全部暴露状态，包含对手私有信息，必须独立标记。
2. battlefield 策略使用己方 HUD、采集到的非塔实体与真实下牌历史，但对象可见性尚未核验，仍属于研究输入。
3. 经过独立可见性核验的公平玩家观测仍是后续工作。

改变观测模式后应建立新的 schema、数据和模型，避免把旧 304 维模型直接接上不兼容字段。

### 3.3 battlefield：给新规则使用的战场研究输入

`royale-platform-battlefield-observation.v1` 包含己方手牌、圣水、皇冠和已核验塔信息，加上：

- `entities`：当前采集到的非塔对象，含原生 ID、owner、card raw、位置、HP、粗分类和部署剩余。塔身份没有核验时不会猜测删除对象，查 `field_coverage.may_include_unattested_towers`。
- `history`：只累计本代、当前 tick 及以前实际观察到的原生下牌消费事件。来源未来 journal 不会变成事件；看见未来登记请求不代表它已经发生。
- `requested_position`：只有同 owner、同 tick 的原生事件与 journal 请求都唯一，且 card ID 精确一致，才附加请求坐标。缺失、歧义、形态 ID 不同都保留 null 和原因。

`requested_position` 是送给引擎的请求坐标；`actual_position` 目前始终为 null。它不是引擎调整后的最终落点，也不能拿来推断实际命中位置。实体采样位置是另一个字段，不能直接充当这张牌的最终部署证明。

该输入不传对手手牌、精确圣水、私有轮转、raw capture、攻击目标/冷却或未来回放动作。每个对象的 `visibility=unknown`，`visibility_fairness_verified=false`：尚未验证隐身、地下等对象对人类是否可见，因此不能称为公平玩家输入，也不能把它直接标成已验证训练真值。

### 3.4 以实际解析结果为准

| 启动选择 | 自定义 `PolicyFile` | 保存的旧 BC |
|---|---|---|
| `auto`，默认 | `battlefield` | `player` |
| `player` | 保守 actor | 支持 |
| `battlefield` | 战场实体与已发生消费历史 | 拒绝；旧模型不支持此特征 |
| `oracle` | 全部已采集研究字段，含双方私有信息 | 拒绝；需要自定义策略 |

页面研究面板显示的信息和策略真正收到的信息是两件事。确认实验身份里的 `information_mode`，再检查最新决策的 `policy_information_mode` 与 `policy_input.schema`，不要只依据启动命令中的 `auto` 或画面上看见了什么判断。

## 4. 你要观察三种不同的运行

| 模式 | 在做什么 | 能证明什么 |
|---|---|---|
| Archive 历史证据 | 读取已经保存的状态、事件和原生截图；视频在输出目录另行打开 | 历史某次实验确实留下了这些内容；无需 VM |
| Replay 来源重建 | 把来源动作按固定规则重新送入原生引擎 | 本次本地版本对这些输入的执行与结果；不是自主 AI |
| Agent 模型运行 | 当前策略根据观测决定动作，原生引擎实际执行 | 策略—动作—原生反馈—终局闭环；不能用来源录像代替 |

来源重建的未来动作属于回放控制器输入，不应送进公平策略。Agent 新局必须没有嵌入来源未来 journal。画面和日志都应该能告诉你当前处于哪种模式。

## 5. 统一入口操作

以下步骤针对当前这台已准备好运行时与资源的电脑。发给同学的理解 ZIP 没有 APK、资源、虚拟机、环境或密钥，从 ZIP 解压到另一台空电脑不能直接执行。空机器需要先按版本/来源清单取得依赖并重新验证。

### 5.1 准备两个 PowerShell 窗口

打开终端 A，在第一行执行：

```powershell
Set-Location -LiteralPath 'D:\研究生\Clash Royal'
.\run_platform.ps1 -Mode Doctor
```

Doctor 只读检查本项目进程、端口和原生运行状态，不启动 VM，也不替代全部依赖与硬验收。终端 B 也切换到项目目录，用来检查与停止；运行画面的终端 A 不用关闭。

若上次已执行 Stop，第一次看到 `status=stopped` 是正常状态；当前状态以本次输出为准。

所有运行一次只允许一个控制器。不要同时启动旧 viewer、replay、batch 或 evaluate，也不要在活跃对局中运行另一个 reset。原生 RPC 共用 26789；控制台默认使用 8770。

### 5.2 第一遍先看 Archive，不启动虚拟机

终端 A 执行：

```powershell
.\run_platform.ps1 -Mode Archive
```

如果 8770 上已经有交付保留的 Archive，直接访问即可，不必再次运行相同命令。终端会打印 URL；请手动在浏览器访问 `http://127.0.0.1:8770`，入口不自动打开浏览器。Archive 读取已保存的 F 原生回放证据：181 帧与对应 raw state，从 tick90 到 tick3648。默认暂停。准备启动 Replay 前，先点“关闭本次控制”退出旧 Archive。

按这个顺序看：

1. 左边确认是原生游戏场景；右边确认 mode=archive 与来源文件夹。
2. 点“播放已有画面”，观察 tick、双方圣水、手牌和单位列表变化。前端每 500ms 请求下一条采样，播放速度不等于原始实时速度。
3. 点“暂停已有画面”，再点“下一帧”，也可以拖时间轴。Archive 只能跳到下一条保存采样；观察 tick 的真实变化，它不能补出未保存的中间帧。
4. 在“双方信息”页看双方手牌/cycle；“战场输入”可查看从保存采样重建的 battlefield。Archive 可能展示重建的 actor，但没有运行 AI：其策略身份为 `saved_evidence_only` / `no_policy_execution`，决策列表为空。不要把重建输入当成已经被某个模型使用。
5. 在实体列表找到 troop/projectile/area，查看位置和 HP。projectile/area 的 HP 通常为 null，不能按零血量理解。
6. 展开 raw JSON，与本教程的 11 项实体布局对应。
7. 在“下牌与决策”页看已累计的原生消费数组，核对 `[sequence,tick,owner,effective_card_id,cost]`。Archive 不重新调用原生反馈，反馈/决策列表为空正常；历史详细请求反馈去原始 episode 目录读取。

Archive 既不是新运行的对局，也不是 AI 自主下牌。它是最低成本学习界面和核对证据的方法。

在终端 A 按 `Ctrl+C` 退出 HTTP 服务，再进入下一种模式。浏览器旧页停止更新属于正常现象。

### 5.3 第二遍运行 Replay，亲自控制一场来源重建

终端 A 执行：

```powershell
.\run_platform.ps1 -Mode Replay -Speed 1 -Quantum 20
```

入口先准备/恢复项目内隔离原生运行时，再开启观察台。当前每次新的 Replay/Agent 会话在配置战斗前都会重启项目内 `phase2-android` 容器、等待 Android boot/ADB 并重新启动应用，以清理连续渲染场景产生的 Surface 资源。上一场已结束的 Android 局势会丢弃；版本资源、虚拟机磁盘和宿主保存的日志保留。

启动可能需要几分钟，待终端打印 URL 后手动访问；页面显示“可观察原生状态”才表示本次对局准备完成。默认暂停；此时一场新的本地来源重建已经 reset，Source replay 控制器掌握来源动作。Doctor 显示原生服务 ready 只是运行时状态，不代表随后新场景已经创建成功。

逐步操作：

1. 先保存/记下 session 输出目录、source episode ID、版本指纹、generation 和初始 tick。
2. 点“+1 tick”，确认 tick 精确增加 1；画面可能短暂不变，因为 50ms 内不一定有明显动画。
3. 在数值框填入 20，点“推进这些 tick”，相当于最多 1 秒模拟时间；如果到达自然终局可能提前停止。
4. 点“开始 / 继续”，确认来源下牌不断进入日志；点“暂停”，观察 tick 不再前进。
5. 点“保存截图”，把当前真实 framebuffer 与语义状态绑定保存。
6. 点“刷新下牌反馈”，区分 requested、injected、consumed、unknown。
7. 最后点“保存报告”，到 session 目录查看状态、动作与截图；让它自然终局后保存的报告更完整。

`Quantum` 控制每次自动推进多少 tick，`Speed` 控制播放节奏。速度不会改变原生战斗规则；较大的 Quantum 会让你跳过中间观测快照。先用 20，检查具体交互时暂停后用 1。`Speed 0` 取消用于展示节奏的等待，适合快速推进；不等于引擎使用零时间。

如来源动作注入或消费不完整，保留 failure/unknown 并打开对应日志。画面能显示一整局不代表每条来源动作都已经确认，也不代表来源官方轨迹逐帧一致。

### 5.4 可选：运行保留的旧 BC 基线

先结束 Replay 控制台，再在终端 A 执行：

```powershell
.\run_platform.ps1 -Mode Agent -Seed 20002 -Speed 1 -Quantum 20
```

默认加载现有保存的 BC 展示模型，`InformationMode=auto` 在这里解析为 `player`。它同样会先重启项目内 Android 容器准备新场景，观察台依旧默认暂停。它是一场新建局，没有来源未来动作。画面由当前模型与声明的启发式对手实际动作驱动。

依次看：

1. 记录 checkpoint、seed、model owner、对手控制器和 observation mode。
2. 查看实验身份 `information_mode=player`，再展开保守观测，确认旧模型收到的是 conservative actor。页面“战场输入”可以同时展示更多研究数据，但旧 BC 不会自动使用它。
3. 单步或推进至一次模型 decision，在“下牌与决策”找到 `policy_input`、`actor_mask`、`model_action` 和计划坐标。AI 每 20 tick 决策一次；单步不是每 tick 都重新决策。
4. 推进后打开反馈，确认这次请求有没有对应真实 CardPlay。WAIT 没有 CardPlay 是正常的。
5. 开始运行到自然终局，查看原生 winner owner，再与 model owner 比较。
6. 保存报告，检查本次模型请求/注入/消费数、unknown/rejection、终局或 watchdog。

演示目的：证明“状态 → 你的策略 → 实际下牌 → 新状态 → 终局”可观测。旧模型只支持 WAIT/火球/万箭；用掉两张法术后可能因其他六张牌无法由它打出而 WAIT 锁定。这个行为不是显示故障。

希望加载明确模型时：

```powershell
.\run_platform.ps1 -Mode Agent `
  -Checkpoint 'outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json' `
  -Seed 20002 -Speed 1 -Quantum 20
```

需要把这场新的 Replay/Agent 留成真实 Android 录屏时，启动时增加 `-Record`：

```powershell
.\run_platform.ps1 -Mode Agent -Seed 20002 -Record
```

录屏从本次准备完成后开始，用 Android `screenrecord` 保存 `part-001.mp4` 等分段，并在推进时约每 165 秒墙钟轮换。无论是否加 Record，每次采样的原生 PNG 都会保存。录屏应在自然终局或正常关闭控制台后查看 `recording.json`，确认分段 `valid_mp4`、SHA、起止 tick 与 error；录制失败不能当作成功视频。

MP4 长度是录像墙钟，tick 是模拟时钟。暂停、截图、传输与页面操作会增加录像时长；`Speed 0` 或快放可能让原生时间推进更快。因此不能按“视频 1 秒 = 游戏 1 秒”做训练时序。准确对齐使用每帧/每段的原生 tick 与状态索引。

### 5.5 用自己的 Python 规则替换模型

推荐从会读取 `entities` 和 `history` 作出反应的 `examples/platform_battlefield_policy.py` 开始。它演示如何接入战场输入，不代表强策略或完整合法性证明。对外函数为 `decide(observation)`；先复制到新的项目内文件再修改，以便保留示例。

退出上一控制台后运行：

```powershell
.\run_platform.ps1 -Mode Agent -PolicyFile examples/platform_battlefield_policy.py -InformationMode battlefield -Speed 1 -Quantum 20
```

`-InformationMode battlefield` 可以省略，因为自定义策略的默认 `auto` 会选择它。初学时先显式写出，便于讨论时指明输入。

按这五步确认“策略真的拿到了战场信息”：

1. 刷新浏览器，查看模式为 Agent，实验身份中的插件路径正确、`information_mode=battlefield`。
2. 在“战场输入”页看 `schema=royale-platform-battlefield-observation.v1`、当前 tick、`entities` 和 `history`。开局为空可能正常，先推进至对手实际出牌。
3. 单步或推进到下一次决策。在“下牌与决策”看 `policy_information_mode=battlefield` 和 `policy_input` 中的实体、历史，再看 `model_action`。这里是本次策略收到的快照；页面当前状态可能已经更晚。
4. 继续推进、刷新反馈，核对请求与真实消费。策略有输入、有建议，不代表每张牌都成功消费。
5. 到本次 session 检查 `battlefield_observations.jsonl`、`decisions.jsonl` 和独立审计报告，使用 tick 对齐输入、动作与后果。不要拿旧 BC 的历史终局证明新插件已经跑通。

所有新的 Replay/Agent 会话都会保存 `battlefield_observations.jsonl`；保存这个文件不等于正在用它选动作。Replay 只按来源重放，完全没有自主 AI 决策；旧 BC 的实际输入仍是 player。

保守输入的简单规则仍可使用旧示例：

```powershell
.\run_platform.ps1 -Mode Agent `
  -PolicyFile 'examples/platform_policy.py' `
  -InformationMode player -Seed 20002
```

这条显式 `player` 命令给旧示例保守 actor；它不是自定义插件的新默认。若本次明确要试“拿到所有已暴露信息”的研究策略：

```powershell
.\run_platform.ps1 -Mode Agent `
  -PolicyFile 'examples/platform_policy.py' `
  -InformationMode oracle -Seed 20002
```

`oracle` 模式给自定义策略 `royale-platform-oracle-observation.v1` 研究观测，包含当前暴露的原生信息；它不改变底层探针覆盖。注意 oracle 的 `players[*].hand` 使用 `card_id`、`cost_elixir` 等命名列，不能与旧 actor 的 `hand[*].card`、`cost` 混用。观察台和日志应明确标记它使用了对手私有信息。旧 BC 模型不因为界面打开 oracle 就自动获得这些新特征。

本轮研究插件可返回任意当前手牌槽位的 `play_card`，先检查当前槽位、费用和坐标边界，再交给原生引擎执行。它比旧 BC 的两法术输出范围更广；这不等于全部卡牌落点已证明合法。原生拒绝/未消费必须保留，技能动作尚未纳入插件。自定义策略与当前模型的动作合约请以实际示例和验证器为准；不要在研究函数里直接连接 RPC 或 reset 引擎。

### 5.6 正确退出和关闭运行时

在终端 A 按 `Ctrl+C` 或使用页面“关闭本次控制”退出观察台。虚拟机运行时通常保留；下一次新 live 会话仍会冷恢复 Android 容器，不会把上局内存当成新局。

如果今天不再使用，在终端 B 执行：

```powershell
.\run_platform.ps1 -Mode Stop
```

Stop 关闭本项目的运行时与转发，不应杀死其他无关程序。最终默认 Stop 已实际完成优雅停机：无强制 VM stop，QEMU/隧道与4个项目端口全部关闭。若提示服务被其他进程使用，先确认是否仍有控制台/批次活动，再处理；不要用广泛的 Python/QEMU kill 代替所有权检查。

早期实测遇到过 guest 优雅 shutdown 超时，现已修复。默认停机只在客体 `/run` 加入本次启动期间有效的更新服务停止时限；发现 apt/dpkg 正在工作会拒绝处理，宿主全局配置不变。历史失败和显式恢复都保留，不改写为默认成功。

如果后续仍出现 timeout，`-Mode Stop` 默认不会悄悄强杀，不能认为已经停机。先保存报告、关闭全部原生控制器，确认是本项目 VM，再显式使用恢复入口：

```powershell
.\.venv-phase2-core\Scripts\python.exe -B tools/phase2_platform_runtime.py down --force
```

该入口只会在身份复核和优雅等待后结束本项目 VM，可能丢失尚未同步的 guest 写入，所以用于卡死恢复。宿主已经落盘的 session 证据保留。最后再运行 Doctor，以 `status=stopped` 与进程/监听检查为准；不要仅凭某一次 TCP connect 结果判断所有权。

Replay 和 Agent 的新运行结果在：

```text
outputs/platform_next/sessions/<时间戳>-<replay|agent>/
```

Archive 直接显示它读取的历史文件夹，不新建一局运行证据。Replay/Agent 每次新目录保留本次配置、日志、状态、原生帧和报告，历史 `outputs/phase2` 验收材料保持不变。

| Replay/Agent 文件 | 怎样用 |
|---|---|
| `configuration.json` | 启动参数、实际 Python 路径与关键实现 SHA |
| `initialization.json` / 回放配置材料 | 本局如何初始化；是否夹具、seed 和来源身份 |
| `wire.jsonl` | 原始 RPC 命令与响应，用来排查注入/通信边界 |
| `states.jsonl` | 当前原生 raw_capture 与规范化语义证据 |
| `observations.jsonl` | 命名后的全部 oracle 字段与相邻采样变化 |
| `battlefield_observations.jsonl` | 逐采样战场研究输入：己方信息、实体、仅已发生消费历史；不代表策略一定使用它 |
| `decisions.jsonl` | Agent 输入、mask、选择与登记；Replay 没有模型决策 |
| `execution_feedback.json` | 点击刷新反馈/保存报告/结束时取得的执行确认 |
| `controls.jsonl` | 页面控制的前后 tick 与状态 |
| `frames/index.jsonl` 和 PNG | 原生帧 SHA、tick 与语义绑定 |
| `recording.json`、`part-*.mp4` | 仅 `-Record` 时保存的真实 Android 录屏分段、起止 tick 与完整性 |
| `journal.json` | 初始化与已登记动作，供重演和审计 |
| `report.json`、`session.json` | 当前/结束统计与状态；须看自然终局、truncated 和 error |

观察台默认保存逐次 PNG，增加 `-Record` 才尝试本次 MP4 录制；可以直接观看页面，历史 F 视频在旧证据目录中。不要把历史 MP4 当作本次 Agent 新运行的视频。

### 5.7 第一次操作遇到问题怎样定位

| 现象 | 先做什么 |
|---|---|
| 没有自动弹浏览器 | 正常，手动访问终端打印的 URL，保持终端开着 |
| 8770 已被占用 | 回到原来的控制台关闭；需要另一个只读 Archive 时可指定 `-Port 8771`，禁止开第二个 live 原生控制器 |
| 页面一直“正在准备” | 看终端与本次 session 的 `error.log`，检查 `outputs/platform_next/runtime/latest.json`；首次 VM 恢复需要等待 |
| Doctor 显示 inspection_failed | 进程/CIM/端口检查被当前终端权限限制；保留原始 stderr。这不是运行时 ready，先在本机正常 PowerShell 检查报告 |
| PS5.1 报 ConvertFrom-Json 无法识别转义、中文路径乱码 | 更新本项目 `run_platform.ps1` 后重试；新入口明确按 UTF-8 读取 JSON、解码 Python stdout 和写日志。旧 wrapper 可能在 runtime 已 ready 后才读取失败，不必据此重装 VM。先关闭仍占 8770 的旧控制台 |
| 画面暂停、tick 不变 | 默认暂停；点“开始 / 继续”，或精确单步。Archive 要点自己的播放按钮 |
| 单步后没有新的 AI 决策 | AI 固定每 20 tick 决策一次，单步只推进 1 tick；查看下一决策边界 |
| 有模型建议但没有下牌 | 检查 mask、手牌完整性、WAIT、request/injected/consumed。模型预测和消费分开 |
| 游戏结束后停止推进 | 自然终局的正常行为；保存报告，退出后新开一局，不在旧局上继续 step |
| result 是 0/1 | 代表原生胜方 owner；本观察台模型 owner=0，不是“0表示失败”的通用错误码 |
| Stop 等待后提示 shutdown timeout | 早期失败已修复且最终默认停机通过；若再发生，VM不会自动被强杀，按5.6核验后显式恢复，再用Doctor确认 |
| 所有信息里出现 null/unknown | 保留未知，查“字段与缺口”，不要把它解释为零值或功能已确认 |
| 看见战场实体但策略仍只看塔/手牌 | 查看实验身份和最新 `decisions.jsonl` 的实际 `policy_input`；旧 BC 自动使用 player，自定义插件的 auto 才是 battlefield |

需要追踪问题时，给同学提供本次 session 路径、终端完整错误、`report.json`、`controls.jsonl` 以及错误前后少量 raw state。不要只发一张截图，也不要为了让画面继续而修改 HP、删动作或把未知改成成功。

Windows PowerShell 5.1 的 UTF-8 修复已用真实 `powershell.exe 5.1.26100.9711` 做独立回归：旧无编码读取会失败，CP936 原生管道会损坏中文；新入口读取、捕获与 UTF-8 日志回读的路径逐字符一致。这是编码层验证，不替代新场景/新策略的原生运行验收。

可只读检查运行时 JSON，或重跑不访问 VM/RPC 的编码回归：

```powershell
Get-Content -LiteralPath 'outputs/platform_next/runtime/latest.json' -Raw -Encoding UTF8 | ConvertFrom-Json
.\.venv-phase2-core\Scripts\python.exe -B tools/phase2_launcher_encoding_check.py
```

## 6. 在一张卡上看懂全过程

选择一个有真实消费记录的时间点，按下面顺序核对：

1. 看动作前 tick，查看对应 owner 的手牌、当前 cost 和圣水。
2. 看控制器：是 source replay、model，还是 heuristic opponent。
3. 看请求中的 root card、x/y、计划执行 tick、request_id。
4. 看 scheduler 的 queued/injected 状态。`succeeded` 只代表注入成功。
5. 看 `card_consumed`、匹配的原生 CardPlay、`actual_execution_tick`。
6. 看下一状态中的手牌、轮转、圣水，以及新出现的单位/法术对象。
7. 对照同 tick 附近的原生画面。实际截图采样间隔可能大于一 tick，不能把画面缺一帧当成没有发生。

单次 raw `plays` 仅包含该事件游标窗口内的消费；观察台旁路累计的原生消费列表才是本次已观察历史，两者数量可能不同。若消费为 unknown，保持 unknown。若请求失败，查错误、generation 和进程所有者；不要连点同一请求盲目重发。一次通信中断可能发生在动作已被执行之后。

## 7. 如何判断“真实还原”到了哪一级

每局要分开看五个质量维度：

| 字段 | 通过含义 |
|---|---|
| parse_valid | 来源结构能够解析 |
| runtime_replayable | 来源动作都有可解释的实际消费且自然终局 |
| deterministic | 同样输入在多次独立 reset 后，暴露语义序列相同 |
| outcome_consistent | 来源可用胜者、皇冠、塔血等终局字段对照通过 |
| trajectory_validated | 与独立时序参考做过交互过程对照 |

正式 100 局历史分母没有隐藏：100 局可解析，10 局 runtime pass，90 局 fail；63 局 outcome pass、23 fail、13 not_run、1 unknown。这里维度重叠，不能相加。全部 100 局没有独立官方时序参考，`trajectory_validated=not_available`。

90 局失败的主要保存消息：76 局未满足所有消费/自然终局确认，9 局初始牌序校准循环，4 局来源等级有损而拒绝，1 局有界超时。失败中 74 局有未确认技能、13 局有未确认普通下牌，二者重叠；不要把 76 局都归因于技能。

这是后续平台工作的具体清单。未来修复后应新建队列和证据，不回写历史失败为成功。同一个引擎的两次结果相同，只能证明本地重复性；没有独立官方时序真值时不能宣称官方所有交互百分之百一致。

## 8. 如何读 AI 效果

先判断能否正常完成一局，再评价棋力：

1. 保存模型是否加载了预期 checkpoint；自定义规则是否记录了正确的插件路径/SHA 和实际输入模式；版本指纹是否相同。
2. 是否产生真实预测，预测时拿到了什么观测、mask 和决策频率。
3. 有多少 play request、多少 native injected、多少 confirmed consumed。
4. 是否有 unknown、rejected、崩溃、超时或 WAIT 锁定。
5. 是否自然终局，原生胜者 owner 与模型 owner 是否相同。

历史 `outputs/phase2/native/agent_eval/acceptance-008/report.json` 有 10 局自然终局、每局至少一次模型真实消费、合计 14 次模型消费；按照当前 winner 编码是 0 胜 10 负。该模型只支持 WAIT、普通 Fireball、Arrows，使用显式 spell-first 初始夹具和推理先验。它展示真实闭环，不能代表完整卡组策略。

此前观察台的旧 BC 对照已实际从新 Android 容器运行一局 `seed=20002`：`outputs/platform_next/sessions/20261018T041121.705498Z-agent/`。可以直接播放其中的 `part-001.mp4` 先看效果，再逐行看同目录决策与反馈。该局 35 次决策中 1 次万箭、34 次 WAIT，模型 1 次消费、对手 5 次消费，tick785 自然结束、模型输。保存 36 帧，MP4 55.4606 秒。它与历史十局是两份独立证据，也不代表新增 battlefield 规则的结果；后者见第 12.1 节。

原生引擎能计算兵种交互，与现有模型能选哪些动作，是两个不同的覆盖范围。新观察台即使展示更多研究状态，也不会自动改变旧模型权重或动作合约。

看新 battlefield 规则时，先核对决策行里的 `policy_input.entities/history` 与动作是否对应，再用反馈确定真实消费。某次遇到敌人后出牌只能作为该次运行现象；要证明逻辑稳定，应另做固定输入对照和多局评估。新插件的胜负不能沿用上述旧 BC 数字。

## 9. 以后把自己的策略放在哪里

先找到以下模块：

| 模块 | 职责 |
|---|---|
| `royale_phase2/backend.py` | 原生 reset、步进、动作登记、反馈、状态与 journal |
| `royale_phase2/visibility.py` | 保守 actor 投影、塔身份、己方费用预筛选 |
| `royale_phase2/observability.py` | 当前全部已采集 raw 字段的无损研究投影、命名列、采样差异和缺口 |
| `royale_phase2/battlefield.py` | 己方信息、非塔实体与已发生下牌历史；请求坐标附注、未知可见性边界 |
| `royale_phase2/platform.py`、`platform_ui.html` | 新观察台、原生单 worker、插件加载、实时画面与控制 |
| `royale_phase2/agent_env.py` | 观测—动作—推进—reward—终局循环 |
| `royale_phase2/learning.py` | 现有 NumPy BC checkpoint 加载和 `predict` |
| `royale_phase2/trajectories.py` | 数据质量、观测前后顺序、动作子空间和导出 |
| `tools/phase2_evaluate_native.py` | 原生批量模型评估与证据输出 |

第一步可以写一个纯函数，而不训练模型。新观察台的战场规则请从 `examples/platform_battlefield_policy.py` 的 `decide(observation)` 开始；保守输入的简单示例在 `examples/platform_policy.py`。下面是原有 `NativeAgentEnv` 严格接口的返回示例，动作限制与通用研究插件不同：

```python
def choose_action(actor, mask):
    # 当前严格接口只允许 WAIT、普通火球、万箭。
    # 返回值仍需经过 validate_policy_action 检查。
    for card in actor['hand']:
        if card['card'] in (28000000, 28000001) and mask['values'][card['slot'] + 1]:
            return {'kind': 'play_card', 'slot': card['slot'],
                    'card': card['card'], 'x': 3500, 'y': 25500}
    return {'kind': 'wait'}
```

这个例子只说明返回结构，owner=0 的固定落点也不通用，不是建议的战术。接入新策略要替换决策选择处，不要修改引擎 HP 或把结果写回游戏。

研究插件现在可以请求当前手牌中的其他卡牌。如果希望完整八卡组都具有可靠的策略动作支持，应继续补动作合约、准备状态、部署区域、建筑 footprint、技能 readiness 与消费证据，再建立对应 mask。不能仅删除 `ALLOWED_SPELLS` 检查便声称全卡组合法。

若研究 oracle AI，使用 `-InformationMode oracle` 和独立记录，不需要在策略内另连 RPC；保持当前 `observe_visible()` 与旧训练轨迹不变。`battlefield` 已提供发生过的消费历史，但公平玩家 AI 仍应优先补单位可见性和事件覆盖核验，不能直接透传对手私有手牌和精确圣水。

## 10. 平台后续工作顺序

当前最合理的下一步可以按可验证结果排序：

1. **以已有观察入口作为后续基础。** 一处启动、Archive/Replay/Agent 区分、oracle/player/battlefield 对照、同局画面、暂停/单步/关闭、日志定位与 Python 策略替换。新增 battlefield 的原生验证单独留证，再在这个入口上扩充覆盖。
2. 扩充原生遥测：逐次攻击、命中、伤害归因、投射物源/目标/状态、区域效果、技能消费。先逐字段核验，再接策略。
3. 改善回放重建：混合等级、技能证据、初始化缺失的有界推断、明确来源版本，针对每类失败建立小夹具。
4. 完整动作平台：固定八卡组、部署/轮转/能力、局内实际消费，两个 owner 都有正反例。
5. 可比实验平台：新冻结来源、独立参考、单元机制与端到端回放证据、重放恢复与多实例隔离。
6. 在以上合约稳定后，比较人工规则、BC、记忆网络、搜索、自博弈与 RL。

完善平台的验收应问“能看到什么、控制什么、证据是什么、失败怎样恢复”，而不是先追问模型胜率。

## 11. 现有证据和文件位置

| 路径 | 作用 |
|---|---|
| `data/phase2/version_manifest.json` | 固定版本、资源、源码和转换身份锁 |
| `outputs/platform_next/audit/platform_gap_audit.json` | 本次平台缺口、全部现有字段、重新计算的历史统计 |
| `outputs/platform_next/platform_acceptance.json` | 本轮观察与操作平台总验收、实际完成范围、失败与证据 SHA |
| `outputs/phase2/acceptance.json` | 历史 A–I 门的实际验收快照与作用范围 |
| `outputs/phase2/formal_audit_complete.json` | 冻结正式 100 局全部成功/失败与反馈分布 |
| `outputs/phase2/viewer/acceptance-001/` | 历史来源重建的原生画面、帧、raw states、wire 与五锚点 |
| `outputs/phase2/native/agent_eval/acceptance-008/` | 历史真实模型十局的决策、原生反馈、状态和终局 |
| `outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json` | 当前展示模型元数据与依赖的权重身份 |
| `phase2/NATIVE_ENGINE_GUIDE.md` | 原生源码、故障和调试入口 |
| `phase2/TRAJECTORIES_AND_BC.md` | 数据导出、训练和旧模型的完整范围 |
| `outputs/platform_next/checks/console_deliverable.png` | 本轮实际观察台界面截图 |

所有新实验应使用新输出目录，不覆盖这些历史产物。新界面/新策略修改后，重新验证受影响能力，并保留原始失败。

## 12. 实际验证记录：此前平台验收与后续修复

这里只记录确实运行的结果，历史阶段二 F/G 不自动证明新观察台已通过。下表保留此前平台验收；最新 battlefield 与 PS5.1 修复记录见第 12.1 节，旧会话的“最终”仅指当时那一轮验收。

| 项目 | 实际结果 | 证据 |
|---|---|---|
| 缺口审计重新计算 | 6 项通过；输入 SHA、正式分母、质量、反馈、画面统计与 Agent 胜负，RPC=0 | `outputs/platform_next/audit/platform_gap_audit.json` |
| 初版控制台与观测测试 | 当时 33 项测试通过，包括保存证据绑定、路径、HTTP token/Host、插件两种输入与动作边界；这些本地测试不代替原生对局 | `outputs/platform_next/console/final_tests_33.log` |
| 运行时 launcher 自测 | 18项离线自测通过，覆盖VM/隧道身份、端口、路径、单控制器、serial/pin信任与拒绝边界；没有在这些自测中启动VM | `outputs/platform_next/runtime/20261018T042843.375712Z-self-test-49864/report.json` |
| Guest shutdown helper 自测 | 3项通过：systemctl属性解析、时限策略读回、拒绝旧30分钟停止策略；离线测试未修改guest | `outputs/platform_next/runtime/guest_shutdown_offline_selftest.json` |
| 静态与前端刷新 | PowerShell/JS 语法通过，同一画面 SHA 下状态刷新通过；core 环境无 NumPy，Agent 已改用 data 环境 | `outputs/platform_next/console/static_review_20261018.report.json` 和当前 `run_platform.ps1` |
| Windows PowerShell 5.1 编码修复 | 真实 PS5.1.26100.9711 回归 pass：复现旧 JSON 读取失败与 CP936 原生输出损坏；明确 UTF-8 后中文 JSON、Python stdout、日志回读准确，wrapper 语法无错误；测试不启动 VM、不访问 RPC | `outputs/platform_next/fixes/20261018_launcher_encoding/powershell5.report.json`；同目录保留旧 wrapper 与测试日志 |
| Archive 实际 HTTP 操作 | pass，下一采样、暂停稳定、终局 tick3648、PNG SHA 与状态绑定；浏览器实际从 tick90 点到91 | `outputs/platform_next/checks/archive-001/report.json` |
| 原生运行时 up | 实际 ready，已复用正确 VM/app/tunnel；中文项目路径与单次状态连接问题修复后重测 | `outputs/platform_next/runtime/verified_up_fixed.log` |
| 新 Replay 完整画面 | HTTP/控制/画面检查 pass：tick90→3648 自然终局、41 帧、43 原生消费事件。43 个已登记输入中仅 33 个完成严格请求配对，其余 10 个保持未确认；本次运行不记严格训练轨迹通过 | `outputs/platform_next/checks/replay-001/report.json`、`outputs/platform_next/sessions/20261018T035759.956813Z-replay/report.json` |
| 新 Agent 完整画面 | HTTP/控制/画面检查 pass：tick90→785 自然终局、71 帧、35 次决策（34 WAIT、1 PLAY）。模型 1 次万箭实际消费，对手 5 次消费，6 个请求均确认；模型 owner0 负于 owner1 | `outputs/platform_next/checks/agent-001/report.json`、`outputs/platform_next/sessions/20261018T040219.515586Z-agent/report.json` 与 `execution_feedback.json` |
| 新 Agent 真实录屏 | `-Record` 实测有效 MP4，28,017,295 字节、SHA 已记录，start/end tick=90/785，录像墙钟101.4584秒、error=null | 同一 Agent session 的 `recording.json`、`part-001.mp4` |
| 连续新场景失败保留 | 第三次场景初始化120秒超时；仅 force-stop 应用后再次尝试触发 `Android Surface$OutOfResourcesException`。这两次没有通过 custom/oracle 验证 | `outputs/platform_next/sessions/custom-oracle-001/`、`custom-oracle-002/` 及对应 `checks/` |
| Android 容器冷恢复与 oracle 插件 | 恢复后 custom-oracle-003 实际通过短程检查：tick90→190、5次决策（2插件出牌、3等待）、4消费=2插件+2对手、7状态/PNG绑定。按100tick预算主动截断，`truncated=true`，不是完整终局评估 | `outputs/platform_next/checks/custom-oracle-003/report.json`、`artifact_audit.json` 与同名 session；每次新 live 的 `application_restart.json` 保存恢复步骤 |
| 此前冷恢复 BC + Record 对照 | 当时版本实际 pass：新 Android 容器、tick90→785 自然终局、35决策、模型1次消费/对手5次、36帧；有效 MP4 15,605,217字节、55.4606秒覆盖90→785、error=null。模型 owner0 负。逐文件证据与媒体绑定离线审计均 pass | `outputs/platform_next/sessions/20261018T041121.705498Z-agent/`、`checks/agent-final/report.json`、`artifact_audit.json`、`media_audit.json` |
| 初次优雅停机失败保留 | guest 没有在有界等待内完成优雅 shutdown；工具明确 timeout、没有擅自强制杀 VM | `outputs/platform_next/runtime/20261018T041413.777630Z-down-30532/report.json` |
| 显式停机恢复 | 默认优雅停机超时后，实际 `down --force` 已结束核验过的项目 VM/隧道，报告 `status=stopped`、无 VM/隧道和监听；此项不改写默认优雅停机失败 | `outputs/platform_next/runtime/20261018T041701.536550Z-down-34624/report.json` |
| 默认 Stop 修复后实测 | `status=stopped`，无 `forced_vm_stop`；客体有界准备 exit0、runtime-only、15秒停止时限，优雅shutdown exit0，4端口全 false、无项目 VM/隧道/监听 | `outputs/platform_next/checks/final_stop_fixed.log`；客体细节在 `outputs/platform_next/runtime/20261018T042434.575243Z-down-27992/004-guest_bounded_shutdown_prepare.stdout.log` |
| 最终完整冷生命周期 | 真正新启VM与隧道（均未复用）到 `ready/contentReady=true`，随后默认优雅停机 `stopped`，无强制VM stop、4端口全部false。两个命令均exit0。冷启无新serial公钥块时，严格使用已有项目pin认证并核验boot ID，没有放宽SSH检查 | `outputs/platform_next/checks/cold_start_verified.log`、`shutdown_verified.log` |
| SSH 身份与项目内缓存修复 | 固定 `StrictHostKeyChecking=yes`；先用项目内已持久 pin 严格认证并核验 boot ID，失败或无 pin 才从身份核验过的本地 QEMU serial 公钥、指纹与启动时间建立信任。保留旧 known_hosts 备份，不采用网络 keyscan 自动信任 | runtime 报告的 SSH/pinned/local-serial 身份证据，项目内 `known_hosts.before-local-serial-bootstrap` |
| 一次冷启动 host-key 等待失败 | 实际30秒等待内未出现完整本地serial host公钥块，启动明确 timeout；这次没有计 ready | `outputs/platform_next/runtime/20261018T042546.962157Z-up-26972/report.json` |
| 一次项目目录外缓存误写已纠正 | SSH路径处理曾将100字节公开host-key缓存写到上级 `D:\研究生\Clash`。已核验移回项目审计目录，重新读取SHA和公钥类型，确认外部原路径不存在；不包含私钥 | `outputs/platform_next/audit/recovered_accidental_known_hosts.txt`，SHA256=`bdc6be10d5a248880399796e016ff34b4d8022f136a56aaed9a4642052a67532` |

早期沙盒内 loopback/CIM 受限、运行时启动和前端问题均有原始失败日志，修复后需依据实际复测判定，不删除这些记录。

### 12.1 2026-10-18：输入扩充与 PS5.1 启动修复实测

用户反馈的 `ConvertFrom-Json` 错误发生在运行时已经 ready 之后。Windows PowerShell 5.1 用系统默认编码读取 UTF-8 JSON，中文路径损坏后产生非法转义。入口现明确以 UTF-8 读取 JSON、捕获 Python 输出和写日志；原始用户日志保存在 `outputs/platform_next/fixes/20261018_launcher_encoding/user_failure.txt`。无需因此重装虚拟机。

| 验证 | 实际结果 | 证据 |
|---|---|---|
| 本次完整相关回归 | 58/58 通过：原始观测、battlefield、控制台及独立产物审计；包括实体/历史改变能改变新规则动作、向后 seek 不保留未来历史、历史/最终位置/策略输入篡改拒绝、旧模型兼容 | `outputs/platform_next/fixes/20261018_battlefield/final_tests.log` |
| 真实 PS5.1 Replay 入口 | 原命令成功启动；暂停稳定、单步90→91，再推进到400；末态2个非塔实体、3条真实消费历史及3个请求落点附注 | `outputs/platform_next/fixes/20261018_battlefield/replay_ps5_check/report.json` |
| 新 battlefield Agent 原生执行 | 90→390；17条状态、17帧、17条 battlefield；15次决策=4 PLAY+11 WAIT；策略4次、对手3次消费全部确认；末态6个非塔实体、7条消费历史 | `outputs/platform_next/fixes/20261018_battlefield/agent_battlefield_check/report.json`；`outputs/platform_next/sessions/20261018T045703.481049Z-agent/` |
| 两次会话独立文件审计 | 均 pass：wire、raw、实际事件、battlefield、PNG、策略输入、动作及反馈绑定重建通过 | `outputs/platform_next/fixes/20261018_battlefield/replay_new_artifact_audit.json`、`agent_new_artifact_audit.json` |
| 页面脚本本地检查 | 语法及6个合成 DOM 检查通过；同一画面 SHA 下单位表仍更新，Replay 明示无策略输入 | `outputs/platform_next/fixes/20261018_battlefield/ui_synthetic_report.json` |
| 测试环境关闭 | 实际默认 Stop 优雅退出，VM/隧道消失，4个运行时端口关闭 | `outputs/platform_next/runtime/20261018T045925.588404Z-down-26920/report.json` |

这两次新原生会话为**人工有界关闭，均未自然终局**。Replay 只到400，43个预登记来源请求中3个已消费，其余40个尚未到执行时刻；仅比较了起始90锚点，`five_points_equal=false`。不把这次短程回归记成完整回放、整局胜率或严格训练轨迹通过。

本次浏览器权限检查拒绝了自动打开 localhost 页面，因而没有新浏览器视觉验收。HTTP、原生 PNG 和合成 DOM 检查已有证据；请你手动打开页面核对“战场输入”页签。实际原生示例画面是 `outputs/platform_next/sessions/20261018T045703.481049Z-agent/frames/000017-tick000390.png`。

现在可直接重新执行第5.5节命令。默认暂停时点击“开始 / 继续”，或推进100 tick；对局开始后再看“战场输入”的最近一次实际策略输入。修改的函数是 `examples/platform_battlefield_policy.py` 中的 `decide()`。新策略自动使用 battlefield；直接运行不带 PolicyFile 的 Agent 仍加载旧 BC，仍使用 player。此次没有重训旧权重。

尚待补齐：原生最终下牌中心的独立采集、隐身/地下可见性、完整攻击/伤害因果、全动作合法性，以及独立官方时序对照。所有 `actual_position` 仍为 null。坐标能力审计见 `outputs/platform_next/fixes/20261018_battlefield/position_capability_audit.json`；本次汇总见同目录 `fix_acceptance.json`。
