# 开源项目与可复用模块

源码固定到 GitHub commit，以完整源码 ZIP 或重要代码/许可子集 ZIP 保存；每项 download_scope 明示范围。CRC 检查及 SHA256 支持离线核验。下载不等于安装、权重验证或对局复现。

相关皇室战争引擎的源码审计与实际构建结果以项目 `docs/` 中的引擎审计报告和 `outputs/` 日志为准。本目录的下载器不执行第三方代码。

| 项目 | 用途 | commit | 许可 | 下载/范围 | 本机状态 |
| --- | --- | --- | --- | --- | --- |
| [DLR-RM/stable-baselines3](https://github.com/DLR-RM/stable-baselines3) | RL 训练工具 | `b2878f4b83e2` | MIT | downloaded / full_source_archive | not_installed_or_executed_by_research_fetch |
| [Farama-Foundation/Gymnasium](https://github.com/Farama-Foundation/Gymnasium) | 环境协议 | `d77197c28440` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [Farama-Foundation/Minari](https://github.com/Farama-Foundation/Minari) | 离线数据规范 | `a4a395d0b379` | Apache-2.0 | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [Farama-Foundation/PettingZoo](https://github.com/Farama-Foundation/PettingZoo) | 多智能体协议 | `b0fe02166d21` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [google-deepmind/mctx](https://github.com/google-deepmind/mctx) | JAX 搜索工具 | `428bfb7e1c93` | Apache-2.0 | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [google-deepmind/open_spiel](https://github.com/google-deepmind/open_spiel) | 博弈/策略池工具 | `48401890ee98` | Apache-2.0 | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [h2r/pomdp-py](https://github.com/h2r/pomdp-py) | POMDP 信念工具 | `bd0e4392247a` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [HumanCompatibleAI/imitation](https://github.com/HumanCompatibleAI/imitation) | 模仿工具 | `e5ef18806c44` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [ifzhang/ByteTrack](https://github.com/ifzhang/ByteTrack) | 追踪工具 | `d1bf0191adff` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [itzik123/ClashRoyaleAi](https://github.com/itzik123/ClashRoyaleAi) | 皇室战争近似战斗引擎 | `2f172925a15b` | MIT | downloaded / upstream_raw_blob_snapshot_excluding_skipped_large_artifacts | native engine compiled and tested separately; see docs/audits and outputs/native_engine* |
| [MSU-AI/clash-royale-gym](https://github.com/MSU-AI/clash-royale-gym) | 皇室战争环境骨架/反例 | `1cd23be16e10` | MIT | downloaded / audited_upstream_source_snapshot | Python3.11 install passed; gym.make/reset/step failed due upstream skeleton |
| [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) | OCR 工具 | `dab3fe353790` | Apache-2.0 | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [Pbatch/ClashRoyaleBuildABot](https://github.com/Pbatch/ClashRoyaleBuildABot) | 皇室战争设备机器人 | `f89ecbf309e6` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [Stable-Baselines-Team/stable-baselines3-contrib](https://github.com/Stable-Baselines-Team/stable-baselines3-contrib) | RL mask/循环工具 | `075bd5be8d43` | MIT | downloaded / full_source_archive | not_installed_or_executed_by_research_fetch |
| [takuseno/d3rlpy](https://github.com/takuseno/d3rlpy) | 离线 RL 工具 | `38c34b6f99fd` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics) | 检测工具 | `ab3bd58844b0` | AGPL | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [voonhous/crforge](https://github.com/voonhous/crforge) | 皇室战争独立参考引擎 | `b41cf9b62769` | Apache-2.0 | downloaded / audited_upstream_source_snapshot | crforge compiled, 974 tests passed and 3 full episodes |
| [vwxyzjn/cleanrl](https://github.com/vwxyzjn/cleanrl) | RL 可读实现 | `fe8d8a03c41a` | Apache-2.0 | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [wty-yy/Clash-Royale-Detection-Dataset](https://github.com/wty-yy/Clash-Royale-Detection-Dataset) | 皇室战争感知数据 | `31b4151fedb1` | MIT | downloaded / fixed_code_license_dependency_subset | not_installed_or_executed_by_research_fetch |
| [wty-yy/Clash-Royale-Replay-Dataset](https://github.com/wty-yy/Clash-Royale-Replay-Dataset) | 皇室战争专家回放 | `ce86e10bedcf` | No LICENSE in fixed upstream tree | partial_dataset / data_only_repository; complete tree plus small samples | not_unpickled_or_used_for_training |
| [wty-yy/KataCR](https://github.com/wty-yy/KataCR) | 皇室战争真实像素代理 | `36ceb9fcfbd1` | MIT | downloaded / full_source_archive | AST110 Python files and card cost utility import passed; realtime import missing cv2; Windows CUDA wheel probe incompatible |

## DLR-RM/stable-baselines3

用途：RL 训练工具。

可复用：PPO、rollout buffer、callback/checkpoint。

依赖与兼容：PyTorch、Gymnasium；Python 版本/torch wheel 需兼容。

修改成本与限制：低中：固定编码/actions；标准 PPO 本身无 mask。

固定来源：[b2878f4b83e21f16a53c54a62def628e05fd2e96](https://github.com/DLR-RM/stable-baselines3/tree/b2878f4b83e21f16a53c54a62def628e05fd2e96)。最新提交时间（抓取时）：`2026-10-13T15:40:25Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`full_source_archive`。省略：完整上游源码 archive，不含 Git 历史。
源码：[source-b2878f4b83e21f16a53c54a62def628e05fd2e96.zip](<D:/研究生/Clash Royal/references/projects/DLR-RM__stable-baselines3/source-b2878f4b83e21f16a53c54a62def628e05fd2e96.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/DLR-RM__stable-baselines3/inventory.json>)。183 个文件，97 个 Python 源码，0 个 C/C++ 源码。SHA256 `09f82aa59a9fdd62a340431f6580122f305deb52a70e861ecad37b9489b7d7b1`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/DLR-RM__stable-baselines3/metadata/LICENSE>)

## Farama-Foundation/Gymnasium

用途：环境协议。

可复用：Env API、spaces、checker、vector env。

依赖与兼容：Python/numpy，模块具体环境另有依赖。

修改成本与限制：低：reset/step、terminated/truncated、seed 定义。

固定来源：[d77197c284406e6094c9d1a74d4988a8749ce849](https://github.com/Farama-Foundation/Gymnasium/tree/d77197c284406e6094c9d1a74d4988a8749ce849)。最新提交时间（抓取时）：`2026-10-10T14:26:18Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-d77197c284406e6094c9d1a74d4988a8749ce849.zip](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Gymnasium/source-d77197c284406e6094c9d1a74d4988a8749ce849.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Gymnasium/inventory.json>)。17 个文件，14 个 Python 源码，0 个 C/C++ 源码。SHA256 `307c8c591bd08d078b125204c0c5df9bb6d139c2f1106021dbd6c6b283e022f5`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Gymnasium/metadata/LICENSE>)

## Farama-Foundation/Minari

用途：离线数据规范。

可复用：dataset metadata、episode storage/校验。

依赖与兼容：Gymnasium、HDF5/相关存储依赖。

修改成本与限制：中：保存版本/事件时间戳/动作反馈；不是专家数据来源。

固定来源：[a4a395d0b379b1299cf357f5c23c9c30838d4e46](https://github.com/Farama-Foundation/Minari/tree/a4a395d0b379b1299cf357f5c23c9c30838d4e46)。最新提交时间（抓取时）：`2026-10-05T00:27:06Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-a4a395d0b379b1299cf357f5c23c9c30838d4e46.zip](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Minari/source-a4a395d0b379b1299cf357f5c23c9c30838d4e46.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Minari/inventory.json>)。15 个文件，12 个 Python 源码，0 个 C/C++ 源码。SHA256 `57ac2f0172694cdfd52eabda5a9611d1964ebfe7a4ceaf3e40021aa8888f1961`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__Minari/metadata/LICENSE>)

## Farama-Foundation/PettingZoo

用途：多智能体协议。

可复用：AEC/Parallel env、API 测试。

依赖与兼容：Gymnasium/numpy；部分环境有额外依赖。

修改成本与限制：中：同时动作的 tick 语义与对称玩家映射。

固定来源：[b0fe02166d2106f8fcb6a6e0ca8125765699bdba](https://github.com/Farama-Foundation/PettingZoo/tree/b0fe02166d2106f8fcb6a6e0ca8125765699bdba)。最新提交时间（抓取时）：`2026-10-09T09:11:33Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-b0fe02166d2106f8fcb6a6e0ca8125765699bdba.zip](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__PettingZoo/source-b0fe02166d2106f8fcb6a6e0ca8125765699bdba.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__PettingZoo/inventory.json>)。8 个文件，5 个 Python 源码，0 个 C/C++ 源码。SHA256 `9700321bde383041136d81879ef7d7b073ba8eacd9a9262485f62696180a3b48`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/Farama-Foundation__PettingZoo/metadata/LICENSE>)

## google-deepmind/mctx

用途：JAX 搜索工具。

可复用：MuZero/AlphaZero 风格向量化树搜索。

依赖与兼容：JAX/accelerator、recurrent inference API。

修改成本与限制：高：Python backend 不能自然 JIT；需纯函数动态或 learned model。

固定来源：[428bfb7e1c931715cd3d74f9c65e9991afd86df8](https://github.com/google-deepmind/mctx/tree/428bfb7e1c931715cd3d74f9c65e9991afd86df8)。最新提交时间（抓取时）：`2026-10-05T22:08:00Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-428bfb7e1c931715cd3d74f9c65e9991afd86df8.zip](<D:/研究生/Clash Royal/references/projects/google-deepmind__mctx/source-428bfb7e1c931715cd3d74f9c65e9991afd86df8.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/google-deepmind__mctx/inventory.json>)。7 个文件，4 个 Python 源码，0 个 C/C++ 源码。SHA256 `fee602cd807ba655b5b48a358cf067b6ee1f6bf2ae0d6ec72ee14af0ddb48e5b`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/google-deepmind__mctx/metadata/LICENSE>)

## google-deepmind/open_spiel

用途：博弈/策略池工具。

可复用：game API、MCTS、NFSP、PSRO、payoff evaluation。

依赖与兼容：C++/Python、不同算法有深度学习依赖。

修改成本与限制：高：实时/空间状态适配；不能替代皇室战斗引擎。

固定来源：[48401890ee9857e611678302371378175a8e4c6b](https://github.com/google-deepmind/open_spiel/tree/48401890ee9857e611678302371378175a8e4c6b)。最新提交时间（抓取时）：`2026-08-31T20:31:36Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-48401890ee9857e611678302371378175a8e4c6b.zip](<D:/研究生/Clash Royal/references/projects/google-deepmind__open_spiel/source-48401890ee9857e611678302371378175a8e4c6b.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/google-deepmind__open_spiel/inventory.json>)。24 个文件，17 个 Python 源码，3 个 C/C++ 源码。SHA256 `129e901476eca723af726a132dfa91b2c5bacc2f13e99001dc249892e3209dce`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/google-deepmind__open_spiel/metadata/LICENSE>)

## h2r/pomdp-py

用途：POMDP 信念工具。

可复用：belief、generative model、POMCP 规划接口。

依赖与兼容：Python/Cython；构建兼容性需核查。

修改成本与限制：中高：观测似然、粒子 reinvigoration 与 rollout 预算。

固定来源：[bd0e4392247aebfe9a95b449275237dcc25e7737](https://github.com/h2r/pomdp-py/tree/bd0e4392247aebfe9a95b449275237dcc25e7737)。最新提交时间（抓取时）：`2025-10-28T03:55:09Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-bd0e4392247aebfe9a95b449275237dcc25e7737.zip](<D:/研究生/Clash Royal/references/projects/h2r__pomdp-py/source-bd0e4392247aebfe9a95b449275237dcc25e7737.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/h2r__pomdp-py/inventory.json>)。14 个文件，6 个 Python 源码，0 个 C/C++ 源码。SHA256 `6c31b61b3ae3ed52bd67c30563ea3b1bdbfccb771b9e0b6e0225b088e14ea7c7`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/h2r__pomdp-py/metadata/LICENSE>)

## HumanCompatibleAI/imitation

用途：模仿工具。

可复用：BC、DAgger、GAIL、trajectory 数据结构。

依赖与兼容：SB3、Gymnasium、PyTorch。

修改成本与限制：中：专家可查询、整局拆分和候选标签 schema。

固定来源：[e5ef18806c449ca47153b494a02471c5e2ae3a14](https://github.com/HumanCompatibleAI/imitation/tree/e5ef18806c449ca47153b494a02471c5e2ae3a14)。最新提交时间（抓取时）：`2025-01-07T05:56:14Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-e5ef18806c449ca47153b494a02471c5e2ae3a14.zip](<D:/研究生/Clash Royal/references/projects/HumanCompatibleAI__imitation/source-e5ef18806c449ca47153b494a02471c5e2ae3a14.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/HumanCompatibleAI__imitation/inventory.json>)。10 个文件，5 个 Python 源码，0 个 C/C++ 源码。SHA256 `972b3016d13d6281a6b1691608eca89271700add8975d9a44aeee218da6706e2`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/HumanCompatibleAI__imitation/metadata/LICENSE>)

## ifzhang/ByteTrack

用途：追踪工具。

可复用：检测后 Kalman/匹配/track 生命周期。

依赖与兼容：numpy/scipy、示例有 YOLOX/torch 等依赖。

修改成本与限制：中：精灵类别/阵营 gated matching，验证召唤与死亡事件。

固定来源：[d1bf0191adff59bc8fcfeaa0b33d3d1642552a99](https://github.com/ifzhang/ByteTrack/tree/d1bf0191adff59bc8fcfeaa0b33d3d1642552a99)。最新提交时间（抓取时）：`2022-12-11T07:05:00Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-d1bf0191adff59bc8fcfeaa0b33d3d1642552a99.zip](<D:/研究生/Clash Royal/references/projects/ifzhang__ByteTrack/source-d1bf0191adff59bc8fcfeaa0b33d3d1642552a99.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/ifzhang__ByteTrack/inventory.json>)。20 个文件，16 个 Python 源码，0 个 C/C++ 源码。SHA256 `53239adeedc38ed7b57ee578fe56033577ac8f306b7352027b75a5cf2d77a771`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/ifzhang__ByteTrack/metadata/LICENSE>)

## itzik123/ClashRoyaleAi

用途：皇室战争近似战斗引擎。

可复用：C++ simulator、Python binding、训练/search。

依赖与兼容：C++ 编译、Python ABI、RL 依赖；Windows 构建需核查。

修改成本与限制：中高：backend adapter、观测泄漏/卡牌覆盖/快照；不等于官方引擎。

固定来源：[2f172925a15b17f973291f41a789b19a29f0fc43](https://github.com/itzik123/ClashRoyaleAi/tree/2f172925a15b17f973291f41a789b19a29f0fc43)。最新提交时间（抓取时）：`2026-09-29T02:45:56Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`native engine compiled and tested separately; see docs/audits and outputs/native_engine*`。
下载范围：`upstream_raw_blob_snapshot_excluding_skipped_large_artifacts`。省略：[{'path': 'perception/clashroyalebuildabot/models/units_M_480x352.onnx', 'status': 'not_downloaded_large_asset', 'size': 30896276}]。
源码：[source-2f172925a15b17f973291f41a789b19a29f0fc43.zip](<D:/研究生/Clash Royal/references/projects/itzik123__ClashRoyaleAi/source-2f172925a15b17f973291f41a789b19a29f0fc43.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/itzik123__ClashRoyaleAi/inventory.json>)。847 个文件，361 个 Python 源码，144 个 C/C++ 源码。SHA256 `e6f5e6efc746e59573dde383cc303dfeda88a8777a8efa18dccdbb4dac7e4b28`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/itzik123__ClashRoyaleAi/metadata/LICENSE>)，[LICENSE.md](<D:/研究生/Clash Royal/references/projects/itzik123__ClashRoyaleAi/metadata/perception/clashroyalebuildabot/LICENSE.md>)
源码及安装/运行实测：[ClashRoyaleAi.md](<D:/研究生/Clash Royal/docs/audits/ClashRoyaleAi.md>)。

## MSU-AI/clash-royale-gym

用途：皇室战争环境骨架/反例。

可复用：Gymnasium注册与草拟模块划分。

依赖与兼容：Python3.11+pygame已安装，reset/step实际未实现。

修改成本与限制：极高：主要方法为pass；不选作后端，保留源码级排除证据。

固定来源：[1cd23be16e10b25c5a5a0889626a863092f32615](https://github.com/MSU-AI/clash-royale-gym/tree/1cd23be16e10b25c5a5a0889626a863092f32615)。最新提交时间（抓取时）：`2024-03-13T02:58:35Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`Python3.11 install passed; gym.make/reset/step failed due upstream skeleton`。
下载范围：`audited_upstream_source_snapshot`。省略：[]。
源码：[source-1cd23be16e10b25c5a5a0889626a863092f32615.zip](<D:/研究生/Clash Royal/references/projects/MSU-AI__clash-royale-gym/source-1cd23be16e10b25c5a5a0889626a863092f32615.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/MSU-AI__clash-royale-gym/inventory.json>)。26 个文件，17 个 Python 源码，0 个 C/C++ 源码。SHA256 `0fbbb2a2726751852e8ada6ce94e2c4ddc7a751e52e52f503b1d12cb2541e18d`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/MSU-AI__clash-royale-gym/metadata/LICENSE>)
源码及安装/运行实测：[MSU_GYM.md](<D:/研究生/Clash Royal/docs/audits/MSU_GYM.md>)。

## PaddlePaddle/PaddleOCR

用途：OCR 工具。

可复用：文本检测/识别、数字 ROI 推理。

依赖与兼容：PaddlePaddle、版本与设备依赖。

修改成本与限制：中：圣水/塔血小字专门校准与合法数值约束。

固定来源：[dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf](https://github.com/PaddlePaddle/PaddleOCR/tree/dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf)。最新提交时间（抓取时）：`2026-09-16T03:30:53Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf.zip](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/source-dab3fe35379033fdcb2d0e9572fac0b36c9a9ebf.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/inventory.json>)。14 个文件，7 个 Python 源码，0 个 C/C++ 源码。SHA256 `1fe3636c2d9a9f29a11f9222862e7355a385c571fdbc0bd93631fc6af1a091d7`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/metadata/LICENSE>)，[LICENSE.md](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/metadata/benchmark/PaddleOCR_DBNet/LICENSE.md>)，[LICENSE](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/metadata/deploy/cpp_infer/THIRD_PARTY_LICENSES/tensorRT_Pro/LICENSE>)，[LICENSE](<D:/研究生/Clash Royal/references/projects/PaddlePaddle__PaddleOCR/metadata/deploy/ios_demo/PaddleOCRDemo/ThirdParty/Clipper1/LICENSE>)

## Pbatch/ClashRoyaleBuildABot

用途：皇室战争设备机器人。

可复用：屏幕状态生成、ADB 动作、视觉标签与策略外壳。

依赖与兼容：Python、Android/模拟器、检测权重；依赖设备。

修改成本与限制：中高：把感知/执行接口接入当前闭环；无设备时只读审计。

固定来源：[f89ecbf309e698cb9fccdfb64a6ff2a930e6541e](https://github.com/Pbatch/ClashRoyaleBuildABot/tree/f89ecbf309e698cb9fccdfb64a6ff2a930e6541e)。最新提交时间（抓取时）：`2026-07-24T21:44:06Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-f89ecbf309e698cb9fccdfb64a6ff2a930e6541e.zip](<D:/研究生/Clash Royal/references/projects/Pbatch__ClashRoyaleBuildABot/source-f89ecbf309e698cb9fccdfb64a6ff2a930e6541e.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/Pbatch__ClashRoyaleBuildABot/inventory.json>)。24 个文件，21 个 Python 源码，0 个 C/C++ 源码。SHA256 `011c8b3eb77913f837b0154ccb5e6aafd4242268e4d9aca9e4eeb2d12a051318`。
许可证文件：[LICENSE.md](<D:/研究生/Clash Royal/references/projects/Pbatch__ClashRoyaleBuildABot/metadata/LICENSE.md>)

## Stable-Baselines-Team/stable-baselines3-contrib

用途：RL mask/循环工具。

可复用：MaskablePPO、RecurrentPPO。

依赖与兼容：SB3/PyTorch 同版匹配。

修改成本与限制：中：两类算法不可简单混为已支持循环 mask 的单一模型。

固定来源：[075bd5be8d43848b0f0cd3bc8a32f6892d0d58fb](https://github.com/Stable-Baselines-Team/stable-baselines3-contrib/tree/075bd5be8d43848b0f0cd3bc8a32f6892d0d58fb)。最新提交时间（抓取时）：`2026-06-15T17:04:20Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`full_source_archive`。省略：完整上游源码 archive，不含 Git 历史。
源码：[source-075bd5be8d43848b0f0cd3bc8a32f6892d0d58fb.zip](<D:/研究生/Clash Royal/references/projects/Stable-Baselines-Team__stable-baselines3-contrib/source-075bd5be8d43848b0f0cd3bc8a32f6892d0d58fb.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/Stable-Baselines-Team__stable-baselines3-contrib/inventory.json>)。108 个文件，58 个 Python 源码，0 个 C/C++ 源码。SHA256 `361a3d6b5181143db5759abb5f6a71ac7091f553ba34f9b4fc68ae2c4d247686`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/Stable-Baselines-Team__stable-baselines3-contrib/metadata/LICENSE>)

## takuseno/d3rlpy

用途：离线 RL 工具。

可复用：CQL/IQL、dataset/checkpoint/评价工具。

依赖与兼容：PyTorch/numpy、动作类型需核查。

修改成本与限制：中高：混合动作应先离散候选或适配动作头。

固定来源：[38c34b6f99fde82875e2ad81d1ba1f7a7da34137](https://github.com/takuseno/d3rlpy/tree/38c34b6f99fde82875e2ad81d1ba1f7a7da34137)。最新提交时间（抓取时）：`2025-09-10T23:06:16Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-38c34b6f99fde82875e2ad81d1ba1f7a7da34137.zip](<D:/研究生/Clash Royal/references/projects/takuseno__d3rlpy/source-38c34b6f99fde82875e2ad81d1ba1f7a7da34137.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/takuseno__d3rlpy/inventory.json>)。24 个文件，21 个 Python 源码，0 个 C/C++ 源码。SHA256 `a90aabbd88e746f942c753bfe5bb334cf2b19c5e67bfc935ebdd57e67b5a43fe`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/takuseno__d3rlpy/metadata/LICENSE>)

## ultralytics/ultralytics

用途：检测工具。

可复用：YOLO 训练/推理、数据格式与导出。

依赖与兼容：PyTorch/OpenCV，代码许可与商业条件需阅读原许可。

修改成本与限制：中：必须训练游戏类别和阵营；通用权重不等于游戏模型。

固定来源：[ab3bd58844b0fa5fda4c14523ad6de8c87687479](https://github.com/ultralytics/ultralytics/tree/ab3bd58844b0fa5fda4c14523ad6de8c87687479)。最新提交时间（抓取时）：`2026-10-13T15:35:33Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-ab3bd58844b0fa5fda4c14523ad6de8c87687479.zip](<D:/研究生/Clash Royal/references/projects/ultralytics__ultralytics/source-ab3bd58844b0fa5fda4c14523ad6de8c87687479.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/ultralytics__ultralytics/inventory.json>)。8 个文件，4 个 Python 源码，0 个 C/C++ 源码。SHA256 `0a48c0ff7ba192669d4fab3a9ca31bffcee5e3f92cb0ba79330e2b1929687bff`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/ultralytics__ultralytics/metadata/LICENSE>)

## voonhous/crforge

用途：皇室战争独立参考引擎。

可复用：Java 战斗tick、ZMQ Gym桥、机制差分。

依赖与兼容：Java17以上、Python Gymnasium/pyzmq；本机JDK21手动构建成功。

修改成本与限制：中高：缺正式snapshot，敌手隐藏字段需过滤；动作反馈提前判断有误，见审计。

固定来源：[b41cf9b6276945152ec4c869ce56f98a5fa3f513](https://github.com/voonhous/crforge/tree/b41cf9b6276945152ec4c869ce56f98a5fa3f513)。最新提交时间（抓取时）：`2026-09-20T16:53:32Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`crforge compiled, 974 tests passed and 3 full episodes`。
下载范围：`audited_upstream_source_snapshot`。省略：['.github/ISSUE_TEMPLATE/bug_report.yml', '.github/ISSUE_TEMPLATE/config.yml', '.github/ISSUE_TEMPLATE/feature_request.yml', '.github/dependabot.yml', '.github/pull_request_template.md', '.github/workflows/build-and-test.yml', 'desktop/build.gradle.kts', 'desktop/src/main/java/org/crforge/desktop/CRForgeGame.java', 'desktop/src/main/java/org/crforge/desktop/DesktopLauncher.java', 'desktop/src/main/java/org/crforge/desktop/render/AbilityOverlayRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/AoeDamageRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/AreaEffectOverlayRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/ArenaRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/CardLayout.java', 'desktop/src/main/java/org/crforge/desktop/render/DamageNumberRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/DebugOverlayRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/DebugRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/DeployOverlayRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/EntityRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/GhostFormation.java', 'desktop/src/main/java/org/crforge/desktop/render/HealthBarRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/HudRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/ProjectileRenderer.java', 'desktop/src/main/java/org/crforge/desktop/render/RenderConstants.java', 'desktop/src/main/java/org/crforge/desktop/render/RenderContext.java', 'desktop/src/main/java/org/crforge/desktop/render/StatusEffectRenderer.java', 'desktop/src/main/java/org/crforge/desktop/screen/AIGameScreen.java', 'desktop/src/main/java/org/crforge/desktop/screen/DebugGameScreen.java', 'desktop/src/main/resources/fonts/RobotoMono-Regular.ttf', 'desktop/src/test/java/org/crforge/desktop/render/RenderUnitConversionTest.java', 'scripts/start_training.sh', 'tools/formation_visualizer/README.md', 'tools/formation_visualizer/formation_visualizer.py', 'tools/formation_visualizer/formation_visualizer_img/barbarians_circle.png', 'tools/formation_visualizer/formation_visualizer_img/barbarians_normal.png', 'tools/formation_visualizer/formation_visualizer_img/elite_barbarians_circle.png', 'tools/formation_visualizer/formation_visualizer_img/elite_barbarians_normal.png', 'tools/formation_visualizer/formation_visualizer_img/royal_hogs_circle.png', 'tools/formation_visualizer/formation_visualizer_img/royal_hogs_normal.png', 'tools/formation_visualizer/formation_visualizer_img/royal_recruits_overlay.png', 'tools/formation_visualizer/formation_visualizer_img/royal_recruits_ref.jpg', 'tools/formation_visualizer/formation_visualizer_img/skarmy_reference.jpg']。
源码：[source-b41cf9b6276945152ec4c869ce56f98a5fa3f513.zip](<D:/研究生/Clash Royal/references/projects/voonhous__crforge/source-b41cf9b6276945152ec4c869ce56f98a5fa3f513.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/voonhous__crforge/inventory.json>)。354 个文件，15 个 Python 源码，0 个 C/C++ 源码。SHA256 `56701a4e09b03bfa139b00e92df8d9726d30b50950bc9bb7bf3fbe3c8172ebff`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/voonhous__crforge/metadata/LICENSE>)
源码及安装/运行实测：[CRFORGE.md](<D:/研究生/Clash Royal/docs/audits/CRFORGE.md>)。

## vwxyzjn/cleanrl

用途：RL 可读实现。

可复用：单文件 PPO、实验记录、算法对照。

依赖与兼容：PyTorch/Gymnasium 等按脚本安装。

修改成本与限制：中：手动接环境与 mask；便于从零教学。

固定来源：[fe8d8a03c41a7ef5b523e2e354bd01c363e786bb](https://github.com/vwxyzjn/cleanrl/tree/fe8d8a03c41a7ef5b523e2e354bd01c363e786bb)。最新提交时间（抓取时）：`2026-04-20T10:57:15Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-fe8d8a03c41a7ef5b523e2e354bd01c363e786bb.zip](<D:/研究生/Clash Royal/references/projects/vwxyzjn__cleanrl/source-fe8d8a03c41a7ef5b523e2e354bd01c363e786bb.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/vwxyzjn__cleanrl/inventory.json>)。12 个文件，9 个 Python 源码，0 个 C/C++ 源码。SHA256 `dedb73db4df1f0b83e159261b378ebd7cc8d331777817a71c7e23f7f9374353a`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/vwxyzjn__cleanrl/metadata/LICENSE>)

## wty-yy/Clash-Royale-Detection-Dataset

用途：皇室战争感知数据。

可复用：合成检测生成流程、类别映射与数据入口。

依赖与兼容：数据/游戏素材另外托管；源码 archive 不包含完整训练集。

修改成本与限制：中：检查素材许可和真实测试集再训练。

固定来源：[31b4151fedb1b914e99c3c122c16dca61cb2b905](https://github.com/wty-yy/Clash-Royale-Detection-Dataset/tree/31b4151fedb1b914e99c3c122c16dca61cb2b905)。最新提交时间（抓取时）：`2024-06-11T10:03:13Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_installed_or_executed_by_research_fetch`。
下载范围：`fixed_code_license_dependency_subset`。省略：unselected source, media, datasets and pretrained models; complete upstream git tree saved。
源码：[source-31b4151fedb1b914e99c3c122c16dca61cb2b905.zip](<D:/研究生/Clash Royal/references/projects/wty-yy__Clash-Royale-Detection-Dataset/source-31b4151fedb1b914e99c3c122c16dca61cb2b905.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/wty-yy__Clash-Royale-Detection-Dataset/inventory.json>)。4 个文件，0 个 Python 源码，0 个 C/C++ 源码。SHA256 `4c7012626494b2b968ae2cee4a7780ccd0fadbba4e0618c6eed69eed8c2cf195`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/wty-yy__Clash-Royale-Detection-Dataset/metadata/LICENSE>)

## wty-yy/Clash-Royale-Replay-Dataset

用途：皇室战争专家回放。

可复用：视频/序列数据说明、训练数据入口。

依赖与兼容：大录像/外链数据另下载；可能需额外工具。

修改成本与限制：高：事件对齐、版本、卡组与缺失观测。

固定来源：[ce86e10bedcf97762c3d207633f035cd12be8936](https://github.com/wty-yy/Clash-Royale-Replay-Dataset/tree/ce86e10bedcf97762c3d207633f035cd12be8936)。最新提交时间（抓取时）：`2024-05-12T08:38:22Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`not_unpickled_or_used_for_training`。
未完成：complete compressed expert dataset approximately 2.02GB not downloaded; object NPY payload not converted；小样本：[assets_manifest.json](<D:/研究生/Clash Royal/references/library/assets_manifest.json>)。

## wty-yy/KataCR

用途：皇室战争真实像素代理。

可复用：katacr 检测/OCR 融合、回放、离线策略。

依赖与兼容：JAX/Flax、检测/OCR、Linux 视频采集链；需 GPU/真实输入。

修改成本与限制：高：阵营/类别/设备/时间对齐；正文可复用，不能当完整服务器。

固定来源：[36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8](https://github.com/wty-yy/KataCR/tree/36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8)。最新提交时间（抓取时）：`2024-06-06T09:06:32Z`，这不是对项目活跃度的保证。

本机验证：source archive CRC, path safety, source inventory and license discovery only；安装/运行：`AST110 Python files and card cost utility import passed; realtime import missing cv2; Windows CUDA wheel probe incompatible`。
下载范围：`full_source_archive`。省略：完整上游源码 archive，不含 Git 历史。
源码：[source-36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8.zip](<D:/研究生/Clash Royal/references/projects/wty-yy__KataCR/source-36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8.zip>)；清单：[inventory.json](<D:/研究生/Clash Royal/references/projects/wty-yy__KataCR/inventory.json>)。176 个文件，124 个 Python 源码，0 个 C/C++ 源码。SHA256 `37ff54e10f5070b4b1efdfd1b0279fd7349bd7d5c2200679b9e217343f8f6ad4`。
许可证文件：[LICENSE](<D:/研究生/Clash Royal/references/projects/wty-yy__KataCR/metadata/LICENSE>)
源码及安装/运行实测：[KataCR.md](<D:/研究生/Clash Royal/docs/audits/KataCR.md>)。
