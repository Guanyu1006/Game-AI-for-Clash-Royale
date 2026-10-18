# 第二阶段原生候选与实际运行审计

更新：2026-10-15。以下将作者说明、源码调用链、实际下载与本机运行结果分开。第一阶段的C++近似引擎不参与这些原生探针，也不计入第二阶段运行成功。

## 固定来源与当前取得状态

| 候选 | 固定版本、取得物 | 实际战斗位置 | 当前运行证据 |
|---|---|---|---|
| Jason-XII/clash-royale-battle-engine | `70a08af6807fa4d59a11c7d52bf6272a96afe30c`；66文件，ZIP CRC通过，含ARM64预编译probe | `engine.py`通过TCP驱动APK内`libg.so`；`clapha.py`依赖另一套未取得的Linux loader | Android NativeBridge实际加载；base及精确.86各一局真实64次消费并终局；原生renderer已创建/消费，画面独立验证中 |
| FirstLight_CR | GitLab `71944e3993b594b3cc89b38768b5e817dfbdbe0e`；290文件/6.68MB，CRC通过；明确排除checkpoints和docs/assets | `native_runner/probe/cr_replay_probe.cpp`调用原生GameStateManager/命令队列/step；render模式交给GameApp原UI | 通过Jason的固定vendor minimal预编译probe实际调用同一原生库；full/rich/snapshot不能由源码存在认定通过 |
| clapha-engine | 原精确链接`arronwang01/clapha-engine` API404；搜索/forks/用户目录/history已实际调查 | Jason调用`crx/loader.py`、`env.CrxEnv`，声称x86_64 Linux加载原libg | 未取得loader或x86_64游戏库；不能把Jason接口文件当成可运行引擎 |

源码路径分别是`third_party/phase2/jason`、`third_party/phase2/firstlight`。两者`ACQUISITION.json`保存归档SHA、固定commit、逐文件SHA。FirstLight仓库声明Apache-2.0；Jason根目录没有确认到独立LICENSE，其`vendor/firstlight/LICENSE/NOTICE`仅能支持对应vendor范围的来源说明。原生游戏库和游戏资源的权利不由封装源码许可证授予，APK/资源仅保留项目内研究副本，分享包应排除它们。

最初GitHub search精确clapha-engine结果0；随后查询作者公开8仓库、Jason的6个fork，并读取作者发布库历史。`clash-ai-releases`当前README明确“Nothing is published here any more”，release列表为空；历史页面指向已撤的closed beta，不能当成现成下载。`clash-royale-simulator-trial@55d8103b...`实为Python自研模拟器，含battle.py/训练权重，没有原生loader/libg，不能作为本阶段降级通过。原始API响应、404正文和history在`outputs/phase2/native/*fetch*.json`及`clapha_*`。

## 真正取得的APK与两层兼容结论

公开发布链为：

`https://nulls.gg/servers/nulls-royale/download/` → `https://dnull.xyz/latest_cr` → `https://tempweb.nullsusercontent.com/fpapk/nr_15.535.13_release_ff1c6c29.apk`

已实际下载794,497,101字节，ZIP全部CRC通过，没有登录、注册账号或创建远程对局。HEAD返回403；普通GET带发布页Referer成功，CDN忽略Range并声明不支持分段。本项目工具能在HTTP200重试时逐段核对重复前缀后继续本地partial，不能宣称服务器支持Range续传。

| 材料 | 实际SHA256与结论 |
|---|---|
| 完整APK | `bbe2b3beae3de8fff17ead1da8aedcfac55d03bf2095f61a6dbec11b768f301a`；不同于上游期望`685c115e...`，原`offline_build.check-input`必须拒绝 |
| ARM64 libg，27,554,368字节 | `110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783`；与两套封装器精确支持值一致 |
| Jason预编译probe，6,346,600字节 | `d526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190`；ELF64/AArch64 |
| FirstLight清单所列基包输入 | 159/159文件逐组件SHA匹配，保存在`user-provided/base-assets`，不能由此推断其他文件相同 |
| APK内fingerprint | `f67740326725ccec63bb8eaf31f60908cad7b4ee02945c5e79efb6db71960419`；JSON version15.535.3、sha1`4a64dd8ad42e052d7dd51672fbaedc40155c5e7d` |
| 上游要求的runtime更新 | version15.535.86，fingerprint SHA`e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339`；83/83固定输入与完整651项delta已实际取得并独立核验，见后文安装/运行证据 |

