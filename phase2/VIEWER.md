# 本地原生画面、交互控制与完整录像

本工具展示真实 Android 原生客户端 framebuffer，使用固定 `.86` 原生后端计算战斗。代码：`royale_phase2/viewer.py`；入口：`tools/phase2_view.py`。它不实现战斗物理，不调用旧模拟器，不把源码动作改写成训练观测。

## 当前验证状态

工程、只读 Android 能力检查与本地编码器检查已完成；完整回放交互、录像和五锚点比较须在主代理交付的独占窗口实际运行后才记为通过。当前不得据页面存在或录屏帮助命令就标记阶段F通过。

- `outputs/phase2/viewer/doctor-v2/report.json`：实际截图成功；未调用RPC、未重启App、未启动screenrecord。
- Android screenrecord v1.3可执行帮助，单段最大180秒；`dumpsys media.codec` 实际报告服务不存在，因此真实录制能力仍需执行确认。
- `outputs/phase2/viewer/encoder_provenance.json`：只从官方PyPI取得imageio-ffmpeg0.6.0 Windows wheel，SHA/ZIP CRC已核验，项目内FFmpeg7.1实际启动成功，无全局安装。
- `outputs/phase2/viewer/self-check/report.json`：实际源状态的语义保留检查与真实FFmpeg编码通过；其中codec-smoke.mp4仅重复一张真实截图来检查编码器，不是完整回放，也不计入录像验收。

## 运行前提与独占性

使用现有项目内 QEMU-WHPX / Android 运行时及127.0.0.1:26789探针隧道，安装资源必须与 `data/phase2/version_manifest.json` 一致。Linux/ADB/原生APK安装与网络隔离见 `phase2/SECONDARY_AUDIT.md`。

同一个原生进程只能由一个控制器调度。正式批处理、机制探针和viewer不可同时运行。`--exclusive-runtime` 表示启动者已释放其他控制器；参数本身不是全局锁，也不代表外部进程会自动退出。工具的单worker确保网页并发请求不会并行操作RPC；版本/代际或传输失败会停止推进，保存错误，不自动重发有不确定结果的动作。

目前 renderer→headless 需冷重启App。关闭viewer时使用 `--restore-headless` 可完成该操作，但原渲染对局不会跨进程保留；这不是snapshot/restore。首次headless参考必须在headless模式启动。

## 打开可交互画面

从项目根目录执行（所有输出路径必须在项目内，输出运行目录要求尚不存在）：

```powershell
.venv\Scripts\python.exe -X utf8 tools/phase2_view.py serve --exclusive-runtime --record --restore-headless --output outputs/phase2/viewer/manual-run
```

打开 **http://127.0.0.1:8766/**。该地址只监听loopback；CSS/脚本均内置本地，不使用CDN。页面有真实可执行的开始/继续、暂停、单步+1tick、定量推进、截图和暂停稳定性检查。选择native owner0/1后，旁路面板展示该玩家真实的手牌ID/费用/圣水和已确认公开塔血量；它没有伪造原生完整手牌HUD。

网页只返回 `observe_visible(owner)` 的保守白名单缓存，不传敌方手牌/轮转、单位目标和冷却。完整minimal状态只写入独立审计文件以供比对，不作为策略actor输入。此回放预登记了完整未来journal，属于观看/诊断模式，明确生成 **0训练决策样本**。

## 为什么可以准确暂停和单步

初始化使用真实 `episode.json` 对应的 `initialization.json`；完整 `source_actions` 按源顺序在tick90一次预登记，保留owner/cardID/坐标/tick/技能hints。源文件不改写，登记结果保存于 `preregistered_journal.json`。

原生时钟始终pause，网页的“开始”只使本地worker重复 `advance_exact_ticks`，不会开启不受控原生resume。暂停在当前有限步执行结束后生效；默认quantum10tick，即单次最多0.5源秒，加截图/RPC耗时。`--quantum 1..100` 可调响应/吞吐折中。单步固定+1tick；定量推进精确到请求目标或自然终局，并在锚点停靠保存完整状态。`pause_check` 在真实原生层两次读取minimal并间隔0.35秒，检查所有规范化语义稳定。

