# 原始论文逐项中文分析

访问日期：2026-10-13。下载与 PDF 解析是本机验证；论文实验结果均为作者报告。以下条目没有声称在本机复现论文棋力。

先读 [学习导航](INDEX.md)、[核心精读](DEEP_READS.md) 和 [方法比较](METHOD_COMPARISON.md)。

## action_masking：A Closer Look at Invalid Action Masking in Policy Gradient Algorithms

作者：Huang, Shengyi; Ontañón, Santiago。年份：2020。分类：动作建模。

原始来源：[A Closer Look at Invalid Action Masking in Policy Gradient Algorithms](https://arxiv.org/abs/2006.14171)。版本：`2006.14171v3`。DOI：`10.48550/arXiv.2006.14171`。

- 问题、输入与输出：策略完整空间包含大量非法动作；输入 logits 与合法 mask，输出有效分布。
- 方法：理论讨论 masked policy gradient，实验对比负奖励和不同 mask 模式。
- 数据、实验与作者结论：作者在 MicroRTS 等设置检验非法动作比例扩大时的效果。
- 局限：mask 必须由玩家已知规则给出，不能偷偷利用对手隐藏状态；训练/执行要一致。
- 本项目应用：WAIT 永远保底、圣水/手牌/区域/技能合法性，zero-invalid 与 stale-mask 拒绝测试。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[action_masking.pdf](<D:/研究生/Clash Royal/references/library/action_masking/action_masking.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/action_masking/extracted.txt>)。页数 10；SHA256 `a12e4130dc9705a5222462e6a40d6e3a4535c62f74e00b3484ed1780eaeeefe2`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## alphazero：Mastering Chess and Shogi by Self-Play with a General Reinforcement Learning Algorithm

作者：Silver, David; Hubert, Thomas; Schrittwieser, Julian; Antonoglou, Ioannis; Lai, Matthew; Guez, Arthur; Lanctot, Marc; Sifre, Laurent; Kumaran, Dharshan; Graepel, Thore; Lillicrap, Timothy; Simonyan, Karen; Hassabis, Demis。年份：2017。分类：搜索/自博弈。

原始来源：[Mastering Chess and Shogi by Self-Play with a General Reinforcement Learning Algorithm](https://arxiv.org/abs/1712.01815)。版本：`1712.01815v1`。DOI：`10.48550/arXiv.1712.01815`。

- 问题、输入与输出：给定游戏规则，通过自博弈学习策略和价值并输出搜索动作。
- 方法：策略/价值网络引导 MCTS，搜索访问分布做策略目标，终局结果做价值目标。
- 数据、实验与作者结论：作者在棋类环境中评估对强程序的棋力，训练计算量远大于本机短程训练。
- 局限：棋类是轮流、完全可观察且规则精确；不直接解决同时动作与对手隐藏圣水。
- 本项目应用：搜索轨迹蒸馏、价值目标、自博弈对手池；需重新设计实时信念与预算。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[alphazero.pdf](<D:/研究生/Clash Royal/references/library/alphazero/alphazero.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/alphazero/extracted.txt>)。页数 19；SHA256 `26c43408551c22f4fdc98e47f9b4592ab2161fac1ba5df27c897f46d8b6cec7f`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## bcq：Off-Policy Deep Reinforcement Learning without Exploration

作者：Fujimoto, Scott; Meger, David; Precup, Doina。年份：2018。分类：离线RL。

原始来源：[Off-Policy Deep Reinforcement Learning without Exploration](https://arxiv.org/abs/1812.02900)。版本：`1812.02900v3`。DOI：`10.48550/arXiv.1812.02900`。

- 问题、输入与输出：离线 Q 学习对未见动作外推；输入固定 transition 数据，输出受数据支持约束的策略。
- 方法：BCQ 生成接近数据的动作、有限扰动与双 Q 估计。
- 数据、实验与作者结论：作者在连续控制固定 batch 设置报告与 off-policy 方法的差异。
- 局限：连续动作版本需适配分解离散动作；数据未覆盖关键反制时不能凭空学会。
- 本项目应用：离线轨迹质量/动作支持诊断，防止价值网络偏爱未见落点。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[bcq.pdf](<D:/研究生/Clash Royal/references/library/bcq/bcq.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/bcq/extracted.txt>)。页数 23；SHA256 `3c0a3d01b7922b7d73d7bee0d8f0cf21b46df6ea99110378d89f7c1bd45b7011`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## bytetrack：ByteTrack: Multi-Object Tracking by Associating Every Detection Box

作者：Zhang, Yifu; Sun, Peize; Jiang, Yi; Yu, Dongdong; Weng, Fucheng; Yuan, Zehuan; Luo, Ping; Liu, Wenyu; Wang, Xinggang。年份：2021。分类：视觉追踪。

原始来源：[ByteTrack: Multi-Object Tracking by Associating Every Detection Box](https://arxiv.org/abs/2110.06864)。版本：`2110.06864v3`。DOI：`10.48550/arXiv.2110.06864`。

- 问题、输入与输出：避免低置信检测被直接丢弃导致轨迹断裂；输入逐帧框，输出 track_id。
- 方法：高分/低分框两阶段关联，复用运动模型及匹配。
- 数据、实验与作者结论：作者在 MOTChallenge 等多目标追踪基准测试。
- 局限：游戏召唤/死亡/阵营切换、密集小单位与视频人群不同。
- 本项目应用：单位跨帧轨迹、短遮挡恢复、派生出牌事件；不能把检测次数当召唤次数。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[bytetrack.pdf](<D:/研究生/Clash Royal/references/library/bytetrack/bytetrack.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/bytetrack/extracted.txt>)。页数 14；SHA256 `772e647095359c8f0db537b04dd41c48e77c17edf83a6d17f7e22dadac0261ad`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## cql：Conservative Q-Learning for Offline Reinforcement Learning

作者：Kumar, Aviral; Zhou, Aurick; Tucker, George; Levine, Sergey。年份：2020。分类：离线RL。

原始来源：[Conservative Q-Learning for Offline Reinforcement Learning](https://arxiv.org/abs/2006.04779)。版本：`2006.04779v3`。DOI：`10.48550/arXiv.2006.04779`。

- 问题、输入与输出：离线策略利用 Q 过估计；输入固定数据，输出保守价值与策略。
- 方法：正则化降低 OOD 动作的 Q，同时拟合数据 Bellman 目标。
- 数据、实验与作者结论：作者在 D4RL 等离线基准测试不同数据质量。
- 局限：保守强度存在折中；理论结论有假设，不能推导当前卡组安全最优。
- 本项目应用：候选价值训练；对 mask 后的动作集合做一致正则和 held-out 行为评价。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[cql.pdf](<D:/研究生/Clash Royal/references/library/cql/cql.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/cql/extracted.txt>)。页数 31；SHA256 `2bae51a0b45635ab714df39446cd656a9f4f1c503f512d6d0ce1eec3198d1716`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## dagger：A Reduction of Imitation Learning and Structured Prediction to No-Regret Online Learning

作者：Ross, Stephane; Gordon, Geoffrey J.; Bagnell, J. Andrew。年份：2010。分类：模仿学习。

原始来源：[A Reduction of Imitation Learning and Structured Prediction to No-Regret Online Learning](https://arxiv.org/abs/1011.0686)。版本：`1011.0686v3`。DOI：`10.48550/arXiv.1011.0686`。

- 问题、输入与输出：BC 在自己导致的状态上出错；输入学习者访问状态及专家标签，输出聚合训练集/策略。
- 方法：DAgger 迭代 rollout 学习者、查询专家、聚合数据并再训练。
- 数据、实验与作者结论：作者在模仿任务及序列标注中比较普通监督学习。
- 局限：需要可查询专家；搜索专家也是近似的，标签成本不能忽略。
- 本项目应用：搜索为在线 oracle 纠正 BC 失败局势，按整局划分并预算专家查询。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[dagger.pdf](<D:/研究生/Clash Royal/references/library/dagger/dagger.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/dagger/extracted.txt>)。页数 9；SHA256 `12b032cfd451b8ace4b18145dbe24382d2cbe2eda6b562331bc86799bbaafd5e`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## dbnet：Real-time Scene Text Detection with Differentiable Binarization

作者：Liao, Minghui; Wan, Zhaoyi; Yao, Cong; Chen, Kai; Bai, Xiang。年份：2019。分类：视觉OCR。

原始来源：[Real-time Scene Text Detection with Differentiable Binarization](https://arxiv.org/abs/1911.08947)。版本：`1911.08947v2`。DOI：`10.48550/arXiv.1911.08947`。

- 问题、输入与输出：场景文字检测；输入图像，输出文字区域。
- 方法：可微二值化使分割后处理进入训练，提高复杂文字边界检测。
- 数据、实验与作者结论：作者在场景文字检测数据集比较精度/速度。
- 局限：检测器本身不识别圣水数字；必须接文字识别器与数值合法性约束。
- 本项目应用：塔血/圣水 ROI 检测与 OCR，保留置信度、时间戳和读取失败。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[dbnet.pdf](<D:/研究生/Clash Royal/references/library/dbnet/dbnet.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/dbnet/extracted.txt>)。页数 8；SHA256 `81aafca19957a33fc7115ba6b76c038ab65d44ccbf0d6cb671b4ce3f0a200ab0`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## decision_transformer：Decision Transformer: Reinforcement Learning via Sequence Modeling

作者：Chen, Lili; Lu, Kevin; Rajeswaran, Aravind; Lee, Kimin; Grover, Aditya; Laskin, Michael; Abbeel, Pieter; Srinivas, Aravind; Mordatch, Igor。年份：2021。分类：序列决策。

原始来源：[Decision Transformer: Reinforcement Learning via Sequence Modeling](https://arxiv.org/abs/2106.01345)。版本：`2106.01345v2`。DOI：`10.48550/arXiv.2106.01345`。

- 问题、输入与输出：将策略学习作为序列建模；输入 return-to-go、状态、动作历史，输出下一动作。
- 方法：因果 Transformer 通过监督训练在目标回报条件下预测动作。
- 数据、实验与作者结论：作者在 Atari、Gym/D4RL 和长程依赖任务比较离线 RL。
- 局限：高目标回报不保证可实现；时间对齐、数据质量和分布外历史影响效果。
- 本项目应用：历史/卡牌/落点/等待分头预测，按局切分，单独报告 WAIT 与出牌准确率。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[decision_transformer.pdf](<D:/研究生/Clash Royal/references/library/decision_transformer/decision_transformer.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/decision_transformer/extracted.txt>)。页数 21；SHA256 `6ff675a7b8286ca7bf8e01e4b90a41bf5c90f4c3c007e17fdb4d24e26fd05e28`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## deepsort：Simple Online and Realtime Tracking with a Deep Association Metric

作者：Wojke, Nicolai; Bewley, Alex; Paulus, Dietrich。年份：2017。分类：视觉追踪。

原始来源：[Simple Online and Realtime Tracking with a Deep Association Metric](https://arxiv.org/abs/1703.07402)。版本：`1703.07402v1`。DOI：`10.48550/arXiv.1703.07402`。

- 问题、输入与输出：遮挡造成身份跳变；输入检测与图像外观，输出持续目标身份。
- 方法：SORT 运动关联增加学习的 appearance distance。
- 数据、实验与作者结论：作者在行人追踪评估身份关联性能。
- 局限：行人外观 embedding 不适用游戏精灵；同卡单位外观极相似。
- 本项目应用：比较几何 ByteTrack 与游戏域外观特征，测 ID switches 和事件计数误差。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[deepsort.pdf](<D:/研究生/Clash Royal/references/library/deepsort/deepsort.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/deepsort/extracted.txt>)。页数 5；SHA256 `67ba5d7d68392a5b9a198ff42fdcb2655f9ea9a662b68aba6dc52d6de06802c4`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## domain_randomization：Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World

作者：Tobin, Josh; Fong, Rachel; Ray, Alex; Schneider, Jonas; Zaremba, Wojciech; Abbeel, Pieter。年份：2017。分类：仿真迁移。

原始来源：[Domain Randomization for Transferring Deep Neural Networks from Simulation to the Real World](https://arxiv.org/abs/1703.06907)。版本：`1703.06907v1`。DOI：`10.48550/arXiv.1703.06907`。

- 问题、输入与输出：合成视觉分布与真实画面不一致；输入随机化渲染训练数据，输出视觉模型。
- 方法：随机纹理、光照、视角等，将真实环境视为随机分布一种。
- 数据、实验与作者结论：作者在机器人视觉任务展示模拟到真实迁移。
- 局限：随机化必须覆盖真实变化；不能凭成功合成样例宣称真实截图准确。
- 本项目应用：生成检测数据时随机遮挡/背景/缩放/特效，并留出独立真实帧评价。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[domain_randomization.pdf](<D:/研究生/Clash Royal/references/library/domain_randomization/domain_randomization.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/domain_randomization/extracted.txt>)。页数 8；SHA256 `3fc98c5f4cea050686d45858e647e1b704492121fdae80f8fcd5d560c107f11f`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## dreamer：Dream to Control: Learning Behaviors by Latent Imagination

作者：Hafner, Danijar; Lillicrap, Timothy; Ba, Jimmy; Norouzi, Mohammad。年份：2019。分类：世界模型。

原始来源：[Dream to Control: Learning Behaviors by Latent Imagination](https://arxiv.org/abs/1912.01603)。版本：`1912.01603v3`。DOI：`10.48550/arXiv.1912.01603`。

- 问题、输入与输出：从像素学习控制，减少真实交互；输入序列，输出潜在想象中的 actor/value。
- 方法：RSSM 学随机/确定潜在动态，以想象轨迹的价值梯度训练策略。
- 数据、实验与作者结论：作者在 DeepMind Control 图像控制任务与相关基线比较样本效率。
- 局限：控制任务不是双人实时卡牌游戏；接触/死亡事件和敌人策略变化可能难以拟合。
- 本项目应用：世界模型接口、观测历史编码、多步 rollout 误差曲线。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[dreamer.pdf](<D:/研究生/Clash Royal/references/library/dreamer/dreamer.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/dreamer/extracted.txt>)。页数 20；SHA256 `318891d59ba0065f57eb0e735735700e5eb528f53bf08b81c773437385586c64`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## dreamerv3：Mastering Diverse Domains through World Models

作者：Hafner, Danijar; Pasukonis, Jurgis; Ba, Jimmy; Lillicrap, Timothy。年份：2023。分类：世界模型。

原始来源：[Mastering Diverse Domains through World Models](https://arxiv.org/abs/2301.04104)。版本：`2301.04104v2`。DOI：`10.48550/arXiv.2301.04104`。

- 问题、输入与输出：跨任务奖励尺度与学习稳定性；输出世界模型、行为策略和价值。
- 方法：DreamerV3 采用稳定化表示、分布价值/奖励和尺度处理等统一设置。
- 数据、实验与作者结论：作者跨多个域测试，并包含无演示的 Minecraft 钻石获取。
- 局限：统一超参不等于在本项目直接可用；多智能体非平稳性和动作 mask 仍需适配。
- 本项目应用：后续高吞吐视觉训练候选；先固定引擎版本和轨迹 schema。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[dreamerv3.pdf](<D:/研究生/Clash Royal/references/library/dreamerv3/dreamerv3.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/dreamerv3/extracted.txt>)。页数 40；SHA256 `d0385798e8bada8e81b915c1743be81d9ce8776f12ed937e09351995e099ce37`。
许可：publisher terms; see saved source metadata。公开全文与代码许可是两回事。

## drqn：Deep Recurrent Q-Learning for Partially Observable MDPs

作者：Hausknecht, Matthew; Stone, Peter。年份：2015。分类：部分可观察/记忆。

原始来源：[Deep Recurrent Q-Learning for Partially Observable MDPs](https://arxiv.org/abs/1507.06527)。版本：`1507.06527v4`。DOI：`10.48550/arXiv.1507.06527`。

- 问题、输入与输出：单帧不能区分隐藏状态；输入图像序列，输出递归 Q 值。
- 方法：DQN 卷积编码后使用 LSTM，将历史整合进价值预测。
- 数据、实验与作者结论：作者使用 Atari 的可观察性变化/闪烁观测评估记忆。
- 局限：隐状态重建依赖训练分布；记忆网络不能消除理论信息缺失。
- 本项目应用：玩家可见历史编码、对手出牌/圣水轨迹；与显式信念比较。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[drqn.pdf](<D:/研究生/Clash Royal/references/library/drqn/drqn.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/drqn/extracted.txt>)。页数 7；SHA256 `f316b46c472f8a8a010820c58ce809cc519247fd22857c9a203578622bf4b968`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## dynamics_randomization：Sim-to-Real Transfer of Robotic Control with Dynamics Randomization

作者：Peng, Xue Bin; Andrychowicz, Marcin; Zaremba, Wojciech; Abbeel, Pieter。年份：2017。分类：仿真校准。

原始来源：[Sim-to-Real Transfer of Robotic Control with Dynamics Randomization](https://arxiv.org/abs/1710.06537)。版本：`1710.06537v3`。DOI：`10.48550/arXiv.1710.06537`。

- 问题、输入与输出：模拟动力学误差造成真实控制失败；输入参数随机化环境，输出稳健策略。
- 方法：训练时随机物理参数，并使用历史/适应机制处理环境变化。
- 数据、实验与作者结论：作者在机器人控制的模拟到真实转移实验测试。
- 局限：参数范围需真实证据；随机化不能补完全缺失的机制。
- 本项目应用：攻击延迟/移速/部署延迟误差区间，版本敏感性和策略鲁棒性报告。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[dynamics_randomization.pdf](<D:/研究生/Clash Royal/references/library/dynamics_randomization/dynamics_randomization.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/dynamics_randomization/extracted.txt>)。页数 8；SHA256 `a15cb24630a11218865027024ae2353f80f46d40371b42dccb2b24e9d00d62d8`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## gae：High-Dimensional Continuous Control Using Generalized Advantage Estimation

作者：Schulman, John; Moritz, Philipp; Levine, Sergey; Jordan, Michael; Abbeel, Pieter。年份：2015。分类：在线RL。

原始来源：[High-Dimensional Continuous Control Using Generalized Advantage Estimation](https://arxiv.org/abs/1506.02438)。版本：`1506.02438v6`。DOI：`10.48550/arXiv.1506.02438`。

- 问题、输入与输出：降低策略梯度方差；输入 TD residual 序列，输出折扣优势估计。
- 方法：GAE 用 gamma/lambda 加权多步误差，在 bias/variance 间折中。
- 数据、实验与作者结论：作者在高维连续控制中结合 trust-region 方法评估。
- 局限：依赖价值估计，截断处理错误会造成偏差；不是独立策略算法。
- 本项目应用：对终局/时间截断区分 bootstrap；诊断长局稀疏奖励和 WAIT 的信用分配。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[gae.pdf](<D:/研究生/Clash Royal/references/library/gae/gae.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/gae/extracted.txt>)。页数 14；SHA256 `5aa736ccf3517a1f36d0ca8146982e69beaf9a2bdccac2ae324f780743bc045e`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## gail：Generative Adversarial Imitation Learning

作者：Ho, Jonathan; Ermon, Stefano。年份：2016。分类：模仿学习。

原始来源：[Generative Adversarial Imitation Learning](https://arxiv.org/abs/1606.03476)。版本：`1606.03476v1`。DOI：`10.48550/arXiv.1606.03476`。

- 问题、输入与输出：只给专家轨迹，没有奖励；输入专家/策略轨迹，输出判别器奖励与策略。
- 方法：匹配 occupancy measure 的对抗训练，替代先求奖励再 RL 的两阶段过程。
- 数据、实验与作者结论：作者在高维控制模仿中测试并比较 model-free 方法。
- 局限：需环境交互且对抗训练可能不稳定；相似行为不等于胜率高。
- 本项目应用：奖励难以手写时的研究对照；保留胜负与塔伤独立评价。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[gail.pdf](<D:/研究生/Clash Royal/references/library/gail/gail.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/gail/extracted.txt>)。页数 14；SHA256 `38d271dd45aa6c72de514b9afc465a866c9d71d04e45c3a92e12474da9ef49aa`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## iql：Offline Reinforcement Learning with Implicit Q-Learning

作者：Kostrikov, Ilya; Nair, Ashvin; Levine, Sergey。年份：2021。分类：离线RL。

原始来源：[Offline Reinforcement Learning with Implicit Q-Learning](https://arxiv.org/abs/2110.06169)。版本：`2110.06169v1`。DOI：`10.48550/arXiv.2110.06169`。

- 问题、输入与输出：避免对数据外动作显式求 max；输入数据动作与回报，输出 expectile value 和加权 BC。
- 方法：expectile regression 隐式估计较好数据动作的价值，再 advantage-weighted 提取策略。
- 数据、实验与作者结论：作者在 D4RL 离线任务评估并讨论在线微调。
- 局限：改善受数据覆盖约束，expectile/权重温度敏感。
- 本项目应用：从搜索与人类回放学习价值，保持合法动作与明确的离线数据来源。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[iql.pdf](<D:/研究生/Clash Royal/references/library/iql/iql.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/iql/extracted.txt>)。页数 13；SHA256 `b135fecfc6685b5ac88c5d41d2f78db8da03ce3e47c8e1152cb1f77a1ffa59ee`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## katacr：Playing Non-Embedded Card-Based Games with Reinforcement Learning

作者：Wu, Tianyang; Wan, Lipeng; Wang, Yuhang; Wan, Qiang; Lan, Xuguang。年份：2025。分类：直接研究。

原始来源：[Playing Non-Embedded Card-Based Games with Reinforcement Learning](https://arxiv.org/abs/2504.04783)。版本：`2504.04783v1`。DOI：`10.48550/arXiv.2504.04783`。

- 问题、输入与输出：非嵌入式皇室战争代理从画面而非隐藏状态出发；输出移动设备实际操作。
- 方法：生成目标检测数据，检测/OCR 特征融合，专家回放离线序列训练，加入动作 delay。
- 数据、实验与作者结论：使用固定卡组、专家回放和内置 AI 对手；正文报告少量成功对局，尚非稳定高胜率。
- 局限：视觉融合时间、等待不平衡、卡组和敌人分布限制；不能以合成图片代替实机验证。
- 本项目应用：像素适配、事件时间戳、WAIT 监督、真实录像训练集与感知误差评价。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[katacr.pdf](<D:/研究生/Clash Royal/references/library/katacr/katacr.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/katacr/extracted.txt>)。页数 15；SHA256 `0144316a5393074d7f0cd546dac0449a525085a3c6de1067ae1e015778e1f20b`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## lola：Learning with Opponent-Learning Awareness

作者：Foerster, Jakob N.; Chen, Richard Y.; Al-Shedivat, Maruan; Whiteson, Shimon; Abbeel, Pieter; Mordatch, Igor。年份：2017。分类：对手建模。

原始来源：[Learning with Opponent-Learning Awareness](https://arxiv.org/abs/1709.04326)。版本：`1709.04326v4`。DOI：`10.48550/arXiv.1709.04326`。

- 问题、输入与输出：学习型对手也在更新；输入双方参数和学习目标，输出考虑对手变化的更新。
- 方法：LOLA 对对手学习步骤求导，改变自身更新方向。
- 数据、实验与作者结论：作者在 iterated dilemma 与多智能体任务研究合作行为。
- 局限：需访问/估计对手学习过程，真实皇室战争未知真人不满足该条件。
- 本项目应用：仅用于可控自博弈实验，不作为普通对手圣水估计器。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[lola.pdf](<D:/研究生/Clash Royal/references/library/lola/lola.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/lola/extracted.txt>)。页数 14；SHA256 `aa91a4634e8d84bc3b83f6a165eafd53a873f7802a2093c5a60f7d781978f2e2`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## muzero：Mastering Atari, Go, Chess and Shogi by Planning with a Learned Model

作者：Schrittwieser, Julian; Antonoglou, Ioannis; Hubert, Thomas; Simonyan, Karen; Sifre, Laurent; Schmitt, Simon; Guez, Arthur; Lockhart, Edward; Hassabis, Demis; Graepel, Thore; Lillicrap, Timothy; Silver, David。年份：2019。分类：搜索/世界模型。

原始来源：[Mastering Atari, Go, Chess and Shogi by Planning with a Learned Model](https://arxiv.org/abs/1911.08265)。版本：`1911.08265v2`。DOI：`10.48550/arXiv.1911.08265`。

- 问题、输入与输出：未知动力学下的规划；输入观测历史，输出策略、价值、奖励预测与选定动作。
- 方法：representation/dynamics/prediction 三个网络，在潜在状态中进行 MCTS。
- 数据、实验与作者结论：作者测试 Atari 57 游戏与棋类，报告与强基线比较的成绩。
- 局限：潜在模型仅为任务预测服务，不保证所有物理交互正确；数据和搜索成本高。
- 本项目应用：替换昂贵 rollout 的潜在模型；仍用规则动作 mask，校验多步塔伤和单位存活误差。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[muzero.pdf](<D:/研究生/Clash Royal/references/library/muzero/muzero.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/muzero/extracted.txt>)。页数 21；SHA256 `25588ee1d48690b33359a08a42fadfe86335aefefeee241d465b8bbd5eaa445c`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## nfsp：Deep Reinforcement Learning from Self-Play in Imperfect-Information Games

作者：Heinrich, Johannes; Silver, David。年份：2016。分类：自博弈/策略池。

原始来源：[Deep Reinforcement Learning from Self-Play in Imperfect-Information Games](https://arxiv.org/abs/1603.01121)。版本：`1603.01121v2`。DOI：`10.48550/arXiv.1603.01121`。

- 问题、输入与输出：不完全信息博弈中的稳健策略；输入自身信息状态，输出最佳响应及平均策略。
- 方法：NFSP 结合 RL 最佳响应和监督学习历史平均行为，自博弈近似 fictitious play。
- 数据、实验与作者结论：作者在扑克任务测试接近均衡和对强策略表现。
- 局限：理论假设与有限网络/有限训练存在差距；扑克不是实时空间部署游戏。
- 本项目应用：保持历史策略池，避免仅打单一规则对手；记录跨对手胜率矩阵。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[nfsp.pdf](<D:/研究生/Clash Royal/references/library/nfsp/nfsp.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/nfsp/extracted.txt>)。页数 10；SHA256 `fe1642cf4fc43a2735330df83ad680d2003f4bc678a5332705f80a4821473e63`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## openai_five：Dota 2 with Large Scale Deep Reinforcement Learning

作者：OpenAI; :; Berner, Christopher; Brockman, Greg; Chan, Brooke; Cheung, Vicki; Dębiak, Przemysław; Dennison, Christy; Farhi, David; Fischer, Quirin; Hashme, Shariq; Hesse, Chris; Józefowicz, Rafal; Gray, Scott; Olsson, Catherine; Pachocki, Jakub; Petrov, Michael; Pinto, Henrique P. d. O.; Raiman, Jonathan; Salimans, Tim; Schlatter, Jeremy; Schneider, Jonas; Sidor, Szymon; Sutskever, Ilya; Tang, Jie; Wolski, Filip; Zhang, Susan。年份：2019。分类：实时博弈/规模。

原始来源：[Dota 2 with Large Scale Deep Reinforcement Learning](https://arxiv.org/abs/1912.06680)。版本：`1912.06680v1`。DOI：`10.48550/arXiv.1912.06680`。

- 问题、输入与输出：长时实时对抗的分解动作、记忆和协作；输入结构化游戏观测，输出动作。
- 方法：大规模循环 PPO、自博弈与工程化分布式训练。
- 数据、实验与作者结论：作者在有限英雄/规则设置下展示与职业队伍比赛结果。
- 局限：计算规模、环境接口与任务限制不能迁移为本项目已获人类级表现。
- 本项目应用：动作头分解、执行延迟、长局 reward 和对手采样；优先借鉴工程指标。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[openai_five.pdf](<D:/研究生/Clash Royal/references/library/openai_five/openai_five.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/openai_five/extracted.txt>)。页数 66；SHA256 `9af6c436cf61b4e48fadfebe067d5e1779fff4d687ccbe59ca9e5abb1e1a6b70`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## opponent_modeling：Opponent Modeling in Deep Reinforcement Learning

作者：He, He; Boyd-Graber, Jordan; Kwok, Kevin; Daumé III, Hal。年份：2016。分类：对手建模。

原始来源：[Opponent Modeling in Deep Reinforcement Learning](https://arxiv.org/abs/1609.05559)。版本：`1609.05559v1`。DOI：`10.48550/arXiv.1609.05559`。

- 问题、输入与输出：适应不同对手行为；输入观测，输出 opponent 表示和自身 Q。
- 方法：DRON 将对手特征学习与 Q 网络组合，混合专家刻画对手变化。
- 数据、实验与作者结论：作者在竞争任务中比较不建模对手的深度 RL。
- 局限：对手身份与动作观察可得性不同；新卡组/视觉漏检可能使表示失准。
- 本项目应用：显式圣水/轮转信念加学习 opponent embedding，做去掉信念的消融。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[opponent_modeling.pdf](<D:/研究生/Clash Royal/references/library/opponent_modeling/opponent_modeling.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/opponent_modeling/extracted.txt>)。页数 10；SHA256 `222dbcd4ea480bd33ea6f3011e19139bb5d1e4f33ac4c82e3f411cca57502dea`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## parameterized_actions：Deep Reinforcement Learning in Parameterized Action Space

作者：Hausknecht, Matthew; Stone, Peter。年份：2015。分类：动作建模。

原始来源：[Deep Reinforcement Learning in Parameterized Action Space](https://arxiv.org/abs/1511.04143)。版本：`1511.04143v5`。DOI：`10.48550/arXiv.1511.04143`。

- 问题、输入与输出：离散动作用连续参数；输入足球状态，输出动作及力度/方向等。
- 方法：将深度 actor-critic 扩展至参数化动作，并处理梯度边界。
- 数据、实验与作者结论：作者在 RoboCup 模拟足球中比较进球能力。
- 局限：小动作集合的足球实验不能证明卡牌区域/延迟建模优越。
- 本项目应用：部署坐标连续化之前先验证边界投影、参数无关维度和动作可执行性。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[parameterized_actions.pdf](<D:/研究生/Clash Royal/references/library/parameterized_actions/parameterized_actions.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/parameterized_actions/extracted.txt>)。页数 12；SHA256 `1be9116b3d155766a8f8032588355f7da7981669752f4fdce4f13e9913da090d`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## pdqn：Parametrized Deep Q-Networks Learning: Reinforcement Learning with Discrete-Continuous Hybrid Action Space

作者：Xiong, Jiechao; Wang, Qing; Yang, Zhuoran; Sun, Peng; Han, Lei; Zheng, Yang; Fu, Haobo; Zhang, Tong; Liu, Ji; Liu, Han。年份：2018。分类：动作建模。

原始来源：[Parametrized Deep Q-Networks Learning: Reinforcement Learning with Discrete-Continuous Hybrid Action Space](https://arxiv.org/abs/1810.06394)。版本：`1810.06394v1`。DOI：`10.48550/arXiv.1810.06394`。

- 问题、输入与输出：动作是离散类型和连续参数；输入状态，输出动作类型及其连续参数。
- 方法：P-DQN 把离散 Q 估计与连续参数网络组合。
- 数据、实验与作者结论：作者在模拟问题、RoboCup、王者荣耀单人模式测试。
- 局限：空间合法区域不规则、不同卡参数语义不同；需要显式约束与等待动作。
- 本项目应用：card_id/xy/delay 的参数化头，与固定格点候选策略作比较。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[pdqn.pdf](<D:/研究生/Clash Royal/references/library/pdqn/pdqn.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/pdqn/extracted.txt>)。页数 18；SHA256 `17b09e5f96b9a04d0f951e8a8caa7109454856e37d18213802c54ab72d2cb199`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## pomcp：Monte-Carlo Planning in Large POMDPs

作者：David Silver; Joel Veness。年份：2010。分类：部分可观察/搜索。

原始来源：[Monte-Carlo Planning in Large POMDPs](https://papers.nips.cc/paper_files/paper/2010/file/edfbe1afcf9246bb0d40eb4d8027d90f-Paper.pdf)。版本：`publisher/openreview source retrieved on 2026-10-13`。DOI：`未分配/未找到`。

- 问题、输入与输出：隐藏状态造成巨大信念空间；输入历史/粒子信念和生成式模拟器，输出当前动作。
- 方法：POMCP 在历史树使用 UCT，并以模拟采样更新粒子信念。
- 数据、实验与作者结论：Rocksample、Battleship 和部分可观察 PacMan，大状态空间中测试在线规划。
- 局限：依赖可采样且可信的转移/观测模型；有限粒子退化与预算限制依然存在。
- 本项目应用：对手圣水、手牌/轮转粒子假设，sampled-state rollout；禁止用真实隐藏状态初始化信念。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[pomcp.pdf](<D:/研究生/Clash Royal/references/library/pomcp/pomcp.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/pomcp/extracted.txt>)。页数 9；SHA256 `fa4c2e0d4a42c82b22bf4163a0096a95ea0cffa000ce81dc33e0aa97146fc92f`。
许可：publisher terms; see saved source metadata。公开全文与代码许可是两回事。

## ppo：Proximal Policy Optimization Algorithms

作者：Schulman, John; Wolski, Filip; Dhariwal, Prafulla; Radford, Alec; Klimov, Oleg。年份：2017。分类：在线RL。

原始来源：[Proximal Policy Optimization Algorithms](https://arxiv.org/abs/1707.06347)。版本：`1707.06347v2`。DOI：`10.48550/arXiv.1707.06347`。

- 问题、输入与输出：避免策略梯度更新过大；输入 on-policy 轨迹，输出更新后的随机策略。
- 方法：剪切 probability ratio 的 surrogate 目标，反复 minibatch 优化。
- 数据、实验与作者结论：作者在控制与 Atari 任务比较学习稳定性及样本效率。
- 局限：无全局最优保证；稀疏胜负、长局和差的 reward shaping 可令训练失效。
- 本项目应用：Masked PPO 基线、版本冻结的训练/评估、整局 seed 留出和 checkpoint。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[ppo.pdf](<D:/研究生/Clash Royal/references/library/ppo/ppo.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/ppo/extracted.txt>)。页数 12；SHA256 `e78feadadbdbb0b601b3c2bcc81404722cd431a489b307545f9b7bea1e8c4f5b`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## psro：A Unified Game-Theoretic Approach to Multiagent Reinforcement Learning

作者：Lanctot, Marc; Zambaldi, Vinicius; Gruslys, Audrunas; Lazaridou, Angeliki; Tuyls, Karl; Perolat, Julien; Silver, David; Graepel, Thore。年份：2017。分类：自博弈/策略池。

原始来源：[A Unified Game-Theoretic Approach to Multiagent Reinforcement Learning](https://arxiv.org/abs/1711.00832)。版本：`1711.00832v2`。DOI：`10.48550/arXiv.1711.00832`。

- 问题、输入与输出：对手非平稳且循环克制；输入策略池/payoff matrix，输出元策略与新响应。
- 方法：PSRO 训练响应策略并扩充经验博弈，使用 meta-solver 混合对手。
- 数据、实验与作者结论：作者在多智能体博弈任务比较泛化与利用性。
- 局限：策略池交叉评估成本随规模增加；响应 oracle 训练不精确。
- 本项目应用：规则、搜索、BC、PPO checkpoint 策略池与对局配对评价。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[psro.pdf](<D:/研究生/Clash Royal/references/library/psro/psro.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/psro/extracted.txt>)。页数 27；SHA256 `2e2720935c297849aff281fef0b76ef36eba3d19e49359cd821c39b2e1c2deb3`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## r2d2：Recurrent Experience Replay in Distributed Reinforcement Learning

作者：Steven Kapturowski; Georg Ostrovski; John Quan; Rémi Munos; Will Dabney。年份：2019。分类：部分可观察/记忆。

原始来源：[Recurrent Experience Replay in Distributed Reinforcement Learning](https://openreview.net/pdf/387fb2fcee8f74c53cf707a9856f40c458f33933.pdf)。版本：`publisher/openreview source retrieved on 2026-10-13`。DOI：`未分配/未找到`。

- 问题、输入与输出：递归网络、回放和分布式参数滞后冲突；输入连续经验段，输出 recurrent Q 策略。
- 方法：R2D2 用序列回放、burn-in 和 recurrent state 管理降低表示漂移影响。
- 数据、实验与作者结论：作者在 Atari 和 DMLab 中报告分布式实验。
- 局限：多 actor 规模并非本机成本；episode 边界、hidden state 版本都要严谨处理。
- 本项目应用：未来循环策略的 replay schema、burn-in 和跨局重置测试。

本机状态：`failed` / `not_checked`。
下载失败记录：`<HTTPError 403: 'Forbidden'>`。保留来源并可重试，不计入已下载数。
许可：publisher terms; see saved source metadata。公开全文与代码许可是两回事。

## rtdetr：DETRs Beat YOLOs on Real-time Object Detection

作者：Zhao, Yian; Lv, Wenyu; Xu, Shangliang; Wei, Jinman; Wang, Guanzhong; Dang, Qingqing; Liu, Yi; Chen, Jie。年份：2023。分类：视觉检测。

原始来源：[DETRs Beat YOLOs on Real-time Object Detection](https://arxiv.org/abs/2304.08069)。版本：`2304.08069v3`。DOI：`10.48550/arXiv.2304.08069`。

- 问题、输入与输出：减少检测后处理瓶颈；输入图像，输出端到端类别和框。
- 方法：RT-DETR 使用高效混合编码器与 query 选择，decoder 深度可调。
- 数据、实验与作者结论：作者在 COCO 检测基准测试精度和 GPU 速度。
- 局限：作者 GPU 速度不等于本机端到端延迟；真实游戏单位类别仍需专用训练。
- 本项目应用：候选视觉后端，对截图/预处理/推理/OCR/融合分别计时。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[rtdetr.pdf](<D:/研究生/Clash Royal/references/library/rtdetr/rtdetr.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/rtdetr/extracted.txt>)。页数 14；SHA256 `01018b33f15723f057b1b65c4ae6958e8cc99b75acf1ca434467c9f5f0782f15`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## seat：Playing Card-Based RTS Games with Deep Reinforcement Learning

作者：Tianyu Liu; Zijie Zheng; Hongchang Li; Kaigui Bian; Lingyang Song。年份：2019。分类：直接研究。

原始来源：[Playing Card-Based RTS Games with Deep Reinforcement Learning](https://www.ijcai.org/proceedings/2019/0631.pdf)。版本：`publisher/openreview source retrieved on 2026-10-13`。DOI：`10.24963/ijcai.2019/631`。

- 问题、输入与输出：卡牌 RTS 的动作同时含卡牌和落点；输入单位状态图，输出选卡与部署网格。
- 方法：SEAT 将 selection 的深度 Q 学习与 attention 的空间选点拆开，按时间窗口累积奖励。
- 数据、实验与作者结论：Lua 模拟器、8 张抽象卡牌，比较规则/决策树对手；这是论文模拟实验。
- 局限：抽象单位和真实皇室战争有差异，不能证明全卡、真实视觉或当前平衡版本有效。
- 本项目应用：动作分解、候选落点生成、出牌间隔和模拟基线。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[seat.pdf](<D:/研究生/Clash Royal/references/library/seat/seat.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/seat/extracted.txt>)。页数 7；SHA256 `b52c301ca34800df869463c10f4d762ccfe7473aabb189bd8b98ae5466b36d3c`。
许可：publisher terms; see saved source metadata。公开全文与代码许可是两回事。

## trajectory_transformer：Offline Reinforcement Learning as One Big Sequence Modeling Problem

作者：Janner, Michael; Li, Qiyang; Levine, Sergey。年份：2021。分类：序列决策/搜索。

原始来源：[Offline Reinforcement Learning as One Big Sequence Modeling Problem](https://arxiv.org/abs/2106.02039)。版本：`2106.02039v4`。DOI：`10.48550/arXiv.2106.02039`。

- 问题、输入与输出：同时学习动力学和行为；输入离散化轨迹 tokens，输出后续状态/动作/奖励。
- 方法：Trajectory Transformer 对全轨迹建模，并用 beam search 搜索高回报序列。
- 数据、实验与作者结论：作者在离线控制任务比较规划与 model-free 方法。
- 局限：token 化和 beam 的代价高，预测误差会随滚动长度积累。
- 本项目应用：事件级而非每像素序列规划；与真实快照 rollout 对照并设时限。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[trajectory_transformer.pdf](<D:/研究生/Clash Royal/references/library/trajectory_transformer/trajectory_transformer.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/trajectory_transformer/extracted.txt>)。页数 17；SHA256 `f935734f7bcc85f9d1069b5c89a674b04848a9e4ade8f1b360c33577cf2d029d`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。

## yolov3：YOLOv3: An Incremental Improvement

作者：Redmon, Joseph; Farhadi, Ali。年份：2018。分类：视觉检测。

原始来源：[YOLOv3: An Incremental Improvement](https://arxiv.org/abs/1804.02767)。版本：`1804.02767v1`。DOI：`10.48550/arXiv.1804.02767`。

- 问题、输入与输出：实时图像检测；输入 RGB，输出类别/框/置信度。
- 方法：多尺度预测、Darknet-53 与独立类别预测。
- 数据、实验与作者结论：作者在 COCO 讨论速度与检测精度折中。
- 局限：COCO 类别不含游戏卡牌；遮挡、阵营、皮肤需要游戏域数据。
- 本项目应用：像素检测接口与吞吐基线，建立小目标/遮挡/阵营混淆测试集。

本机状态：`downloaded` / `pdf_parsed_text_extracted`。
PDF：[yolov3.pdf](<D:/研究生/Clash Royal/references/library/yolov3/yolov3.pdf>)；正文：[extracted.txt](<D:/研究生/Clash Royal/references/library/yolov3/extracted.txt>)。页数 6；SHA256 `37049049b5e06f67c6cd22b72f7b9352914b0eb3a03e99abf54594c1005a8468`。
许可：http://arxiv.org/licenses/nonexclusive-distrib/1.0/。公开全文与代码许可是两回事。
