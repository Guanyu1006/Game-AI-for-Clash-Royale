"""Refresh Chinese continuation state from the currently recorded evidence."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.dataset import write_json


def read_if(path):
    path=local(path)
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else None


def main():
    configure();queue=read_if('outputs/phase2/batches/formal100-v1/queue.json')
    records=[]
    if queue:
        records=[read_if(j['result']['path']) for j in queue['jobs'] if j['state']=='recorded']
    quality={key:dict(Counter(r['quality'][key] for r in records)) for key in
             ('parse_valid','runtime_replayable','deterministic','outcome_consistent','trajectory_validated')}
    acceptance=read_if('outputs/phase2/acceptance.json')
    gates={k:v['status'] for k,v in acceptance['checks'].items()} if acceptance else {}
    state={'generated_utc':datetime.now(timezone.utc).isoformat(),'goal_status':'active',
           'formal_recorded':len(records),'formal_denominator':100,'formal_quality_dimensions':quality,
           'formal_job_states':dict(Counter(j['state'] for j in queue['jobs'])) if queue else {},
           'last_acceptance_snapshot':gates,'all_required_passed':acceptance.get('all_required_passed',False) if acceptance else False}
    write_json(local('outputs/phase2/progress.json'),state)
    text=f'''# 第二阶段接续状态

更新：{state['generated_utc']}。持续Goal为active，全部硬验收通过且最终理解包验证后才可完成。

正式冻结分母100，当前已记录 **{len(records)}/100**。质量维度互相重叠，不能相加：

```json
{json.dumps(quality,ensure_ascii=False,indent=2)}
```

最近一次实际验收脚本快照：`{json.dumps(gates,ensure_ascii=False)}`。该快照会落后于运行中队列；只有重新执行acceptance并重算产物才能更新，不能手工补passed。

已经实际完成的基础能力：

- 原生ARM64 libg/probe加载、创建/重置/下牌/步进/自然终局，renderer固定画面和raw binding validator已通过F；不宣称官方艺术资源或官方全轨迹一致。
- 客户端15.535.13、content15.535.86；830项版本资产验证，652项安装hash实际相同。整体APK与上游旧archive SHA不同，组件证据精确；scdb SHA1元数据例外、fingerprint等价派生均保留。
- 项目内QEMU-WHPX/Ubuntu/Redroid/ARM翻译层可运行，restrict=on外网隔离、冷启动、真实replay和模型局联合B已通过；首次guest networkd等失败报告保留。
- 20项真实原生内部机制回归最新通过，旧失败与重测保留。独立官方时序参考0，不宣称官方全轨迹一致。
- 原子与two-RPC推进同语义；H已实际测得atomic约1.67倍wall-time加速，并保存Python/QEMU资源证据。
- 实际消费后kill与恢复：partial未计完成，fresh reset后43动作全部消费、只计1局。早期worker.log哈希错误及Windows原子替换冲突均保留日志。
- 正式100+开发20源清单固定；另65局学习扩展和16+2源字段cohort独立冻结。重复采集只用r1学习，r2/r3只作证据。
- actor保守白名单、私有字段反例、两类普通spell子空间真实正/负例已通过。非塔实体可见性、全卡牌placement和通用技能消费尚有限制。
- 真实partial手牌3张的推理only-WAIT边界已修复并离线验证。严格导出26,068行、18,440个hard-E样本，训练/验证/测试为13,431/3,348/1,661；variant checkpoint通过独立留出和G的10局原生实战（14次真实模型消费）。WAIT偏置、两法术动作子空间和spell-first seed fixture均在报告中明确，不计诊断数据。
- 旧基线156证据hash未变，26+21旧回归通过。干净project core环境与数据环境检查、17项统一入口只读检查实际通过。

当前原生RPC只允许一个owner；验收批次已完成，默认不保持控制器。任何新的viewer、batch或agent运行仍须独占窗口并先确认旧QEMU/tunnel已关闭。

接续优先级：

1. 以 `outputs/phase2/acceptance.json` 和本文件的实际快照为准；任何源码/资源变化后必须重新运行受影响门。
2. 后续策略研究应先扩展完整卡组/兵种/建筑/技能的公开动作子空间，再建立新的冻结cohort和三轮reset证据，不回写本轮样本。
3. 继续扩充公开回放前先检查等级有损、私有字段泄漏和source/group split；当前模型只用于研究闭环，不是官方强度或官方私服。
4. 重新运行原生工具必须遵守单owner、项目内缓存和网络隔离规则；失败批次保留并在 `BLOCKERS.md` 记录。
5. 最终理解包只纳入源代码、证据摘要、文档和可复现实验命令；构建后执行CRC、逐文件SHA、敏感扫描和≤512MB验收。

关键入口：[QUICKSTART](QUICKSTART.md)、[中文教程](TUTORIAL.md)、[原生源码与故障指南](NATIVE_ENGINE_GUIDE.md)、[数据审计](DATA_AUDIT.md)、[学习](TRAJECTORIES_AND_BC.md)、[画面](VIEWER.md)、[离线](OFFLINE.md)、[分享边界](SHARING.md)。
'''
    local('phase2/STATUS.md').write_text(text,encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
