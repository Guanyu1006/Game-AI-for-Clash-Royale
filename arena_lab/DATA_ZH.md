# Arena Lab 公开高手整局数据

本页记录 2026-11-13 取得的真实公开回放、身份凭据和模拟重建边界。它与旧 `royale_*`、FirstLight 固定平台的训练数据和验收分别记录。首轮训练输入已冻结，后续完整归档扩展使用独立目录。

## 复现首轮 212 局

在仓库根目录使用项目解释器；模拟器先按 [Arena Lab 方案](PLAN_ZH.md) 安装。以下一条命令先取得固定来源，再在新目录重建：

```bash
.venv-cloud/bin/python -m arena_lab.data_sources fetch --output-dir data/arena-lab && \
.venv-cloud/bin/python -m arena_lab.replay_data --source-index data/arena-lab/cochon-historical-top100-v1/source-index.json --destination data/arena-lab/derived-base-default6-reproduced
```

首次获取约 151 MB。下载和已存在文件都按锁验证；不同内容不会覆盖原始文件。派生目录必须新建，已有 NPZ 不会被覆盖。当前代码已包含 Heal Spirit、Spirit Empress、Void 和 Rune Giant 的显式内部名映射，复现时直接处理全部 212 局，无需历史执行时的单独恢复操作。

已完成一次独立空目录的远端重新下载与选取，得到完全相同的 212 个原局指纹、来源 SHA、236 个老师席位与整局 split。证据：`outputs/arena-lab-20261113/data/cold-reproduction-verification.json`。

## 来源和高手身份

