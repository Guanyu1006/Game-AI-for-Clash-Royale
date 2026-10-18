# 从零开始：本地皇室战争 AI 研究平台

服务器、浏览器与无界面训练使用同一套已审计的C++战斗逻辑。本平台尚不能保证官方所有交互一致，也不提供可连接官方客户端的完整私服。

## 启动与实际下牌

```powershell
Set-Location -LiteralPath 'D:\研究生\Clash Royal'
.\start_demo.ps1
```

终端出现 `http://127.0.0.1:18765` 后访问该地址。选择卡组和控制方式，点击“创建对局”。手动模式选择手牌并点击棋盘合法区域：公开事件增加、圣水减少、手牌轮转、单位进入真实战斗。点击“开始”以0.1秒tick推进，或“推进10秒”加速。选择搜索/规则/模仿/PPO后重新创建，可观看自动对战。

Ctrl+C停止服务；重跑命令即重开。models/platform已有短程实验模型，不需先训练才能体验。界面显示双方调试信息，agent收到另一份过滤观测，见ARCHITECTURE.md。

## 环境与安装

已验证Windows x64 / CPython3.14.5 / MSVC2022。`.venv`、`.venv-repro`、源码和缓存均在项目内。第一次安装或依赖损坏时：

```powershell
.\bootstrap.ps1
.\.venv\Scripts\python.exe -m royale_lab info
```

requirements.lock锁定准确版本。主依赖numpy/torch/Gymnasium；OpenCV支持原MVP。训练使用CPU；已检测RTX4060，但未配置CUDA版torch。

原生pyd按CPython3.14 ABI构建。换Python版本不能直接复用。更新引擎先停止服务器/训练/评估，再编译：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\build_native.ps1 -Tests
```

构建器读取已有MSVC，使用项目缓存pybind11。CMake预览版曾崩溃，最后采用直接cl编译；见原生审计。不删除源码/cache即可在已有工具链上重建。

## 检查与实验

```powershell
.\run.ps1 -Action tests
.\.venv\Scripts\python.exe -m royale_lab demo --policies learned greedy
.\.venv\Scripts\python.exe -m royale_lab train-bc --episodes 12 --epochs 20
.\.venv\Scripts\python.exe -m royale_lab train-ppo --steps 4096
.\.venv\Scripts\python.exe -m royale_lab evaluate --games 100 --workers 4
```

测试覆盖原MVP及平台接口、隐私、确定性、动作、模型加载。默认训练只验收流程，不代表收敛。评估换边、4卡组、多个种子、3类对手，仅说明本近似引擎表现。每局保存replay/决策日志。

“保存局势”保存精确内存快照和项目内JSON重演日志；恢复/分叉供同局势实验。磁盘局势采用种子+接受动作，不加载pickle。文件加载示例见TUTORIAL.md。

## 故障定位

| 现象 | 处理 |
|---|---|
| 地址不能访问 | 确认服务在运行；端口冲突可用 `serve --port 18766` |
| 模型不存在 | 运行对应train-bc/train-ppo，API会返回缺失错误 |
| Native extension not built | 使用Python3.14，停止服务后build_native.ps1 |
| DLL不能写入 | 项目Python进程仍持有pyd，先停止服务/评估 |
| pip超时 | 本机代理127.0.0.1:17890曾导致大下载挂起；检查终端代理或缓存，不改系统设置 |
| 下牌被拒绝 | 查feedback、费用、冷却、区域；重复command_id不会再次执行 |
| 快进不继续 | 终局需创建新局；训练/评估会自动reset |

继续阅读：MODELING → ARCHITECTURE → STRATEGY_MAP → TUTORIAL → ENGINE_GUIDE → LEARNING_PATH。最新验收和剩余项看根STATUS.md、DELIVERY_REPORT.md。
