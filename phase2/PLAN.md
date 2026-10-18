# 第二阶段执行计划

1. 保存原基线哈希，审计环境；并行核查Jason/FirstLight/clapha及Scroll两条原生路线，取得和核查真实回放小样本。
2. 选择实际可运行且与数据匹配的版本，先完成创建/下牌/步进/终局原生探针；固定程序/资源/数据指纹。先过关再扩容。
3. 接入统一适配器、玩家可见观测和执行反馈；解析真实journal，核对时间/坐标/初始条件，保存不确定性。
4. 少量开发回放定位差异；冻结正式100局，运行质量评估、确定性、交互夹具、断网冷启动及恢复测试。
5. 导出可信轨迹和整局分组；实际BC、checkpoint新进程加载及10完整agent局；记录WAIT/PLAY和失败分项。
6. 复现配套游戏renderer，控制同一局并核对5个锚点，保存真实截图/录屏；更新文档/命令/512MB理解包。

当前正式队列：`.venv-phase2-core/Scripts/python.exe -m royale_phase2 batch-replay --name formal100-v1 --split formal --grid-ticks 10`。进度和失败以queue/逐局episode为准。统一PowerShell入口、真实replay/batch、只读export/train/evaluate契约及viewer已实现；后续实验不能以命令存在计为通过。

正式100结束后先应用已审计的partial进化消费补丁（需新的backend hash），实际重放91e候选并核原生日志；不回写旧100原件。然后运行预声明learning16及formal2各3个独立reset（grid5，只r1供学习）。原生RPC始终只交给一个控制者。

之后按实际合格子集进行Parquet回读、>=10000唯一决策的BC/独立test、新进程10模型完整局；未合格来源保留分母并隔离。完整viewer/五锚点/录屏、两个全流程计算配置、冷VM隔离真实replay+模型局都必须实际运行。最后A–I验收重算、独立理解包CRC/逐文件hash/敏感扫描，才可完成Goal。

并行分工：native_engine负责Jason/FirstLight/clapha原生加载和可行性；engine_audit负责Scroll和宿主执行条件；research_library负责真实数据与转换；主代理负责状态、契约、集成、质量/离线/训练/渲染验收。
