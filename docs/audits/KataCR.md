# KataCR：真实像素与动作适配资料，而非本地战斗模拟器

审计日期：2026-10-13。固定提交 [36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8](https://github.com/wty-yy/KataCR/tree/36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8)，提交日期 2024-06-06，MIT 许可证。实际源码位于 `third_party/audit_candidates/KataCR`；固定源码的完整研究库下载由文献库 manifest 记录。本审计不将本目录曾中断的 ZIP 当作成功下载证据。

结论：适合后续 C 路径的屏幕感知、离线轨迹处理和动作适配。它没有自己计算碰撞、伤害和胜负的本地 battle loop；游戏本身仍由手机上的原游戏运行。因此不能把它用作可加速、可分叉的 headless 模拟器。

| 可复用内容 | 真实源码位置 | 接入成本和限制 |
|---|---|---|
| 屏幕融合与追踪 | `katacr/policy/visualization/visual_fusion.py` | 依赖检测、分类、OCR 权重与屏幕比例标定 |
| 状态/动作/奖励构造 | `katacr/policy/perceptron/{state,action,reward,sar}_builder.py` | 输入是视觉推断，应保留置信度与推断来源 |
| 离线决策模型 | `katacr/policy/offline/{dt,starformer,starformer_2L,vidformer}.py` | JAX/Flax 训练栈、录像轨迹、checkpoint 路径需迁移 |
| 实机闭环 | `katacr/policy/env/interact_env.py`、`sar_daemon.py` | 依赖真实手机屏幕流、模型和 ADB；本机没有完成这种实机运行 |
| 实际下牌 | `katacr/policy/env/utils.py::tap_screen` | 执行 `adb shell input tap`；`InteractEnv._act` 先选卡、再点击落点 |
| 录像环境 | `katacr/policy/env/video_env.py` | 从录像构建观测，step 只是读取下一帧并打印预测，不会让录像因动作改变 |
| 卡牌元数据 | `katacr/constants/card_list.py` | utility import 实测成功，126 个费用条目；条目不是行为实现 |

`SARDaemon` 明确调用 `cv2.VideoCapture(2)`，默认假定 Linux `v4l2loopback` + scrcpy 的视频流；失败会断言手机流不能连接。`InteractEnv` 使用多进程队列等待推断结果，不能在没有感知进程的情况下当作普通模拟环境调用。`offline/eval.py` 默认指向作者本地 `logs/Policy/.../ckpt`，这些路径不是本机已有权重。

本机验证执行了 `tools/audit_katacr_smoke.py`：110 个 Python 源文件 AST 解析成功，卡牌费用 utility 导入成功；在隔离 Python 3.11 环境中实时环境导入止于未安装 cv2。更实质的复现障碍来自原始 `requirements.txt` 中 Linux CUDA 特定包 `jaxlib==0.4.26+cuda12.cudnn89` 和 `paddlepaddle_gpu==2.6.1.post120`。实际执行二进制安装的 dry-run 检查，前者在当前 Windows/PyPI 组合没有匹配分发，见 `outputs/audit_candidates/KataCR_windows_install_probe.log`。没有为这项审计下载庞大的 CUDA 训练栈，也没有宣称安装完整依赖成功。

```powershell
.venv-audit\Scripts\python.exe tools\audit_katacr_smoke.py
.venv-audit\Scripts\python.exe -m pip install --dry-run --no-deps --only-binary=:all: 'jaxlib==0.4.26+cuda12.cudnn89'
```

结果：`outputs/audit_candidates/KataCR_smoke.json`。没有本机完整对局吞吐、可回滚快照或官方交互对照的实测值。迁移时先将 ADB executor、视觉 observation 和 belief 接入本项目已有接口，再替换训练模型；不要直接复用 `VideoEnv` 冒充动作会改变游戏的环境。

方法背景来自作者原始研究 [Playing Non-Embedded Card-Based Games with Reinforcement Learning](https://arxiv.org/abs/2504.04783)；论文结果属于作者实验，本报告只有上述安装/utility 证据。
