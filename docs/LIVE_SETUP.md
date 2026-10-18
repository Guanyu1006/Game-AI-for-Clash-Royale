# 真实截图与设备接入

本地模拟闭环和合成像素闭环已经验证。这里的 ADB、真实 YOLO 和手动标定入口是已实现但尚未进行真实设备验收的接口；当前项目没有真实模型权重或设备截图。

## 1. 设备与截图

将 Android 官方 platform-tools 或模拟器提供的 ADB 放到本项目 `tools/adb/` 下，或使用已安装 ADB 的绝对路径。开启设备允许的调试连接并取得 serial。无需游戏私有 API、注入或读取内存。

```powershell
.\tools\adb\adb.exe devices
.\.venv\Scripts\python.exe -m royale_ai capture --adb tools/adb/adb.exe --serial emulator-5554 --output data/screenshots/frame.png
```

示例 serial 仅是占位，必须替换为 `adb devices` 实际返回的值。`capture` 用 `exec-out screencap -p` 保存原始 PNG，不经过 PowerShell 文本重定向。[Android 官方说明](https://developer.android.com/tools/adb)

代码为自己启动的 ADB 设置项目内 `ANDROID_USER_HOME` 和 `ANDROID_SDK_HOME`。若系统已有 ADB server，其环境由启动它的进程决定；不要把项目内设置误当成现有 server 已迁移。这里没有启动或终止系统 ADB server。

## 2. 标定布局和手牌

```powershell
.\.venv\Scripts\python.exe tools/calibrate.py data/screenshots/frame.png --hand giant knight archers minions
```

工具用 OpenCV 依次选择：竞技场、四个手牌图案、对局专属稳定图标、我方圣水数字和六座塔的 HP 数字。按图上的当前四张牌修改 `--hand`，不要按照卡组顺序盲填。

模板保存到 `data/templates/live/`，配置到 `config/live.json`。循环到其余卡牌后截取另一帧，再运行：

```powershell
.\.venv\Scripts\python.exe tools/calibrate.py data/screenshots/frame2.png --hand musketeer mini_pekka arrows fireball --update-cards-only
```

模板尽量只包含稳定卡牌图案，排除费用覆盖、选中高亮、圣水不足灰化等会变化的内容。当前实现每张卡只存一个模板，灰化或特殊效果可能使它拒绝识别；后续应改成多模板或手牌分类器。

## 3. 数字与塔状态

当前 OCR 是字形模板匹配：从实际字体制作 `digits/0.png` 至 `digits/9.png`，每张包含一个独立白色数字、暗背景。现有阈值适配白字，不自动适配所有游戏皮肤和字体；必须使用真实画面检查误识别。

配置 `towers` 中应填写实际等级对应 `max_hp`，以及需要修正的归一化 `(x,y)`。默认最大塔血 2500/4000 是模拟器近似值；真实读数超过该值会拒绝观测。被拆塔需要在状态接口中提供 HP=0；当前六个固定数字 ROI 的实现没有自动补全消失的塔血，是一个真实接入限制。

`image_size` 必须与截图完全一致，不会悄悄缩放。配置示例只是字段说明：

```json
{
  "image_size": [540, 960],
  "arena": [90, 100, 450, 740],
  "slots": [[30,820,135,920],[155,820,260,920],[280,820,385,920],[405,820,510,920]],
  "towers": {"0_left": {"max_hp": 2500, "x": 3.5, "y": 26.5}},
  "units": {
    "backend": "yolo",
    "model": "models/clash_detector.pt",
    "confidence": 0.8,
    "classes": {
      "enemy_giant": {"card": "giant", "team": 1},
      "friendly_giant": {"card": "giant", "team": 0}
    }
  },
  "calibrated": false
}
```

其余字段由工具生成。上述 ROI、塔血和两个类别不是可直接用于任何真实分辨率的配置。

## 4. 单位检测模型

需要**游戏专用**模型；通用 COCO YOLO 不认识皇室战争单位。本项目可选 Ultralytics 后端要求模型输出可映射到“卡牌类别 + 阵营”的类别，例如 `friendly_giant` 与 `enemy_giant`，单位位置取边框底部中心。空中单位、投影和大的精灵需要进一步标定落点。

KataCR 的模型使用自己的标签和阵营格式，不能承诺把它的 `.pt` 文件改个名字就能直接运行。应写转换适配器，或用统一的双阵营标签重新训练。模型推理接口依据 [Ultralytics 官方文档](https://docs.ultralytics.com/modes/predict)。

可选训练入口（尚未在本机训练深度检测模型）：

```powershell
# 在兼容 PyTorch 的本地 Python 环境中安装 Ultralytics，环境也须放在项目内。
# model 和 dataset YAML 必须已经在本项目内；脚本不隐式下载基础权重。
python tools/train_detector.py --model models/base_detector.pt --data data/detection/dataset.yaml --epochs 50 --device cpu
```

训练输出在 `outputs/detector/`。数据集目录、train/val 文件和权重都应放在当前项目内；按整场视频划分训练与验证。Ultralytics/PyTorch 的兼容版本和许可证需按实际选择检查，不与已验证的 NumPy/OpenCV 环境混为一谈。

## 5. 先观察，再闭环

```powershell
.\.venv\Scripts\python.exe -m royale_ai observe data/screenshots/frame.png --config config/live.json --elapsed 30
```

检查 `outputs/observation.json` 的四张手牌、圣水、6 塔 HP、单位阵营和位置。`--elapsed` 是真实对局已进行秒数；中途启动时不能默认 0。当前没有识别比赛倒计时，因此必须显式给出起始时间，主循环随后用单调时钟推进。

```powershell
# 只截图、分析和生成点击建议
.\.venv\Scripts\python.exe -m royale_ai live --config config/live.json --adb tools/adb/adb.exe --serial emulator-5554 --elapsed 30 --frames 30
```

默认不会发送 taps。画面不是对局、手牌模糊、数字无法读取或分辨率错误时不产生操作。日志在 `outputs/live.jsonl`，最新截图在 `data/screenshots/latest.png`。

确认实际模板、所有模型类别、塔等级和落点正确后，配置 `calibrated=true`，才可使用已提供的显式执行入口：

```powershell
.\.venv\Scripts\python.exe -m royale_ai live --config config/live.json --adb tools/adb/adb.exe --serial emulator-5554 --elapsed 0 --frames 30 --execute
```

执行前再次检查动作合法性与置信门限，拒绝 1.5 秒以前的观测，并限制两次出牌至少相隔 1 秒。这里没有实现动作执行后的手牌变化确认、重连、自动开局、多局状态重置和完整比赛阶段管理；应在真实数据验证后再加入。

自动化涉及官方服务规则限制，不能仅凭“训练师模式”认定已获平台授权。[Supercell 服务条款](https://supercell.com/en/terms-of-service/)
