# Royale AI Lab：皇室战争 AI 研究原型

已经跑通两个本地闭环：**结构化局势 → 对手信息估计 → 合法动作搜索 → 模拟对局**，以及 **合成截图 → 手牌/数字/单位识别 → 决策 → ADB 点击命令预览**。

这是从零构建的 Windows/Python 研究 MVP。默认策略在近似模型中比较未来 8 秒的候选下牌；没有声称真实游戏的全局最优，也没有把模拟器胜率当成天梯胜率。当前环境未检测到 PATH 中可用的 ADB 命令，也未配置设备连接或提供真实对局截图，因此真实游戏自动操作尚未验证。

## 现在就运行

在当前文件夹打开 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m royale_ai simulate --seed 7
```

双击 `outputs/replay.html` 查看离线回放：可以播放、拖动时间轴，观察单位、塔血、当前手牌、对手圣水区间、决策原因和候选排名。生成的 `outputs/demo.jsonl` 包含可复现的逐步状态和动作。

也可以运行 `./run_demo.ps1`。若 PowerShell 不允许执行脚本，直接使用上面的 Python 命令。**核心模拟与搜索只依赖 Python 标准库**，也可用 `python -m royale_ai simulate`。

## 已实现的能力

| 模块 | 已有行为 | 重要范围 |
| --- | --- | --- |
| 战斗环境 | 两路、桥、6 座塔、圣水、4 手牌轮转、近战/远程/空中/建筑/法术 | 12 张基础卡，参数和机制经过简化 |
| 对手信息 | 已见卡牌、确认出牌去重、圣水区间、四卡轮转约束 | 截图新轨迹只作为弱证据，无法精确扣费 |
| 决策 | 等待/出牌/落点候选，合法性约束，两个对手假设，8 秒前瞻 | 有限候选和时间窗中的最高评分 |
| 感知 | 手牌模板、数字 OCR、单位轨迹、可选自定义 YOLO | 合成截图已测试；真实模板/模型未提供 |
| 控制 | ADB PNG 截图、像素坐标映射、命令预览、可选点击 | 默认 `dry-run`；未连接真实设备 |
| 学习 | 搜索教师日志 → 线性候选排名模仿模型 | 已训练示例模型，属于模仿学习，不是深度 RL |
| 实验 | 随机/规则/前瞻配对对比，测试、JSONL 日志、离线 HTML 回放 | 所有指标均来自近似模拟器 |

默认卡组：巨人、骑士、弓箭手、亡灵、火枪手、迷你皮卡、万箭齐发、火球。另有野猪骑士、加农炮、骷髅兵、女武神，用于第二种测试卡组。

## 常用命令

```powershell
# 四种战术场景：巨人来袭、空中群体、法术斩杀、低圣水等待
.\.venv\Scripts\python.exe -m royale_ai scenario giant
.\.venv\Scripts\python.exe -m royale_ai scenario air
.\.venv\Scripts\python.exe -m royale_ai scenario finish
.\.venv\Scripts\python.exe -m royale_ai scenario save

# 读取你提供的结构化局势 JSON
.\.venv\Scripts\python.exe -m royale_ai decide examples/giant.json

# 合成像素输入的端到端验证，需要 OpenCV 与 NumPy
.\.venv\Scripts\python.exe -m royale_ai vision-demo

# 配对实验：每种策略 12 局，起手随机种子、座位、对手和卡组保持可比
.\.venv\Scripts\python.exe -m royale_ai evaluate --games 12 --seed 10

# 从三个不同对局蒸馏搜索策略；按整局划分训练/验证
.\.venv\Scripts\python.exe -m royale_ai train outputs/demo.jsonl outputs/teacher_8.jsonl outputs/teacher_9.jsonl
.\.venv\Scripts\python.exe -m royale_ai simulate --policy models/linear_policy.json --log outputs/learned.jsonl --html outputs/learned.html

# 测试及完整验收；verify 需要 Node.js 检查回放脚本
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify.py
```

文件夹内已有 `.venv`，安装并验证了 `numpy==2.5.4` 和 `opencv-python==4.14.0.94`。重新安装时使用项目内缓存：

```powershell
New-Item -ItemType Directory -Force -Path .tmp,.cache | Out-Null
$env:TEMP = "$PWD\.tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-vision.txt
```

## 真实游戏接入

步骤见 [真实输入接入说明](docs/LIVE_SETUP.md)。已提供 `capture`、`observe`、`live` 命令、截图 ROI 标定工具和可选检测器训练入口。真实接入需要设备/模拟器的 ADB、实际分辨率模板、游戏专用单位模型和卡牌数值标定。原始开源权重的类别格式未必兼容这里的 YOLO 接口。

自动操作涉及 Supercell 的服务规则；官方条款列明禁止未经授权的自动化和 bots，可能限制账号。研究优先使用本地模拟器与离线数据。[官方服务条款](https://supercell.com/en/terms-of-service/)

## 项目结构

```text
royale_ai/              状态、卡牌、模拟器、信念、策略、感知、控制、学习、CLI
royale_ai/data/         可编辑的 12 卡研究参数
tests/                 核心机制、战术、控制与合成视觉测试
tools/                 验收、引用下载、成本审计、ROI 标定、可选检测器训练
examples/              完整状态 JSON 样例
docs/                  文献调研、问题建模、架构、接入、实验与后续路线
references/papers/     4 篇论文原文
references/upstream/   固定版本 MIT 许可证及 KataCR 卡牌常量参考
references/manifest.json  来源、commit、SHA256、下载记录
models/                已训练的线性模仿模型；未来的视觉权重也放这里
outputs/               验收结果、对局日志、决策、HTML 回放与合成截图
.venv/ .cache/ .tmp/    项目内环境、缓存与临时文件
```

所有项目写入路径通过 `royale_ai.paths` 限制在当前文件夹；论文下载器也只向 `references` 写入。不创建其他项目文件夹，不使用云服务保存结果。

先读 [问题建模](docs/MODELING.md)，再读 [文献与开源调研](docs/RESEARCH.md)、[架构与数据接口](docs/ARCHITECTURE.md)、[实验记录](docs/EXPERIMENTS.md) 和 [迭代路线](docs/ROADMAP.md)。
