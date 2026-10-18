# 已保存的数据、权重与恢复下载

资产清单为 [assets_manifest.json](<D:/研究生/Clash Royal/references/library/assets_manifest.json>)，包含固定 commit、原始 URL、上游 Git blob SHA1、本地 SHA256、大小、许可与验证状态。下载数据与模型不表示已经完成真实游戏训练/识别。

## 真实专家回放：2 个小样本

来源为 [Clash-Royale-Replay-Dataset](https://github.com/wty-yy/Clash-Royale-Replay-Dataset/tree/ce86e10bedcf97762c3d207633f035cd12be8936)，固定 `ce86e10bedcf97762c3d207633f035cd12be8936`。本机保存：

- `fast_hog_2.6/OYASSU_20230630_episodes_5.npy.xz`：198000 字节，解压约 1.30 MB。
- `fast_hog_2.6/OYASSU_20231105_episodes_9.npy.xz`：614388 字节，解压约 2.57 MB。

路径在 `references/library/assets/wty-yy__Clash-Royale-Replay-Dataset/`。已验证固定 Git blob、XZ 解压与 NPY header。二者均为 `descr='|O'` 的 object NPY scalar，数据载荷没有 unpickle，没有声称已转换为当前 encoder 格式。后续转换应在确定内容与来源后，导出清楚的 episode/时间/观测/动作/回报 schema，并检查卡组与版本。

固定仓库完整树有 347 个数据文件，合计 **2016380664 字节**，约 2.02 GB 压缩数据；树中未找到 LICENSE。该资源主要是数据，并不是独立战斗引擎。资料库把完整 archive 下载失败和当前小样本成功分开记录，不把没有代码文件的仓库伪造为已下载完整软件。

## 游戏检测数据：2 张原始竞技场帧和格式参考

来源为 [Clash-Royale-Detection-Dataset](https://github.com/wty-yy/Clash-Royale-Detection-Dataset/tree/31b4151fedb1b914e99c3c122c16dca61cb2b905)，固定 `31b4151fedb1b914e99c3c122c16dca61cb2b905`，仓库 LICENSE 为 MIT；游戏图像的原始来源另由数据作者说明。

保存的两张图为 `images/part2/OYASSU_20210528_episodes/1/00000.jpg` 和 `00015.jpg`，约 69/72 KB；保存 `Clash Royale dataset annotation.md` 和 `images/part2/val_annotation_mini.txt` 供研究标签格式。全部核验上游 blob 身份和本地 hash，图像核验 JPEG header。这两张示例并不保证出现在 mini validation list 中，不能直接据此计算模型 mAP。

固定数据树有 26798 个文件，合计 **667657459 字节**（文件体积求和，不是压缩 archive 大小）。完整数据未下载；已保留 tree、重要源文件/README/LICENSE，以及实际小样本。论文区分合成切片训练与人工真实帧验证，本项目也必须保持此边界。

## 游戏专用单位检测 ONNX：来源及接口已验证

文件：[units_M_480x352.onnx](<D:/研究生/Clash Royal/references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx>)，来自 [固定原生仓库](https://github.com/itzik123/ClashRoyaleAi/tree/2f172925a15b17f973291f41a789b19a29f0fc43)。大小 30896276 字节；SHA256 为 `4cf422370650b1d18647cffc05e02bf75fdc2fbdb1f8ef983f4b8d6f8ea908b3`。已核验固定 Git blob 身份，不是替代性的通用 COCO 权重。

独立项目内 `.venv-vision` 使用 Python 3.11、ONNX 1.18.0、ONNX Runtime 1.22.1、numpy 2.2.6；完整依赖锁在 [research_vision_requirements.lock.txt](<D:/研究生/Clash Royal/tools/research_vision_requirements.lock.txt>)。

| 检查 | 本机结果 |
| --- | --- |
| ONNX checker | 通过；IR 10、opset 19 |
| 执行 provider | CPUExecutionProvider |
| 输入 | `images`，float16，`[1,3,480,352]`，H=480、W=352 |
| 输出 | `output0`，float32，`[1,300,6]` |
| 行字段解释 | 上游 UnitDetector 采用 `left,top,right,bottom,conf,cls` |
| 零张量推理 | 3 次输出均 finite，最新本机约 133-144 ms；并非正式 benchmark |
| 类别 | 内嵌 metadata 有 97 个名字 |
| 标签核对 | 96 项相同；索引 46 内嵌 `hungry_dragon`、源码 `baby_dragon` |
| 真实识别准确度 | 未验证 |
| 缺失闭环字段 | 单位模型本身不提供手牌、圣水、塔血、阵营/出牌事件可信度 |

结果为 [vision_model_smoke.json](<D:/研究生/Clash Royal/outputs/research/vision_model_smoke.json>)，标签和来源为 [unit_detector_labels.json](<D:/研究生/Clash Royal/references/library/assets/unit_detector_labels.json>)。优先使用模型内嵌标签，以显式 alias 对应源码的 baby_dragon；不能假定名字差异已经由真实图片验证。

模型内嵌 metadata 声明 **AGPL-3.0**，描述为 2024-08-31 导出的 Ultralytics YOLOv10m。周围原生仓库 MIT 不能覆盖模型自身许可。类别/游戏版本、后处理阈值、坐标 ROI、阵营及多单位关联仍需真实样本检验。

## 下载和验证命令

```powershell
.\.venv\Scripts\python.exe -B tools\research_assets.py
.\.venv-vision\Scripts\python.exe -B tools\research_vision_smoke.py
```

已完成资产按固定路径/hash复用，临时和缓存仍只写项目。手动增加专家样本可以用 `--replay-count 12 --skip-model`；完整专家数据恢复用：

```powershell
.\.venv\Scripts\python.exe -B tools\research_assets.py --all-replays --skip-model
```

`--all-replays` 明确要求大型下载，脚本先检查预计缺失体积与磁盘，并逐文件保存、记录异常；重跑跳过已存在且 blob 身份匹配的资源。格式验证的解压上限为 100 MB/文件，超过此值记为格式/预算限制，保留压缩原件与来源。选择更多样本后仍须按完整 episode 划分训练/验证。

若重新创建视觉环境，请先按项目方法找到已存在的 Python 3.11，仅在项目内创建 `.venv-vision`，将 `PIP_CACHE_DIR` 与 `TEMP/TMP` 指向项目 `.cache/research/pip` 和 `tmp/research`，再安装锁文件。官方使用方式依据 [ONNX Runtime Python 文档](https://onnxruntime.ai/docs/get-started/with-python.html) 和 [InferenceSession API](https://onnxruntime.ai/docs/api/python/api_summary.html)。
