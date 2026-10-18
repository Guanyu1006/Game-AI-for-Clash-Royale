# 架构决定

- D001：新增royale_phase2包和独立phase2入口，原royale_lab保持基线。原生后端必须明确identity/capabilities；无依赖时fail closed，不自动回落。
- D002：数据解析、原生执行、可见观测、质量评估和训练轨迹分别建模。未知原始字段保留unknown；不能拿原生全状态当玩家信息。
- D003：优先原生逻辑+可选renderer；不先搭完整账号/商城。候选选择必须基于实际依赖和启动证据。
- D004：正式100局与开发样本分离；动作与来源不可变，补充初始化假设须单独记录。
- D005：运行环境使用项目内便携QEMU/WHPX与Ubuntu磁盘，已经实际启动并SSH执行。全局宿主WSL/驱动不变；Android/ARM64桥仍需实测。guest内安装依赖属于项目qcow2准备阶段。
- D006：APK整体SHA与上游旧声明不同，不能改写成旧SHA验证通过；实际ARM64 libg SHA精确匹配，159项base资源逐项精确匹配。固定更新83项尚缺，不能以base资源替代并声称content15.535.86。
- D007：FirstLight转换器对混合卡牌等级的众数归一化为有损假设。正式4局/开发3局严格配置拒绝；另113局仅已知等级可保持，实际runtime兼容性仍unknown。原始与假设文件均保留。
- D008：minimal实体包含索敌、攻击冷却及可能不可见单位，只能用于debug。未获可见性证据前禁止直接交给actor；观察来源、隐藏状态与合法mask须独立审计。
- D009：允许检索公开更新资源与静态bootstrap元数据；不执行未知远端脚本，不连接官方账号或远端战斗服务，不请求登录/遥测端点。只在本地验证资源哈希，准备阶段联网与正式断网验收分开。

- D010：首个开发重建43动作/43原生消费；发现minimal的hero effective ID可为0、evo可为13类ID，不能按root ID硬比较；用真实前后手牌/费用/唯一消费补证，未知形态身份仍unknown。原失败run保留，不回写。源duration.timeline_seconds并不是已提供的native终局tick，source-v1明确terminal_engine_tick=null；主比较修复为unknown，另报实际5.15秒差异，不拟合偏移、不移动动作。该修复仅依据开发样本和字段契约，正式集未运行。
- D011：完整15.535.86更新现已取得，652项实际安装hash通过，先前D006的缺件状态已解决。version_manifest固定830资产，整体APK差异与scdb单文件SHA1元数据例外不隐去。
- D012：最小严格BC先限制WAIT/普通Fireball/Arrows连续原生坐标；40个实际落点、反例、两owner隐私counterfactual、readiness都有证据。这不代表全卡牌placement oracle或强策略，离手后可能WAIT锁定。
- D013：正式100分母不变，额外65局学习池按源字段筛选，learning16和formal2重复cohort按预先group hash选取、不读终局。重复r2/r3仅作确定性证据，学习只用预声明r1，不复制凑数。
- D014：实际恢复测试先发现parent worker.log尚在增长的hash错误，旧失败保留。改由parent封存其日志后重做：真实消费后kill，partial不计数，fresh reset完整43消费且只计一次。正式13局后Windows reader/replace产生WinError5，write_json仅对本地原子发布作有界重试，绝不重试native动作；已校13份完成hash并续跑。
- D015：真实partial手牌3张不能导致推理崩溃；白名单特征可表达缺槽，但严格mask只允许WAIT。两种readiness和伪造play-mask反例已用真实capture离线检验。训练这些partial行仍默认隔离。