证据：`apk_endpoint_probe.json`、`apk_download.json/.log`、`apk_local_inspection.json`、`base_asset_comparison.json`。实际APK只有arm64-v8a和armeabi-v7a，没有x86/x86_64游戏库。APK整体/逻辑库/数据资源必须分别锁定；后续使用本地新版APK需要建立显式的新来源锁，不修改旧SHA为“已通过”。

## 调用链、平台与离线边界

FirstLight原设计需要Windows/Python3.12、rooted MuMu ARM64，编译需要JDK17、AndroidNDK/SDK。Jason emulator.py则硬编码macOS SDK路径，直接执行会在本机失败，不能照README命令宣称已经安装。现有Windows JDK21可读可用；没有系统级安装Android组件。

真实调用链：APK的Titan GameApp加载libg → JNI_OnLoad/显式nativeBootstrapProbe → probe校验并安装地址hook/TCP控制 → GameApp先建立Content root/context → `configure`解析原生battle JSON → `native_controlled_worker_main`分配0x230 GameStateManager、调用constructor/setup/load → `GameStateManager::step`计算实际战斗。

`cold-config`表示无需一局先前回放来建立新battle，**不表示无需GameApp/content启动**。`inspect_content_runtime`读取libg全局`+0x019ad878`、root+0x20和context+0x6e8；probe自身没有加载libg和建立这些对象。将probe直接改成main还缺内容初始化和Android平台服务，不能靠填充假context通过。

精确libg的DT_NEEDED有libscid_sdk、liblog、libandroid、libEGL、libfmod、libfmodstudio、libdl、libsentry-android、libsentry、libm、libc++_shared、libc。APK提供scid/fmod/sentry/libc++等；Bionic/Android/EGL仍需运行时。库仅6个动态导出，核心逻辑无符号导出，需要已知偏移和严格指纹。静态解析DT_RELA/JNI表获得createGameMain等原生入口，9个核心probe prologue全部匹配；静态反汇编不是原生运行证据。

协调中的执行路线是项目内QEMU Linux VM + Android容器/ARM64 native bridge；独立QEMU-user+Bionic dlopen可作为低层探针，但尚不能替代App/JNI/content启动。FirstLight已有针对x86_64 libndk_translation的AArch64 SHA指令兼容补丁。Android主ABI为x86_64时不能用Jason硬编码`ro.product.cpu.abi==arm64-v8a`判据冒充客体库ABI，应实际校验库ELF、哈希和运行时attestation。

本项目通过公开静态CDN取得更新，没有注册远程账号。Android安装完成后已实际在隔断外网状态冷启动并完成原生战斗，网络正控制与隔离失败证据在secondary支线；这不等于完整A/B全部条目已通过。未运行官方账号、官方服务对战或第三方在线训练。

## API/状态与需要防止的误解

- 原生tick是50ms/20Hz；Jason reset显式warmup到tick90，第一动作通常tick91。不能复用旧平台10Hz或把warmup丢掉。
- 原生卡ID使用26000000/27000000/28000000等全局ID；坐标是整数原生单位。不能沿用旧173行ID/18×34坐标。
- Engine提供reset、step、advance、批量run、scheduled card/ability、schedule-status；RenderedEngine提供configure-native、pause、advance-native和速度。它们调用真实TCP接口，当前无服务时会失败。
- `minimal`含双方手牌/精确圣水和所有实体，属于oracle/debug，不可直接当actor观测；target_id等也可能暴露隐藏信息。需要实际隐身/视角证据后再定义可见过滤。
- 同一owner在一个step多次提交时，Jason executed布尔值可能只按owner/tick匹配同一消费事件；适配必须限制每owner一次或用逐请求schedule-status，而不能把多条都记为executed。
- Native-render由原ReplayBattleController和GameApp UI产生；未启动Android画面前，不把自绘界面、JNI入口或静态截图列作配套画面完成。
- probe有原生ByteStream snapshot接口和多resident manager，但不能从源码存在认定完整restore/多实例隔离已测；本阶段均未实测。

