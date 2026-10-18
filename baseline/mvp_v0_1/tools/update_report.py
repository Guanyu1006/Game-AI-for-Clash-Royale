"""Generate the experiment note from actual local artifacts."""
from pathlib import Path
import json
import re
ROOT=Path(__file__).resolve().parent.parent
load=lambda p:json.loads((ROOT/p).read_text(encoding='utf-8'))
evaluation=load('outputs/evaluation.json')
model=load('models/linear_policy.json')['report']
verification=load('outputs/verification.json')
vision=load('outputs/vision_demo/result.json')
test_log=(ROOT/'outputs/verify_0.txt').read_text(encoding='utf-8')
match=re.search(r'Ran (\d+) tests',test_log)
tests=int(match.group(1)) if match else 0
lines=[]
for name,row in evaluation['summary'].items():
    lines.append(f"| {name} | {row['wins']} | {row['draws']} | {row['losses']} | {row['win_rate']:.1%} | {row['invalid_actions']} | {row['median_latency_ms']:.3f} |")
learned_path=ROOT/'outputs/learned.jsonl'
learned=json.loads(learned_path.read_text(encoding='utf-8').splitlines()[-1])['metrics'] if learned_path.exists() else None
text=f'''# 实验与验收记录

运行日期：2026-10-13。Windows，Python 3.14.5；视觉依赖 NumPy 2.5.4、OpenCV 4.14.0.94。硬件型号未记录，耗时不用于跨机器比较。

## 自动验收

- `{tests}` 项单元与集成测试通过，无跳过；包含卡费/轮转、非法动作不改状态、恢复阶段、事件去重、可见信息边界、确定性、空中目标、法术、塔结局、弱视觉证据、坐标映射和 OCR。
- `tools/verify.py` 的 `{len(verification)}` 个命令均以 0 退出。命令和输出在 `outputs/verification.json` 及 `outputs/verify_*.txt`。
- 12 张基础卡费与固定版本 KataCR 表一致，`outputs/card_cost_audit.json` 无不一致。核对的是该 commit，不是最新平衡版本。
- 合成图像 `outputs/vision_demo/frame.png` 识别出 4 手牌、7 圣水、6 塔 HP 和 3 个单位；生成 `{vision['decision']['card']}` 决策，控制器为 `{vision['controller']['status']}`。
- 回放的离线脚本检查通过：JSON 解析、首次 canvas 绘制、选帧与播放/暂停。应用浏览器拒绝访问 file 协议，因此没有完成浏览器视觉布局验收，也没有绕过限制。

## 配对策略评估

每种策略 12 局，共 36 局；起手 seed=10–21，决策周期 2 秒，单局上限 180 秒，搜索窗口 8 秒，对手固定 greedy，座位交替，巨人/野猪对手卡组每两局切换。不同策略使用相同 seed、座位、卡组与规则。

| 策略 | 胜 | 平 | 负 | 胜率 | 非法动作总数 | 单局中位延迟的中位值 / ms |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(lines)}

这些是当前简化引擎及 12 个固定条件下的结果。样本小，对手单一，环境与搜索共用近似规则；没有证明对未见卡组、人类或真实天梯的泛化。规则策略对自己不一定 50%，因为起手、卡组、座位及固定处理顺序不同。

完整每局结果、塔血和耗时在 `outputs/evaluation.json`。模拟器提前结束只支持国王塔被摧毁；超时的拆塔/血量判定不是官方决胜规则。

## 战术场景

`examples/giant.json`、`air.json`、`finish.json`、`save.json` 可输入 decide 命令。巨人场景会选迷你皮卡；空中场景以少受塔伤检验，地面单位也可能通过吸收伤害配合塔完成防守；残局选择能斩杀的法术；低圣水场景等待。

场景期望只对本项目参数成立。特别是空中防守不能只用“选的牌是否能对空”判断好坏，必须比较实际塔伤和资源代价。底层测试另外验证地面近战单位不能直接攻击空中单位。

## 模仿模型

使用 seed=7、8、9 的搜索教师日志，按整局分割：训练 {model['train_samples']} 个决策，验证 {model['validation_samples']} 个决策。

- 训练 top-1 动作一致率：{model['train_top1_accuracy']:.1%}。
- 验证总 top-1 一致率：{model['validation_top1_accuracy']:.1%}。
- 验证等待比例：{model['validation_wait_fraction']:.1%}。
- 验证出牌决策仅 {model['validation_play_samples']} 个，出牌 top-1 一致率：{model['validation_play_top1_accuracy']:.1%}。
- 单独 seed=31 的学习模型试跑结果：{learned['result'] if learned else '未运行'}，非法动作 {learned['invalid_actions'] if learned else '未记录'}。一局仅检查推理接口，无棋力统计意义。

模型文件 `models/linear_policy.json` 包含特征版本、卡牌顺序、权重和训练报告。大量等待会抬高总准确率，当前出牌样本少且效果弱，所以默认仍使用搜索策略。没有部署或训练深度强化学习模型。

## 可复现命令

```powershell
.\\.venv\\Scripts\\python.exe tools/verify.py
.\\.venv\\Scripts\\python.exe -m royale_ai simulate --policy models/linear_policy.json --seed 31 --log outputs/learned.jsonl --html outputs/learned.html
.\\.venv\\Scripts\\python.exe tools/update_report.py
```

## 未完成的真实验收

未配置 ADB/设备连接，未提供真实对局截图、游戏专用视觉模型或逐卡动力学标定。真实 capture/live 与检测器训练入口尚未在设备/GPU 上运行。合成视觉通过不能证明真实画面识别准确；当前 screen 模式单位血量估为满血、圣水隐藏区间保守为 [0,10]。
'''
(ROOT/'docs/EXPERIMENTS.md').write_text(text,encoding='utf-8')
print('Updated docs/EXPERIMENTS.md')
