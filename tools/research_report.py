"""Generate navigable research inventory from downloaded primary-source records."""
from pathlib import Path
import json
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "research"
DOC.mkdir(parents=True, exist_ok=True)
MANIFEST = ROOT / "references" / "library" / "manifest.json"

def link(path, label=None):
    return f"[{label or Path(path).name}](<{(ROOT / path).as_posix()}>)"

def escape(text):
    return str(text).replace("|", "\\|").replace("\n", " ")

def main():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    papers = [r for r in manifest["items"] if r["kind"] == "paper"]
    projects = [r for r in manifest["items"] if r["kind"] == "project"]
    summary = json.loads((ROOT / "outputs" / "research" / "download_summary.json").read_text(encoding="utf-8"))
    lines = ["# 原始论文逐项中文分析", "", "访问日期：2026-10-13。下载与 PDF 解析是本机验证；论文实验结果均为作者报告。以下条目没有声称在本机复现论文棋力。",
             "", "先读 [学习导航](INDEX.md)、[核心精读](DEEP_READS.md) 和 [方法比较](METHOD_COMPARISON.md)。", ""]
    bib = []
    for r in papers:
        key = r["id"]
        lines += [f"## {key}：{r['title']}", "", f"作者：{'; '.join(r.get('authors', []))}。年份：{r.get('year', '待核查')}。分类：{r['group']}。",
                  "", f"原始来源：[{r['title']}]({r.get('url', '')})。版本：`{r.get('version', 'unknown')}`。DOI：`{r.get('doi') or '未分配/未找到'}`。",
                  "", f"- 问题、输入与输出：{r['problem_io']}", f"- 方法：{r['method']}",
                  f"- 数据、实验与作者结论：{r['author_evaluation']}", f"- 局限：{r['limitation']}",
                  f"- 本项目应用：{r['application']}", "", f"本机状态：`{r['download_status']}` / `{r.get('verification_status', 'not_checked')}`。"]
        if r.get("local"):
            lines += [f"PDF：{link(r['local'])}；正文：{link(r['extracted_text']) if r.get('extracted_text') else '未提取'}。页数 {r.get('pages', 'unknown')}；SHA256 `{r['sha256']}`。"]
        if r.get("error"):
            lines += [f"下载失败记录：`{r['error']}`。保留来源并可重试，不计入已下载数。"]
        lines += [f"许可：{r['license']}。公开全文与代码许可是两回事。", ""]
        fields = {"title": r["title"], "author": " and ".join(r.get("authors", [])), "year": r.get("year", ""),
                  "url": r.get("url", ""), "note": "Accessed 2026-10-13; " + r.get("version", "")}
        if r.get("doi"):
            fields["doi"] = r["doi"]
        if r.get("arxiv"):
            fields["eprint"], fields["archivePrefix"] = r["arxiv"], "arXiv"
        bib += ["@article{" + key + ",", *["  " + k + " = {" + str(v).replace("&", "\\&") + "}," for k, v in fields.items()], "}\n"]
    (DOC / "PAPERS.md").write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "references" / "library" / "bibliography.bib").write_text("\n".join(bib), encoding="utf-8")

    lines = ["# 开源项目与可复用模块", "", "源码固定到 GitHub commit，以完整源码 ZIP 或重要代码/许可子集 ZIP 保存；每项 download_scope 明示范围。CRC 检查及 SHA256 支持离线核验。下载不等于安装、权重验证或对局复现。",
             "", "相关皇室战争引擎的源码审计与实际构建结果以项目 `docs/` 中的引擎审计报告和 `outputs/` 日志为准。本目录的下载器不执行第三方代码。", "",
             "| 项目 | 用途 | commit | 许可 | 下载/范围 | 本机状态 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in projects:
        lines += [f"| [{r['repository']}]({r['url']}) | {r['group']} | `{r.get('commit', '?')[:12]}` | {escape(r.get('license', 'unknown'))} | {r['download_status']} / {r.get('download_scope', '?')} | {escape(r['execution_status'])} |"]
    for r in projects:
        lines += ["", f"## {r['repository']}", "", f"用途：{r['group']}。", "", f"可复用：{r['reusable_modules']}。",
                  "", f"依赖与兼容：{r['dependencies']}。", "", f"修改成本与限制：{r['modification_cost']}。",
                  "", f"固定来源：[{r.get('commit', 'unresolved')}]({r['url'] + '/tree/' + r.get('commit', '')})。最新提交时间（抓取时）：`{r.get('latest_commit_date', 'unknown')}`，这不是对项目活跃度的保证。",
                  "", f"本机验证：{r['local_validation']}；安装/运行：`{r['execution_status']}`。"]
        if r.get("local"):
            lines += [f"下载范围：`{r.get('download_scope', 'unknown')}`。省略：{r.get('omitted', '完整上游源码 archive，不含 Git 历史')}。"]
            lines += [f"源码：{link(r['local'])}；清单：{link(r['inventory'])}。{r['files']} 个文件，{r['python_sources']} 个 Python 源码，{r['cpp_sources']} 个 C/C++ 源码。SHA256 `{r['sha256']}`。"]
            licfiles = [a for a in r.get("metadata_files", []) if "license" in a["archive_path"].lower() or "copying" in a["archive_path"].lower()]
            lines += ["许可证文件：" + ("，".join(link(a["local"]) for a in licfiles[:4]) if licfiles else "archive 未找到 LICENSE；不能自动视为开源授权。")]
        if r.get("error"):
            lines += [f"下载失败：`{r['error']}`。"]
        if r.get("remaining"):
            lines += [f"未完成：{r['remaining']}；小样本：{link(r['assets_manifest'])}。"]
        if r.get("audit_document"):
            lines += [f"源码及安装/运行实测：{link(r['audit_document'])}。"]
    (DOC / "PROJECTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    groups = Counter(r["group"] for r in papers)
    index = f"""# 皇室战争 AI 研究资料导航

这份资料库服务于可见观测、真实动作闭环和可验证模拟器。访问日期为 2026-10-13；直接研究与可迁移方法分开。当前登记 **{summary['papers']} 篇原始论文**，**{summary['pdf_downloaded']} 个 PDF 下载且 {summary['pdf_parsed']} 个已解析正文**；登记 **{summary['projects']} 个项目**，**{summary['source_archives_downloaded']} 个固定源码 archive/子集**（完整源码 {summary.get('full_source_archives',0)}、代码/许可子集或原始blob快照 {summary.get('source_subsets',0)}）。具体以 [manifest](<{MANIFEST.as_posix()}>) 和 [校验日志](<{(ROOT/'outputs/research/verification.json').as_posix()}>) 为准。资料不包含论文算法的本机棋力复现声明。

## 文件入口

- [33 篇逐项中文分析](PAPERS.md)：问题、输入输出、方法、作者实验、边界和本项目用途。
- [开源项目目录](PROJECTS.md)：commit、许可证、模块、依赖、修改成本与下载/安装状态。
- [核心论文正文精读](DEEP_READS.md)：方法细节、公式、页码、能做的实验和失败风险。
- [方法比较和优化定位](METHOD_COMPARISON.md)：按问题选择改进，不把新算法名字当作棋力。
- [研究缺口及实证路线](GAPS.md)：能由近似引擎支持的结论、必须真实对照的结论。
- [数据与权重获取计划](DATASETS.md)：已保存的小样本与受限外部资源。
- [BibTeX](<{(ROOT/'references/library/bibliography.bib').as_posix()}>)；[下载日志](<{(ROOT/'outputs/research/download_log.jsonl').as_posix()}>)。
- [资料库验收](<{(ROOT/'outputs/research/corpus_acceptance.json').as_posix()}>)：最低数量与已交付文件完整性；失败/部分数据资源仍明确列出。
- [从零学习本平台](<{(ROOT/'docs/LEARNING_PATH.md').as_posix()}>)：12 章，每章连接真实函数与本地论文。

## 从零开始的阅读顺序

1. 先运行平台、观察手牌/圣水/塔血和一次实际下牌；读项目建模文档，区分 state、observation、action、reward。在源码中跟一遍 `session → policy → backend`。
2. 阅读 SEAT 与 KataCR：一个主要是嵌入式近似模拟实验，另一个采用真实像素/设备；先弄清不同证据支持什么结论。
3. 阅读 action_masking、参数化动作两篇：能付得起的卡、能下的格子、WAIT 共同定义策略分布；边界错误优先修复。
4. 阅读 POMCP、DRQN：手牌/圣水不确定性是信念或记忆问题。用同一可见观测比较显式信念与记忆网络。
5. 阅读 DAgger、Decision Transformer、IQL/CQL：先把按整局划分的轨迹数据、等待标签、专家查询和评估弄正确，再换网络。
6. 阅读 PPO 与 GAE，启动短程训练并加载 checkpoint 完整打完一局；学习曲线只验证优化过程，独立对局才评价策略。
7. 阅读 PSRO/NFSP 与 AlphaZero：训练对手和验证对手分开，保存历史策略池，查看克制循环与跨卡组表现。
8. 需要真实设备时再读 YOLO/RT-DETR、DBNet、ByteTrack、domain randomization；先标注真实测试帧及误差，再接策略。
9. 高吞吐环境和足量轨迹齐备后，再研究 MuZero、Dreamer、Trajectory Transformer。先测多步模型误差和端到端 p95 延迟。

必备概念：概率分布与置信区间、POMDP、Bellman 目标、策略梯度、on/off-policy、数据分布偏移、动作屏蔽、模仿误差累积、自博弈、信念更新、MCTS/rollout、版本化数据、样本泄漏。

## 分类统计

| 类别 | 原始论文数 |
| --- | --- |
""" + "\n".join(f"| {group} | {n} |" for group, n in sorted(groups.items())) + """

## 复核与恢复下载

在项目根目录运行（使用项目自带 Python 环境）：

```powershell
.\\.venv\\Scripts\\python.exe -B tools\\research_fetch.py --verify
.\\.venv\\Scripts\\python.exe -B tools\\research_verify.py
.\\.venv\\Scripts\\python.exe -B tools\\research_fetch.py --papers
.\\.venv\\Scripts\\python.exe -B tools\\research_fetch.py --projects
.\\.venv\\Scripts\\python.exe -B tools\\research_fetch.py --projects --source-only --workers 3
.\\.venv\\Scripts\\python.exe -B tools\\research_assets.py
.\\.venv\\Scripts\\python.exe -B tools\\research_report.py
```

下载器缓存已完成资源、固定 commit，重跑不会追踪最新版；`--only ppo,r2d2` 可定向重试。本机 raw GitHub/metadata 通过配置代理，codeload 请求级无代理，不修改系统代理。大 archive 超时可加 `--source-only` 保存固定 commit 的重要源文件、依赖和许可；不会将子集标为完整。若 Python 环境没有 `pypdf`，PDF 保存/头部/SHA 校验照常完成，但正文解析状态明确为未执行；安装 `pypdf` 后可重跑解析。所有临时目录位于项目 `tmp/research`，缓存位于 `.cache/research`。

`research_fetch.py --verify` 是严格全项检查，会因未获得的 optional PDF 或未完整下载数据集返回非零；`research_verify.py` 检查实际交付文件完整性和 ≥30 PDF/≥15 固定项目源码/≥10 精读的最低目标，保留已知缺项。完整数据集和游戏专用模型下载与验证另见 DATASETS.md。官方机制一致性必须由真实录像/轨迹对照证明；论文/仓库的宣传和本项目自写测试都不能替代这项证据。
"""
    (DOC / "INDEX.md").write_text(index, encoding="utf-8")
    print(f"Wrote research reports for {len(papers)} papers and {len(projects)} projects")

if __name__ == "__main__":
    main()