## 已执行命令与继续切口

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/phase2_native_fetch.py
.\.venv\Scripts\python.exe -X utf8 tools/phase2_native_assets.py --download
.\.venv\Scripts\python.exe -X utf8 tools/phase2_native_assets.py --extract-local
.\.venv\Scripts\python.exe -X utf8 tools/phase2_native_probe.py
.\.venv\Scripts\python.exe -X utf8 tools/phase2_native_disassemble.py
```

`native_probe.json`明确runtime_succeeded=false：TCP reset超时、Linux resource模块在Windows不可用、真实ARM64/ARMv7 ELF的Windows加载报WinError193。后续必须完成reset→实际消费下牌→推进→终局，才能更改原生运行结论。

## 已完成的离线APK构建与尚未通过的运行门槛

`tools/phase2_native_build_apk.py`已实际解包、修改smali、重建和本地签名APK。工具链为固定SHA的Apktool2.12.1、Apache apksig8.8.2及本机既有JDK21；没有安装全局SDK或接受SDK协议。补丁来自FirstLight，跳过GameApp在线loader判断，并在TitanGameApp加载libg后加载预编译crprobe、调用nativeBootstrapProbe。原libg及游戏数据没有重新实现或填造。

构建物`outputs/phase2/native/offline-native-smoke.apk`为794,792,236字节，SHA256 `97bad257c5d6e0499d2fe7e674f1f4de24fc28c19a8456c574dd8a8e8ea42c9a`。全ZIP CRC通过，APKVerifier实际V2/V3签名验证通过；V1未启用。嵌入的libg和probe仍分别是`110aa2...9783`与`d526ed...7190`。完整构建记录见`apk_build_state.json`、`apk_sign_verify.log`及重建日志。

中文绝对路径导致初次aapt2编译失败，失败日志保留在`apk_rebuild_unicode_failure.log`。项目内MSVC小包装器将同一项目内的绝对路径改为相对路径后，aapt2重建实际通过，未创建盘符映射或修改系统设置。源码为`tools/phase2_native_src/phase2_aapt_wrapper.cpp`。

辅助候选`Jaasssoooonnnnn/FL-Royale@cf16ef1759f465d74f1797c5e92fe3d939bbb4fc`已取117文件、ZIP CRC通过，位于`third_party/phase2/clapha/alternatives/FL-Royale`。无公开release APK。其本地RuntimeUpdateInstaller负责将打包的更新ZIP按逐文件SHA安装到应用私有`update`目录，其cold_home_scene/cold_clone_adapter有本地UI桥；仍依赖同一固定FirstLight/libg与资源，不是已取得的clapha loader，也不是已经运行的renderer。

团队运行时支线已实际启动项目内QEMU-WHPX Ubuntu24.04.5和Redroid Android12：boot_completed=1、zygote/surfaceflinger运行、ADB在线。ARM64代码已实际经NativeBridge执行。操作系统启动本身只计运行环境证据；原生战斗和renderer测试见后文独立记录。

本机探针`tools/phase2_native_match_probe.py`会记录每条原始TCP命令/响应、完整状态、实际CardPlay事件、generation、tick和终局。此前无服务时的失败结果保存在`outputs/phase2/native/match_probe/before_runtime`，不能算作运行通过。首次原生探针可明确选择基包content15.535.3；不能把它标成15.535.86。

独立资产支线已通过公开CDN取得15.535.86 fingerprint（内部content SHA1与上游完全一致），并逐文件核验83项清单以及基包到更新的完整651项delta。它们由根代理在`outputs/phase2/assets`记录；下载或静态SHA匹配不能替代内容初始化与离线冷启动测试。

## 已实际运行的原生闭环（后续更新）

隔离Android首次contentReady=true后，标准fixture拒绝，原始wire见`match_probe/base_actual_1`。logcat明确location15000199不存在；解出基包原始SC-LZMA locations.csv后确认只有199个定义（15000000至15000198）。改用实际存在的TrainingCamp15000001进行显式base15.535.3诊断，未改游戏数字或模拟逻辑：generation1、终局tick3934、result0、crowns[1,0]、64条实际原生CardPlay消费，用时6.51秒。第一条双方消费都在tick91。证据`match_probe/base_training_camp/{wire.jsonl,states.jsonl,report.json,initialization.json}`。

精确更新的651项完整delta实际取得123,035,884字节；加canonical fingerprint后652文件124,875,056字节。确定性tar SHA256 `2d067ac41de200b849a381030a89fe83505d579bcc819a9429cfc0820ed1785d`；Android安装后**652/652实际SHA256校验通过**，保留原update备份。fingerprint为精确上游e0cb，content15.535.86/41d8df...。公共fingerprint对assets.scdb所列SHA1与实际blob不同，但实际SHA256与固定FirstLight source_files精确相同；例外逐行保留，未放宽其他未知文件检查。

新版APK在外网被阻断时冷启，PID2132实际maps包含精确libg/probe和`/data/data/.../update/assets.scdb`。`tools/phase2_native_identity_receipt.py`核验已安装SHA、maps、全部652项检查和更新版本，保存`runtime86_adb_identity.json`。重要能力限制：固定预编译probe为CR_MINIMAL_CAPTURE，实际返回`attest`不支持；这里采用**外部Android root文件与进程映射证据**，不声称原生attest存在。源码中的full profile支持不等于已部署profile支持。

以精确.86标准location15000199运行`tools/phase2_native_match_probe.py --identity outputs/phase2/native/runtime86_adb_identity.json`，实际终局且64条消费。原始逐命令/状态证据位于`match_probe/runtime86_first`。这两场是oracle随机诊断fixture，不能计作人类回放、BC评估或官方轨迹一致性。

`tools/phase2_native_render_probe.py`随后实际调用configure-native、等待原ReplayBattleController、pause、advance-native，初始化至tick90，再实际下牌消费tick91并推进120。`render/runtime86_first/report.json`和wire证明nativeRenderReady=true、原生manager/controller均存在。独立ADB framebuffer `frame.png`（SHA256 d6370329...fb16dc8）已由两位代理实际view_image检查：真实竞技场、六塔、河桥、原生单位和血条，非加载画面。`frame_visual_review.json`明确局限：单帧、未测视频/FPS、minimal view没有完整手牌/控制HUD，不能由此宣称完整F全部功能完成。原协议阶段report另存`report.protocol_only.json`。

真实backend实现为`royale_phase2/backend.py`。它使用原始cr-minimal.v4数组返回debug oracle状态，actor只通过独立visibility白名单读取本人HUD和初始化确认的六个公共塔。scheduler的`succeeded`仅表示注入原生命令队列；backend额外要求唯一对应的CardPlay事件才标card_consumed，ability消费在minimal不可独立确认。同owner/tick多条、嵌入cmd与外部命令混合导致的来源歧义明确标记。无完整placement合法性oracle、无snapshot/restore，错误后须显式reset且通信变更不自动重发。

`tools/phase2_native_backend_smoke.py`已在恢复headless的真实.86进程运行7项检查全部通过，记录`backend_smoke/report.json`和完整wire：reset/warmup90、本人HUD+六塔白名单、tick91真实消费、缺卡/缺ability原生拒绝、exact20 tick、reset+journal replay的完整minimal状态对照一致（排除generation和全局事件序号）、原生排他上界x18000本地拒绝、快照能力显式失败、原生命令错误后episode失效并通过明确reset恢复。

## 接入最小示例

先按`phase2/SECONDARY_AUDIT.md`中的项目内VM/Android命令启动已准备的隔离运行时并建立loopback26789，再运行以下Python。它不会启动其他近似引擎；端口、版本、内容或原生命令失败都会报错。一个端口同时只允许一个推进/配置控制者，renderer切回headless需要重启App。

```python
import json
from pathlib import Path
from royale_phase2.backend import NativeBattleBackend

