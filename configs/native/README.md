# 原生普通八卡初始化模板

`ordinary_eight_template.json` 来自 FirstLight_CR 固定提交 `3ade158c52bf33de3ffcea600ce380946b7de0d8` 的 [native_match.json](https://github.com/Jaasssoooonnnnn/FirstLight_CR/blob/3ade158c52bf33de3ffcea600ce380946b7de0d8/native_runner/data/native_match.json)，原始字节 SHA256 为 `75913c9364b8d1f4c69baf90abb6467bd32983e1e3d87245954d2e7dc3ee54b9`，大小 1083 字节。

模板只定义静态建局数据，不含历史动作，也不实现任何游戏物理。`royale_phase3.actions.match` 优先保留原本本地运行时的初始化文件；该文件缺失时才使用此模板，并明确填入固定八卡、seed、16 级卡牌及国王塔、本地玩家名称。Android 原生资源和协议身份仍需独立校验。

读取和生成初始化对象已经离线检查；该模板在当前 Codespaces 中的原生 reset 尚未验证。