- 数据集：[Cochon123/clash-royale-replays](https://huggingface.co/datasets/Cochon123/clash-royale-replays)，固定 revision `10cca44e9d863d06cf078f142d3e744f78698b0a`。
- 来源快照：`snapshots/data-2026-11-01.tar.gz`。首轮读取的是其 **139,991,040 字节压缩前缀中所有完整成员的全部身份匹配**；并非整个 HF 数据集、整个快照或随机抽样。
- 老师凭据：[RoyaleAPI 2026-08-04 发布的 July 2026 全球 Top 100 榜单](https://royaleapi.com/blog/leaderboard-decks-august-2026)，赛季于 2026-08-03 结束。依据排名正文及其账号链接精确匹配公开玩家 tag；不是凭显示名猜测身份。
- 可提交的事实表与获取锁：[`historical-top100-202607.json`](manifests/historical-top100-202607.json)、[`cochon-historical-top100-v1.json`](manifests/cochon-historical-top100-v1.json)。事实表含排名、分数、账号、日期和来源 URL，不复制文章正文。
- 212 局原始采集时间为 2026-07-26 至 2026-08-11。这证明参与账号在邻近赛季达到历史 Top 100；不声称每局当时排名或双方都是高手。只有有凭据的席位作为老师。

首轮扫描 25,597 个完整 tar 原始成员，217 个匹配身份的候选中 212 局具有可解析的完整双卡组及时间线，另 5 个候选缺少两套完整独特八卡组而排除；另有 1 个非身份可确认成员的 JSON 不可读。来源索引保留所有分母和排除原因。

不能把采集器“从排行榜开始抓取”的描述当作所有玩家的高手证据：该采集器也扩展对手。快照中另一批官方 API battlelog 文件实际为 `accessDenied/Invalid authorization`，未作为排名或完整性凭据，也未尝试读取登录凭据。

## 哈希和数据位置

| 对象 | SHA256 |
|---|---|
| 首轮压缩前缀 | `a140d9fe121e39353e852b17148dc5a3746f67cf7a4710dd5337fa4be3983458` |
| 上游逐文件 manifest | `28be3b2f0a830921f126a1cb9e230d568436bbe9e1564e1ee2e4c4ce9ef9c9da` |
| 完整 534,361,487 字节归档 | `01fa9b8973b67c8115547f815e9ca0218240fe928aa2f2f33a3c29f040ea7fc9` |
| 原始榜单浏览器提取证据 | `44bdaf5a03cdb2d6b5b44cacf2d1c9566ed3cd910561268a279aba1bca7c7500` |
| 首轮冻结训练 index | `0c3b508bb0fdb4fce6a84ebfb166dec31330bec657fb6ea3c565afc2c6ddc90f` |

原始榜单证据仅保留在被忽略的 `data/arena-lab/raw/evidence/`，明确是浏览器可读文本提取，不是原 HTTP HTML。直接匿名 HTTP 访问返回 403，403 页面没有被当作选手档案证据。

取得的 212 份原局原文合计 18,808,568 字节，每份均与固定上游 manifest 的 SHA256 一致，位于 `data/arena-lab/cochon-historical-top100-v1/raw-selected/`；规范化文件在同级 `normalized/`，来源索引为 `source-index.json`。原文保留双方 tag、双八卡组、全部来源事件、坐标及方向字段、时间边界与终局皇冠。

冻结的训练输入为 `data/arena-lab/derived-base-default6-v2/index.json`，含 236 个 NPZ 老师序列。历史恢复的 7 局 Rune Giant 放在其 `recovered/` 子目录，前一轮缺别名的排除现场仍保留。后续训练不得改写该 index 或 NPZ。首轮冻结时的数据源码副本及 SHA 保留于 `outputs/arena-lab-20261113/source-v1/`。

## 整局划分与可用标签

按去除观看方向影响的原局指纹划分：排序后的实际玩家 tag、完整卡组、皇冠、以及 tick/实际玩家/card/ability 轨迹。指纹忽略坐标，避免同局反向观看落入不同 split；相同 source ID、原始 SHA 和原局指纹亦检查不可跨 split。同局双老师席位保持同组。

| split | 原局数 | 老师序列 | 有效下牌标签 | 有效 WAIT | 失效标签 |
|---|---:|---:|---:|---:|---:|
| train | 153 | 171 | 1,998 | 27,048 | 40,294 |
| validation | 32 | 34 | 312 | 4,164 | 7,864 |
| test | 27 | 31 | 373 | 5,268 | 6,359 |

236 个 NPZ 共 93,680 个决策帧。当前 base 假设没有有效能力标签。表中同时给出失效和 WAIT 分母，不把仅剩的可接受动作当成完整原局标签。全部 NPZ 已逐份验证哈希、无 pickle/object、shape、时间递增和目标合法 mask；不会为迁就来源动作解除 mask。

## 原始动作与模拟假设分层

原始回放是 RoyaleAPI HTML 的完整部署时间线和终局元数据，**不包含原局逐帧实体状态**。以下原始字段缺失：精确客户端 build、初手顺序和 RNG、等级、实际进化/英雄形态。所有来源事件未带 `-ev1/-hero`，因此规范化的 `form` 保持 `None`，不能称为已知基础形态。

重建使用固定 RoyaleSim 0.1.51 / RoyaleGym 0.1.29、牌表 FNV `803ec02c7eac9fd3`，观测 `arena-public-v2`：global 1057、entity 405、最多 128 实体。实际 reset 覆盖写入 index 顶层及 provenance：双卡组、`shuffle=0`、seed 0、等级/塔等级 16、基础形态假设、引擎固定默认初始圣水 6、时间偏移 0。构造器默认配置与这些逐局覆盖分开记录。初始圣水 5 的早期敏感性现场保留，未混入主训练集。

初手求解在普通八卡循环模型下枚举 1,680 个“初始四卡集合＋余下队列”的等价类，选择字典序第一个满足整段可用性约束的假设；初始槽位排列仍有等价歧义，冠军存活三卡循环也未被该求解器证明。它使用完整来源未来动作约束开局，属于 **hindsight 条件下的模拟假设**，不是恢复了真实因果观测。每局保存一致候选数、最长一致前缀、选择规则与未知原始 seed；不会逐次把未来要出的牌塞入手牌。

所有动作仍在原 tick 尝试执行，原始 millitile 坐标乘 18；`data-i` 决定 source team 到 native seat 的映射，不能对所有局统一翻转。训练每 10 tick、即 0.5 秒，先观测后取窗口 `[t,t+10)` 内首个老师动作为标签。记录提前量 0–9 tick；同侧多动作窗口明确失效。位置标签用 own-frame 格点，精确原生重演仍使用原坐标，并单独记录格心量化误差。

`Heal` 内部虽标为 SPELL，实际为生成 HealSpirit 的部署包装；Spirit Empress 3/6 形态和 Rune Giant 附魔亦按底层定义及原生/官方证据映射，见 [`card-alias-evidence-v1.json`](manifests/card-alias-evidence-v1.json)。Void 在 2026-08-04 从 3 费改为 5 费，采集期跨过该更新；采用当前固定表会逐局记录版本警告，不能称为当局完全匹配。

## 重建审计结论

212 局来源共有 17,713 事件：16,861 次下牌和 852 次能力。模拟器接收 13,373 次下牌、原生拒绝 10 次、假设手牌缺牌 29 次；3,649 个来源事件在模拟提前终局后未到达。首个偏差原因为：192 局能力身份未知、2 局 `NO_DEPLOY`、1 局 `OUT_OF_TERRITORY`；另 17 局在到达的事件中未遇首偏差。首偏差所在决策窗口及全部后续状态标签均失效，仍保留整局原文、原始分母和逐事件诊断。没有重试、加圣水或扩大时间容差来掩盖拒绝。

178 局模拟自然终局，另 34 局到来源时间边界仍未自然结束。在 178 局中，正确映射双方后只有 124 局胜者与原局相同；自然终局 tick 精确匹配为 0。该统计衡量重建差异，不是学习策略胜率。

因此 `strong_imitation_delivery_gate_passed=false`。已取得真实历史高手的整局动作来源，并实际导出可审计的假设状态模仿数据；尚未取得忠实重建的官方战场状态或实战高手策略证明。完整数字见 `outputs/arena-lab-20261113/data/final-dataset-freeze-v2.json` 与冻结目录的 `validation-audit.json`；训练与闭环结果见 [本轮结果](../docs/ARENA_LAB_RESULTS_20261113_ZH.md)。

## 完整归档扩展

完整固定快照使用独立清单 [`cochon-historical-top100-full-v2.json`](manifests/cochon-historical-top100-full-v2.json) 和目录，保持首轮冻结输入不变：

```bash
.venv-cloud/bin/python -m arena_lab.data_sources fetch --full --output-dir data/arena-lab/full-v2
```

该命令下载并验证完整归档，再扫描其全部 raw 成员；不是把压缩前缀伪装为完整归档。完整快照本身仍不代表 HF 当前仓库所有版本的数据。新增选取结果和与首轮的一致性单独审计，未自动加入已完成训练。

完整扫描已经完成：64,275 个 raw 成员中有 586 个身份候选，选出 **574 个独立整局、682 个认证老师席位**，比首轮新增 362 局。12 个候选排除为 10 个双卡组不完整、2 个空或超过终止边界的时间线，另有 2 个不可读 JSON。574 局按整局划分为 train 446 / validation 63 / test 65。原 212 局的原局指纹、来源 SHA、老师席位及 split 逐条一致。

完整集包含 47,063 次来源事件，其中下牌 44,825、能力 2,238；所有来源 `form` 仍未知，未补出精确 build、初手、RNG 或等级。采集时间扩展至 2026-08-11 18:39 UTC，未改变历史榜单证据的性质。来源索引为 `data/arena-lab/full-v2/cochon-historical-top100-full-v2/source-index.json`；对比审计为 `outputs/arena-lab-20261113/data/full-source-comparison-v2.json`。

其中 11 局来源双方皇冠相等，HTML 未提供明确胜负或终局塔血，`winner` 保持未知，不认定平局，也不从模拟结果反推原局胜负。老师席位的原局结果为 405 胜、263 负、14 未知，未按胜负筛选。全量覆盖 82 个认证账号、147 种基础八卡组；这些只描述样本覆盖，不能弥补缺失的战场状态。

完整集的同配置严格重建另存 `data/arena-lab/full-v2/derived-base-default6-v1/`，不会改首轮 index、NPZ 或模型。重建命令为：

```bash
.venv-cloud/bin/python -m arena_lab.replay_data --source-index data/arena-lab/full-v2/cochon-historical-top100-full-v2/source-index.json --destination data/arena-lab/full-v2/derived-base-default6-v1
```

## 许可与访问范围

[固定版本数据集说明](https://huggingface.co/datasets/Cochon123/clash-royale-replays/blob/10cca44e9d863d06cf078f142d3e744f78698b0a/README.md) 标记 MIT，同时要求 RoyaleAPI/Supercell 材料用于研究或个人项目；源码许可不能替代素材与服务条款。这里保存公开游戏账号及回放，不取得官方账号授权、私密对局或 API 凭据。文章正文只作本机研究原始证据，Git 中提交的是事实表、获取方法、许可链接与哈希；HF 与 RoyaleAPI 的可访问性仍可能变化。