match = json.loads(Path(
    'outputs/phase2/native/match_probe/runtime86_first/initialization.json'
).read_text(encoding='utf-8'))
with NativeBattleBackend(trace_path='outputs/phase2/native/example-wire.jsonl') as native:
    oracle = native.reset(match)  # 原生tick90；oracle不可直接交给actor
    own_hand = oracle['players'][0]['hand']
    card_id = own_hand[0][1]
    receipt = native.schedule_card(0, card_id, 3000, 12000, 91, request_id='example-1')
    native.advance_exact_ticks(20)
    print(native.execution_feedback())  # 注入和实际消费分别报告
    print(native.observe_visible(0))    # actor只用此白名单观测
```

`reset(match)`保留传入原生JSON中的cmd/evt，不改卡等、种子、坐标或事件顺序。若要逐动作可审计来源，使用空cmd的初始化并通过`schedule_journal`注册；同时混合原cmd和外部schedule会使消费来源不唯一，wrapper不会把该歧义转成成功标签。`debug_state()`是明确oracle调试；`result()`提供原生终局/tick/crowns；`export_journal()`与`replay_from_start()`可恢复本wrapper调度历史，不能替代未知隐藏状态的真实原局快照。

## 原子推进、真实机制夹具与两法术动作证明

backend现默认`advance_mode='atomic'`，使用原有原生`minimal-transition generation cursor N 0`一次返回推进后的原始capture；`advance_mode='two_rpc'`保留先step再minimal方案。没有变更原生库或加入Python物理。`tools/phase2_native_atomic_benchmark.py`实际比较相同seed、4条相同scheduled命令、两模式各121个完整状态点，排除generation/全局事件序号后全部字段一致。一次短fixture耗时two-RPC8.576秒、atomic4.346秒，约1.97倍；证据`outputs/phase2/native/atomic_benchmark`。这是单fixture的通讯吞吐测量，不是整个总体100局或并发能力结论。

root实际人类回放暴露effective-form的事件编码差异。修复后，注册时保留本人hand/cost/packed参数和轮转，跨目标tick记录本人变化。event.card=0不被硬映射成普通卡；仅在native注入参数一致、同owner/tick唯一事件、成本及hand/cycle变化都确认时标记root card已消费，effective_form_identity仍unknown。evolution用精确spells_evolved.csv名称与锁定card_specs连接，并要求本人边界证据。已有原始真实记录离线重算43/43，随后根代理新原生运行也43/43；旧未识别10条的记录保留，没有放松输入或levels。

`tools/phase2_native_fixtures.py --prepare`只读资源并写未运行计划；`--run`需独占RPC。20项覆盖重置/六塔、圣水速率与上限、2x/3x阶段、实际费用、手牌轮转、数量、过桥、建筑拉扯/重新索敌、攻击/冷却、火球塔伤、箭雨波次、空地法术、护盾、墓碑死亡衍生、石头人分裂、evolution部署及ArcherQueen能力runtime转换。最新20项内部回归通过见`outputs/phase2/fixtures/mechanism_coverage.json`，其中19个独立新配置、1项明确使用已实际运行回放中的evolution边界记录。全部独立官方真值分母为0；evo战斗特效、能力完整效果和隐身可见性仍未由这些夹具证明。

首轮17pass/3fail保存在`native-v1`。修复并重新运行的6项见`native-v2-repair`，没有覆盖首轮：

- 原先假设严格3/2.8圣水每秒不符这个固定游戏版本。精确battle_timelines.csv的满条10费时间为28000/14000/9300ms；原生每tick固定整数增量是floor(100000×50/fullBarMS)，即178/357/537raw，实测56ticks增9968/19992/30072，重新按数据校验精确一致。
- 部署阶段attack_cooldown_ms可为null；测试不把它与整数排序，仍要求部署后真实伤害和非零冷却变化。
- 请求墓碑坐标(3000,1000)实际原生修正为(2500,2500)。死亡衍生比较必须围绕实际父对象落点。子兵card沿用父root27000009，父对象kind也被minimal标成troop；这些遥测字段不能直接作为完整单位原型解释。actor当前省略所有非塔对象，不受该原型歧义影响。

`data/phase2/action_subspace_proof.json`定义`wait_or_arena_spell.v1`，只含WAIT与本人当前普通Fireball28000000/Arrows28000001手牌。规则来源为锁定catalog及原始source_records：CanDeployOnEnemySide/CanPlaceOnWater/CanPlaceOnBuildings均true、target_schema full_arena、不要求可见目标、两卡evolution均null。40次实际原生正例覆盖2卡×2owner×10代表点（四角、河/桥、塔位），并实测出界、缺牌、不足费用反例、当前选择参数/命令边界和敌方私有字段扰动下投影不变。root的proof guard实际返回verified_local_rule_evidence。坐标是规则支持的整数请求矩形0..17999/0..31999，有限点测试不等于穷举每个坐标，更不延伸到其他牌、进化、hero或ability。
