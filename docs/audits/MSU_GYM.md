# MSU-AI / clash-royale-gym：实测为未完成的接口骨架

审计日期：2026-10-13。固定源码为 [1cd23be16e10b25c5a5a0889626a863092f32615](https://github.com/MSU-AI/clash-royale-gym/tree/1cd23be16e10b25c5a5a0889626a863092f32615)，该提交时间为 2024-03-12，MIT 许可证。源码在 `third_party/audit_candidates/clash-royale-gym`，许可证在同目录 `LICENSE`。

结论：不作为本项目战斗后端。README 的 RGB 图像观测与 2304 个动作没有落实到这个固定版本的实际环境。可参考 Gymnasium 包注册、模块命名及草拟接口，但修复成本相当于重新实现引擎。

| 检查 | 实际证据 |
|---|---|
| 环境注册 | `clash_royale/__init__.py` 注册 `clash-royale` |
| 动作与观测 | `clash_royale/envs/clash_royale_env.py` 实际为 `Discrete(1)` 和空 `Dict()` |
| reset / step / render | 全部是 `pass`，直接调用返回 `None` |
| 战斗推进 | `game_engine/arena.py` 的 `step`、`play_card`、`reset` 是 `pass` |
| 实体、塔和结果 | `get_entities` 固定 `[]`，`tower_count` 和 `lowest_tower_health` 固定 0 |
| 卡牌 | `game_engine/card.py` 是 pseudo Card，没有版本化卡牌行为目录 |
| 攻击逻辑 | `logic/attack.py` 的范围判断使用 `attack_range <= distance`，与通常的射程内判断相反；未执行此攻击场景，属于静态风险 |
| 快照恢复 | 没有可用实现 |
| 吞吐 | 无真实完整战斗，无法给出战斗吞吐 |

在项目 `.venv` 的 Python 3.14 上普通安装试图下载 pygame 源码，二进制安装检查明确没有对应 pygame wheel；日志见 `outputs/audit_candidates/gym_binary_install.log`。随后使用系统已有 Python 3.11 创建项目内 `.venv-audit`，实际安装该源码包和其依赖成功：pygame 2.6.1、gymnasium 1.4.0、numpy 2.4.6。日志见 `gym_install_py311.log`。

```powershell
py -3.11 -m venv .venv-audit
.venv-audit\Scripts\python.exe -m pip install .\third_party\audit_candidates\clash-royale-gym
.venv-audit\Scripts\python.exe tools\audit_gym_smoke.py
```

`gym.make` 首先因空 `Dict` 观测空间触发 `AssertionError: An empty Dict observation space is not allowed.`。仅为检查占位行为关闭环境检查器后，reset、step、render 均返回 `None`。结构化结果为 `outputs/audit_candidates/gym_smoke.json`。本机验证是安装成功、运行接口失败，不能写成已跑通 RL 环境。

原始说明可访问 [上游 README](https://github.com/MSU-AI/clash-royale-gym/blob/1cd23be16e10b25c5a5a0889626a863092f32615/README.md)；本报告以固定源码和实际执行输出为准。
