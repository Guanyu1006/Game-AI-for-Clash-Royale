# 方法比较与真实优化入口

此表区分已有接口与拟研究算法。出现论文名字不表示平台已实现该论文。运行状态应查看项目实验日志和主状态文档。

| 要解决的问题 | 备选方法/原始论文 | 所需数据/接口 | 本项目真实入口 | 首要评价 | 主要成本/风险 |
| --- | --- | --- | --- | --- | --- |
| 卡牌×位置×等待的巨大空间 | [SEAT](https://www.ijcai.org/proceedings/2019/0631.pdf)、[P-DQN](https://arxiv.org/abs/1810.06394) | 可见状态、合法卡牌/区域、动作接受反馈 | `royale_lab/contracts.py` Action；`policies.py` 候选 | 候选命中、非法动作、延迟 | 细坐标过多使搜索慢；分解会忽略卡/位置耦合 |
| 不能执行的动作过多 | [action masking](https://arxiv.org/abs/2006.14171) | 自身信息与规则 mask | `backend.py` 合法性；`learning.py` 分布 | 拒绝率、KL、动作覆盖 | mask 采样/训练不一致；过期观测 |
| 不知道对手圣水/手牌 | [POMCP](https://papers.nips.cc/paper_files/paper/2010/file/edfbe1afcf9246bb0d40eb4d8027d90f-Paper.pdf)、[DRQN](https://arxiv.org/abs/1507.06527) | 可见出牌/时间历史、生成式模型或序列数据 | `memory.py` Belief；`policies.py` | 区间覆盖、反制成功、去信念消融 | 漏检、粒子退化；递归回放不跨局 |
| 搜索预算与长期价值 | [AlphaZero](https://arxiv.org/abs/1712.01815)、[MuZero](https://arxiv.org/abs/1911.08265) | snapshot/restore、rollout、value target | `backend.py` 快照；`policies.py` 搜索 | 固定 p95 延迟下胜率、塔伤 | 近似机制被搜索过度利用 |
| BC 自己造成新局势后失效 | [DAgger](https://arxiv.org/abs/1011.0686) | 可查询搜索教师、学习者对局 | `learning.py` 轨迹/教师；`session.py` | 未见局势恢复、查询成本 | 教师本身近似、标签预算大 |
| 固定回放中 Q 外推 | [CQL](https://arxiv.org/abs/2006.04779)、[IQL](https://arxiv.org/abs/2110.06169) | 数据内合法动作/回报/终局 | `learning.py` 新训练器；`rewards.py` | 同数据胜率、Q 排序校准 | 没有覆盖的反制难学习 |
| 历史与等待预测 | [DT](https://arxiv.org/abs/2106.01345)、[KataCR](https://arxiv.org/abs/2504.04783) | episode tokens、delay 标签、因果 mask | `learning.py`；`session.py` 时间戳 | WAIT/出牌分项、时机 MAE | 同局帧泄漏、目标回报不可实现 |
| 从环境奖励继续改善 | [PPO](https://arxiv.org/abs/1707.06347)、[GAE](https://arxiv.org/abs/1506.02438) | 有 mask 的 env、reward、checkpoint | `learning.py`；`rewards.py` | 独立胜率、KL/entropy 曲线 | 长局信用分配、奖励钻空子 |
| 对固定对手过拟合 | [PSRO](https://arxiv.org/abs/1711.00832)、[NFSP](https://arxiv.org/abs/1603.01121) | checkpoint 池、交叉 payoff | `session.py`；训练/评价入口 | 最差对手、矩阵与换边结果 | 交叉评估开销、克制循环 |
| 原生 rollout 太贵 | [Dreamer](https://arxiv.org/abs/1912.01603)、[MuZero](https://arxiv.org/abs/1911.08265) | 连续轨迹、校准基准 | `policies.py` 后续 world-model 扩展 | 多步误差、同预算棋力 | 模型偏差积累与策略利用 |
| 真实画面到事件 | [RT-DETR](https://arxiv.org/abs/2304.08069)、[DBNet](https://arxiv.org/abs/1911.08947)、[ByteTrack](https://arxiv.org/abs/2110.06864) | 游戏专用真实测试帧/轨迹 | `royale_ai/perception.py`；`controller.py` | mAP/OCR/事件误差、p95 | 通用类模型不含游戏卡；遮挡/阵营 |
| 近似引擎迁移偏差 | [dynamics randomization](https://arxiv.org/abs/1710.06537)、[domain randomization](https://arxiv.org/abs/1703.06907) | 真实对照录像与参数区间 | `backend.py`、卡牌数据、coverage | 轨迹误差、排序鲁棒性 | 随机化不能补缺失机制 |

路径相对于项目 `D:\研究生\Clash Royal`。更细的类/函数和配置由主策略指南提供；此表的文件存在性可由 `rg --files royale_lab royale_ai` 检查。未来方法需要新增实现和独立测试，不能从借鉴表推断已复现。

## 第一组有意义的研究实验

固定引擎 commit、卡牌数据 hash、卡组与相同种子，比较规则、相同预算搜索和其蒸馏策略；对手使用随机/进攻/防守三类。每一局保存可见观测、计划动作、接受反馈、后续状态/胜负与延迟。先比较 `with_belief` 和去掉信念的搜索，冻结候选数与预算：这样差异可以更合理地归因到对手信息处理。

主指标为胜/平/负和配对胜率差，次指标为塔伤、非法/拒绝动作、WAIT 比例、决策 p50/p95。按完整对局划分训练/验证；相同种子换边是配对而非独立样本，应使用以 seed/deck 为单位的 bootstrap。每类主要策略至少 100 局，低于目标明确标记不足；不能挑最好一局回放证明平均效果。
