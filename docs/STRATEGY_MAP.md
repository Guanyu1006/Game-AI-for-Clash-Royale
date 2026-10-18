# 策略优化定位表

路径以根目录为基准。一次改一个模块，先测试再配对评估；引擎平衡变化后重新训练，避免把版本收益归于策略。

| 模块 | 实际入口 | 现在的实现/替换位置 | 必须检查的指标 |
|---|---|---|---|
| 玩家观测 | royale_lab/backend.py observe | 自己HUD+公共单位/事件；禁止敌方私有字段 | 扰动隐藏状态后观测不变，无oracle引用 |
| 特征编码 | learning.py encode | 34基础+173卡牌one-hot，最多128候选；修改ENCODER_VERSION | schema/mask/有限值，分WAIT/PLAY准确率 |
| 圣水/轮转 | memory.py Belief.update/available | 确认事件扣费+恢复区间，4卡近似；可换粒子/循环网络 | 区间覆盖率、去重、未知历史保守性 |
| 卡组先验 | policies.py SearchPolicy.decide prior | 已见牌+Hog补齐；可换卡组池 | 未见卡组测试、训练测试隔离 |
| 落点/时机 | policies.py raw_candidates | 桥头/防御/敌群/塔、WAIT、技能 | 合法接受率、亚秒时机、WAIT/PLAY分统计 |
| 合法屏蔽 | backend.py legal/apply | 原生can_play与技能就绪；反馈检查 | Mirror费用、冷却、范围、重复动作 |
| 世界模型 | backend.py from_observation | 公开状态重建后原生前推；群体/状态/队列近似 | 单位数/HP/轨迹差分、预测误差 |
| 搜索 | policies.py SearchPolicy | 2假设、4/6秒、软预算 | horizon_ticks/budget_ms/max_candidates/responses；胜率+p95+超限 |
| 规则专家 | policies.py tactical_score | 威胁/费用/法术覆盖与塔伤 | 整局表现，不把局部score称全局最优 |
| 奖励/价值 | rewards.py utility/transition_reward | 塔、单位资源/位置、终局 | 奖励投机、终局胜率与伤害一起看 |
| 策略网络 | learning.py CandidateNet/LearnedPolicy | 候选MLP+WAIT行value；可换集合/注意力/序列模型 | checkpoint版本、mask、推理p95 |
| 模仿训练 | learning.py train_bc | 搜索标签，整局train/val | 样本/epochs/老师配置；PLAY准确率和整局强度 |
| RL训练 | learning.py CandidateGym/train_ppo | CPU masked PPO/GAE/clip | steps/课程/opponent；Gym检查、曲线、加载对局 |
| 执行器 | session.py advance/act | 同时观察、提交、反馈、日志 | decision_interval、真实扣费/轮转、终局与多局reset |
| 评估 | evaluation.py scenarios/evaluate/play_game | 冻结换边/4卡组/3对手，JSONL恢复 | 每策略100局、消融、崩溃/延迟/塔伤 |
| 真实视觉 | pixels.py PixelAdapter | 真实检测器协议，缺HUD失败 | mAP、OCR、ID切换、坐标标定、端到端拒绝率 |

建议优先修对策略排序影响最大的引擎误差，再修世界模型重建和候选，最后扩大模型/搜索。法术塔伤不折减时，模型容易利用模拟器漏洞。

新Policy实现decide(obs)->Decision并注册make_policy，不应持有Backend或Session引用。新Backend满足contracts.BattleBackend；适配crforge也必须过滤其上游ObservationBuilder泄露的敌方手牌/圣水。
