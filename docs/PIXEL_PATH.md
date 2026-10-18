# 真实像素路径的现状

主闭环使用本地玩家可见状态，像素路径尚未完成真实设备端到端验收。原MVP合成截图检测不作为真实截图成功证据。

真实模型资料：references/library/assets及outputs/research/vision_model_smoke.json。单位ONNX来自固定源码快照，SHA256 `4cf422370650b1d18647cffc05e02bf75fdc2fbdb1f8ef983f4b8d6f8ea908b3`。输入float16 `[1,3,480,352]`，输出float32 `[1,300,6]`。依上游实现每行是x1/y1/x2/y2/conf/class，没有阵营。模型内97类；class46内嵌hungry_dragon与源码baby_dragon有差异，保留两份标签，不静默替换。

独立.venv-vision通过CPU零帧3次推理约240/172/146ms。零帧只能验证ONNX接口，不证明真实游戏准确率。模型metadata写AGPL-3.0，与源码仓库MIT不同；权重再分发/集成应按模型自身许可核查，manifest已单列。

royale_lab/pixels.py提供GameFrameDetector协议与PixelAdapter。调用者必须完成截图来源、HUD手牌/圣水OCR、阵营判定、坐标标定、HP读取和稳定tracking ID。HUD必填缺失直接ValueError，不伪造为有效游戏观测。视觉出牌推断必须evidence=inferred，不能当作confirmed扣费；screen信念保持宽圣水区间。

后续验收需要合法真实截图标注集，报告mAP/OCR字准确率/阵营准确率/ID切换、延迟与下牌接受率。没有设备/录像时不把零帧或专家object-array文件当成完整像素复现。

## 两张真实数据集帧的实际推理

新增可复用命令行工具 [vision_infer.py](../tools/vision_infer.py)。输入和输出路径必须位于本项目内；默认固定上述ONNX和源码commit，强制核对模型及不可变源码ZIP的SHA256。工具使用独立 `.venv-vision`，依赖完整锁定在 [research_vision_requirements.lock.txt](../tools/research_vision_requirements.lock.txt)，包括 Pillow 11.3.0、NumPy 2.2.6、ONNX Runtime 1.22.1。

工具按固定 `unit_detector.py` 与 `onnx_detector.py` 实现：取原图纵向5%–80%，Pillow默认RGB缩放，整数截断尺寸，对称/奇数不对称padding值114，float16原地除255，CHW加batch，执行CPU推理；保留 `confidence > 0.3`；减padding后恢复原图坐标。输出包含300行原始模型结果、过滤后框、模型与源码两份标签、完整预处理参数、来源/权重/输入/输出SHA256。只在可视化时裁掉越界框，JSON另存未经裁边的上游坐标。两帧的输入张量、padding及全部300行框的逆变换，均与从固定源码ZIP提取的纯数值方法逐值相等；这些方法未执行设备控制、阵营分类或格点标定。

实际输入来自公开 [Clash-Royale-Detection-Dataset](https://github.com/wty-yy/Clash-Royale-Detection-Dataset) 的 `OYASSU_20210528_episodes/1/00000.jpg`、`00015.jpg`，均为568×896的真实竞技场截图。两张已下载JPEG与来源固定commit/文件Git身份见 [assets_manifest.json](../references/library/assets_manifest.json)。这属于历史真实帧，没有运行真实设备或提交游戏操作。

| 帧 | 严格阈值后的框 | 最大置信度 | 此次CPU推理延迟 | 证据 |
|---|---:|---:|---:|---|
| 00000 | 0 | 0.292969 | 173.05 ms | [JSON](../outputs/research/real_detection/00000/detections.json)、[标注图](../outputs/research/real_detection/00000/detections.png)、[模型输入](../outputs/research/real_detection/00000/model_input.png) |
| 00015 | 1 | 0.314697 | 174.71 ms | [JSON](../outputs/research/real_detection/00015/detections.json)、[标注图](../outputs/research/real_detection/00015/detections.png)、[模型输入](../outputs/research/real_detection/00015/model_input.png) |

第二帧输出class64 `minipekka`，原图框 `[434,628,485,683]`。人工查看标注图，框落在右下公主塔的造型上，疑似将塔皮肤当成了可移动单位。这里只报告可见异常，不把没有匹配真值的两个样本转换成准确率。保存的454项 `val_annotation_mini.txt` 划分名单均不含这两张图，且未下载对应bbox真值；JSON记录 `matched_split_list=false` 和 `metrics_computed=false`。因此没有mAP、召回率或OCR准确率结果。

另一个明确限制是输入域：数据集part2已经裁出竞技场，上游这套预处理面向包含HUD的全屏截图。再次取5%–80%会保留原图约y45–717，舍弃下方国王塔的一部分。工具为了验证指定上游链路保持该裁剪，标注图用蓝框展示ROI，JSON写明 `domain_compatibility=mismatch`。后续应取得匹配全屏数据，或者建立单独、经过标注评估的arena预处理配置，不能靠在本次推理中静默改裁剪来宣称复现成功。

在项目目录执行以下命令可复现两帧。检测结果中阵营、HP、tracking ID、格点都显式为null；手牌/圣水/HUD没有输出。因此该CLI尚不能满足 `GameFrameDetector` 的完整输入契约，也不能启动C路径闭环。

```powershell
$env:TEMP = 'D:\研究生\Clash Royal\tmp\research'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
.\.venv-vision\Scripts\python.exe -B tools\vision_infer.py --image references/library/assets/wty-yy__Clash-Royale-Detection-Dataset/images/part2/OYASSU_20210528_episodes/1/00000.jpg --output outputs/research/real_detection/00000
.\.venv-vision\Scripts\python.exe -B tools\vision_infer.py --image references/library/assets/wty-yy__Clash-Royale-Detection-Dataset/images/part2/OYASSU_20210528_episodes/1/00015.jpg --output outputs/research/real_detection/00015
```
