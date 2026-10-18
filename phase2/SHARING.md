# 第二阶段项目理解包

`tools/phase2_share_pack.py`生成独立的第二阶段ZIP，压缩文件必须不超过500,000,000字节。它不覆盖第一阶段交付，也不是包含原生游戏材料的可直接运行分发。最终包须等实际acceptance、模型和样例就绪后，由根代理统一生成；当前先做自检。

## 自检与最终生成

```powershell
# 只扫描allowlist、计算SHA及检查敏感文件，不生成最终ZIP、不连接RPC。
.\.venv\Scripts\python.exe -X utf8 tools/phase2_share_pack.py --self-check

# 仅打包自有说明文档作为CRC canary，并验证损坏ZIP/危险文件拒绝；不是最终理解包。
.\.venv\Scripts\python.exe -X utf8 tools/phase2_share_pack.py --self-test

# 后续统一生成时使用真实路径；示例文件名不表示这些验收已通过。
.\.venv\Scripts\python.exe -X utf8 tools/phase2_share_pack.py --build `
  --acceptance outputs/phase2/acceptance.json `
  --checkpoint models/phase2/bc/checkpoint.json `
  --sample-episode outputs/phase2/replays/development/ordinary-feedback-fixed `
  --name phase2_context_final

# 已生成ZIP可重复CRC、逐文件SHA及敏感类型检查。
.\.venv\Scripts\python.exe -X utf8 tools/phase2_share_pack.py `
  --verify-zip outputs/share/phase2_context_final.zip
```

所有产物写入`outputs/share`，名字必须以`phase2_`开头。已有同名ZIP会报错；不会静默覆盖。默认self-check的报告明确`final_zip_created=false`。最终构建必须指定实际acceptance，GPT_README直接读取其内容与SHA，不把缺项或失败改成“已完成”。

最终learning/source/split manifest若采用其他路径，可以重复传入`--manifest <实际小型manifest.json>`。它们会以原来源路径和SHA记录，拒绝含原始payload或完整actions的内容。`--sample-episode`也可重复，合计最多3条实际消费样例。

## 明确纳入的范围

- 项目自己的`royale_phase2`文本源码、`phase2`中文文档、`tools/phase2*`白名单文本工具和对应测试。包含本地签名密码的APK构建工具排除，版本锁保留其来源SHA。
- version/frozen/action-proof及选定learning/split manifests，只包含身份与来源锁，不复制其引用的游戏素材或原始数据。
- 实际acceptance、精简逐局质量统计和最多3条真实脱敏派生样例。样例来自已保存的native actor/实际消费事件，保留原证据SHA；不包含原source ID、姓名、完整动作时间线、原始payload或源坐标。
- 显式指定的NumPy BC checkpoint及有限float数值weights。拒绝pickle、任意对象数组或不匹配的权重SHA。
- 有限、明确Apache2来源的FirstLight封装源码和LICENSE/NOTICE/固定commit。游戏库、数据表和素材不在该许可范围。Jason根wrapper许可未确认，不复制其源码；来源仍可由版本锁/文档查到。

## 明确排除

环境与缓存、VM/磁盘镜像、APK、so/dll/exe/pyd、游戏资源及完整提取catalog、原始Parquet/全量payload/完整actions、录像/未知素材、keystore/私钥/凭据不进入包。原项目中可运行所需的这些材料必须按版本锁另行准备，不能用替代空文件伪装。包中的相对路径/SHA引用不表示对应大证据文件已随包分发。

`PACKAGE_MANIFEST.json`逐项列payload大小、SHA256及来源；它自身的SHA记录在外部verification报告，避免自指哈希。ZIP完成后回读所有成员、检查CRC、逐文件SHA、重复/越界路径、总大小、二进制魔数、敏感后缀与常见私钥/token格式，再写ZIP SHA256。检查只验证实际纳入物与声明的规则，不替代数据/游戏资源的权利证明。

## 让GPT继续分析时

先让GPT读取`GPT_README.md`中的实际验收状态，再读`phase2/NATIVE_ENGINE_GUIDE.md`和`phase2/NATIVE_AUDIT.md`，然后定位`royale_phase2/backend.py`、`agent_env.py`、`trajectories.py`与`learning.py`。要求它区分实际原生运行、内部回归、源回放质量、官方真值与尚未验证的能力。

如果包中当前只有WAIT/Fireball/Arrows模型，应说明两法术离手后的轮转锁定和窄子空间范围。扩卡、单位可见性、隐藏状态搜索、完整技能消费或新的版本，都需要新的明确审计与运行证据，不能从静态catalog或已有单帧画面外推通过。