`--speed 1`请求按20tick/秒节奏推进，截图/编码开销可能使实际更慢；`--speed 0`为尽可能快的有限步推进。录像墙钟、原生tick和frame索引分别记录，不声称二者相同。

## 实际HTTP控制、完整录像与五锚点验收

主代理授予独占窗口后运行：

```powershell
.venv\Scripts\python.exe -X utf8 tools/phase2_view.py exercise --exclusive-runtime --restore-headless --output outputs/phase2/viewer/acceptance-001
```

该命令先以同初始化/完整journal独立运行headless，再创建真正renderer并启动本地HTTP服务，通过真实HTTP接口执行单步、定量推进、开始、暂停、检查暂停和继续，最终推进到自然终局。全过程保存原始RPC、控制回执、完整minimal状态、frame PNG、tick/墙钟索引和录像。最后关闭RPC并按参数冷重启恢复headless。

默认真实数据：`outputs/phase2/replays/development/ordinary-feedback-fixed/episode.json`，source episode `85c0a48b6a1d47cc8057710e05bf7773`，43条动作，已执行原生终局tick3648。验收锚点固定 **90、1000、2000、3000、3648**。缺少锚点或语义差异均判失败，不只比较胜负、截图或HP。

比较仅去除generation标识、平移单调事件序列原点；不同采样窗口的plays还原为完整已观察事件流。其余minimal字段全部保留，包括每个实体ID/血量/坐标/目标/冷却、手牌/轮转/packed参数、进化数据、结果与全部事件顺序。原始capture也单独保留。未暴露的原生内部内存仍未知，五锚点通过不能等同逐帧完整内存相等或与官方独立轨迹完全相同。

`anchor_comparison.json`列出五点的完整语义SHA和首批逐字段差异路径；输入初始化、journal或版本不同会拒绝比较。

## 录像与可恢复材料

优先启动实际Android screenrecord，每段180秒上限前165秒轮换；保留每段起止tick、实际MP4 SHA、容器/时长检查及编码器日志。每段停止使用其自己的PID，不修改对局RPC。录像包括交互暂停，可能随请求速度快放或慢放。

无论录屏是否成功，每次受控推进都会保存未修改的原生PNG到 `rendered/frames/`，`index.jsonl`记录源tick、真实墙钟、frameSHA和对应完整minimal语义SHA。因此编码器失败可恢复，不能把黑屏或空MP4当成功录像。自动验收会尝试使用已准备的项目内FFmpeg对这些原始帧按实测采集间隔编码，只有实际有效视频并覆盖完整源区间才通过录像检查。

手动重编码：

```powershell
.venv\Scripts\python.exe -X utf8 tools/phase2_view.py prepare-encoder
.venv\Scripts\python.exe -X utf8 tools/phase2_view.py encode-frames --frames outputs/phase2/viewer/acceptance-001/rendered/frames --ffmpeg .cache/phase2/viewer/encoder/ffmpeg-win-x86_64-v7.1.exe --output outputs/phase2/viewer/acceptance-001/rendered/native-full-match.mp4
```

编码只组合真实原生帧，不绘制替代竞技场，不插入推测战斗状态；frame采样并非完整20fps，应依据索引评估运动连续性。视频、帧与headless比较是不同证据，不能互相替代。

## 文件定位

| 文件 | 用途 |
|---|---|
| `headless/anchors.json`、`rendered/anchors.json` | 五个完整minimal规范化锚点及原始capture |
| `*/wire.jsonl`、`*/states.jsonl` | 原始RPC和完整最小状态/事件 |
| `rendered/controls.jsonl` | 每个真实控制操作前后tick、耗时、暂停检查 |
| `rendered/frames/index.jsonl` | 可恢复原生帧、tick、墙钟、语义SHA索引 |
| `rendered/recording.json` | 实际录屏分段及失败原因/回退信息 |
| `rendered/encoding.json` | 回退编码命令和退出码 |
| `anchor_comparison.json` | 五点逐字段相等性与差异路径 |
| `report.json` | 交互、视频、自然终局和完整五点验收结果 |

缓存中的编码器是单独授权组件（该FFmpeg构建启用GPL）；保留来源与许可证，公共源码分享包排除 `.cache/phase2/viewer` 二进制。原生APK、资源与VM私钥的排除规则沿用阶段二审计，不因加入viewer而变化。
