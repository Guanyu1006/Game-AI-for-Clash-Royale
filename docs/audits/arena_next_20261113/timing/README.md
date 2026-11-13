# 时序策略的冻结实验与数值证据

本目录记录同版本规则教师的双臂模仿实验，实验主说明见 [`arena_lab/TIMING_ZH.md`](../../../../arena_lab/TIMING_ZH.md)。人类高手身份、官方版本等价、截图识别和真实设备闭环均有各自的验收，不能由这里的离线指标代替。

- `experiment-plan.json`：两臂任何最终测试发生前冻结的训练参数、整局划分、主要指标和整局 bootstrap 规则。
- `dataset-index.json`：本轮 80 个整局、160 个双方视角文件的原始索引副本及 SHA。它保留原始相对路径，不是将轨迹数据嵌入这个审计目录。
- `frozen-source.json` 与 `frozen_source/`：本轮两臂实际使用的三份训练源码字节快照。后续报表字段修正不会回写这些快照。
- `candidate/`：时序模型原始报告、测试前冻结参数、测试数值预测、输入实体消融预测、恢复与部署接口审计。
- `reference/`：同数据、同预算联合分类基线的对应证据。
- `comparison.json`：以完整原始对局为重采样单位的配对比较，两个玩家视角始终一同抽取。

每个分组的 `files.json` 记录原路径、字节数和 SHA256。原始运行目录保留在 `outputs/arena-next-20261113/`；本目录中的报告与预测是字节相同的副本，未调整阈值、选择轮次或修改测试结论。`.npz` 仅包含数值数组，读取使用 `numpy.load(path, allow_pickle=False)`。

测试数据在模型选择和事件校准之前已按完整对局隔离。候选按训练 monitor 选择模型，仅在 5 个 calibration 局拟合风险率参数和事件阈值；另外 5 个 validation-check 局只用于检查。两臂最终各在同一批 10 个全新测试局评分一次。旧高手重建实验中已查看的测试局均未参与本次实验。

运行原始比较器需要完整复现后的数据索引、源 NPZ 与两臂权重。比较器会重新验证源文件 SHA、动作／位置标签、时间、可行动 mask、轨迹归属，以及测试前模型冻结文件；单凭这个小型证据目录不宣称已经重跑训练或源数据验收。复现命令见主说明。

原实验 `report.json` 顶层 `calibration` 指校准分组指标。真正用于部署的校准参数保存在 `frozen-before-test.json` 的 `calibration`、checkpoint metadata 的 `event_calibration`，并在独立审计摘要中显式列为 `calibration_parameters`。这些来源应一致，不能将校准分组得分误读为阈值参数。

本次主要负结果：完整动作精确率为时序候选 40.21%、joint 基线 43.93%；配对差值 -3.72 个百分点，95% 整局 bootstrap 区间为 [-5.98, -1.13] 个百分点。事件 precision 同样较低，而 recall、条件选牌和事件 NLL 的差值区间跨零。本轮独立结构对照没有显示时序候选的收益，不能以候选相比早期不同数据／预算实验的成绩变好来替代本次对照。

`report-field-fix.json` 记录两臂完成后进行的单个报表键修正及修正前后源码 SHA；`contract-tests-final.log` 是修正后的 12 项契约测试记录。两臂原报告及模型没有因此重跑。`comparison-source.py.txt` 保存了实际比较器源码，和训练阶段冻结源码分别标记。
