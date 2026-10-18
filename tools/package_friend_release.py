"""Build and actually exercise an allowlisted, portable friend release."""
from __future__ import annotations
import ast
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEXT = {".py", ".ps1", ".cmd", ".bat", ".sh", ".md", ".txt", ".json", ".html",
        ".toml", ".lock", ".bib", ".cpp", ".cc", ".c", ".h", ".hpp", ".inc", ".S",
        ".java", ".gradle", ".properties", ".xml", ".yaml", ".yml"}
SECRET = [
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("openai_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{30,}\b")),
    ("aws_key", re.compile(rb"\bAKIA[A-Z0-9]{16}\b")),
    ("bearer", re.compile(rb"(?i)\bAuthorization\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._-]{20,}")),
]
SKIP_PARTS = {".git", "__pycache__", "user-provided", "runtime-assets", "node_modules"}
BLOCK_KEYS = {"raw", "raw_state", "native_state", "raw_payload", "payload_json", "raw_event",
              "raw_capture", "source_payload", "source_actions", "cmd", "evt", "wire", "states",
              "decisions", "password", "passwd", "private_key", "api_key", "access_token",
              "refresh_token", "authorization", "cookie", "credentials"}
PAYLOAD = {}
RECORDS = {}
EXCLUDED = []


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def safe(name):
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and ".." not in path.parts and ":" not in name
                and "\\" not in name and not set(path.parts) & SKIP_PARTS)


def add(name, data, origin="generated", source=None, transform=None):
    if not safe(name):
        raise ValueError("Unsafe payload path: " + name)
    if name in PAYLOAD:
        raise ValueError("Duplicate payload path: " + name)
    if data[:4] == b"\x7fELF" or data[:2] == b"MZ":
        raise ValueError("Native binary forbidden: " + name)
    for rule, pattern in SECRET:
        if pattern.search(data):
            raise ValueError("Credential pattern " + rule + " in " + name)
    PAYLOAD[name] = data
    RECORDS[name] = {"path": name, "bytes": len(data), "sha256": sha(data), "origin": origin}
    if source:
        RECORDS[name]["source"] = source
        RECORDS[name]["source_sha256"] = sha((ROOT / source).read_bytes())
    if transform:
        RECORDS[name]["transformation"] = transform


def file(path, origin="project_source"):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Source symlink: " + str(path))
    name = path.relative_to(ROOT).as_posix()
    data = path.read_bytes()
    transformed = None
    if path.suffix == ".py":
        text = data.decode("utf-8-sig")
        # Keep the build procedure, require the recipient's own debug password.
        text, count = re.subn(r"(?m)^(\s*)password\s*=\s*(['\"])[^'\"]+\2\s*$",
                             r"\1password=__import__('os').environ['ROYALE_LOCAL_SIGNING_PASSWORD']", text)
        if count:
            data = text.encode("utf-8")
            transformed = "Local debug password replaced by ROYALE_LOCAL_SIGNING_PASSWORD; original untouched"
        ast.parse(data, filename=name)
    add(name, data, origin, name, transformed)


def source_tree(folder, origin="project_source", exclude=()):
    base = ROOT / folder
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(base)
        if set(rel.parts) & SKIP_PARTS or any(part.startswith(".venv") for part in rel.parts):
            continue
        if any(rel.as_posix().startswith(prefix) for prefix in exclude):
            continue
        if path.suffix not in TEXT and path.name not in {"LICENSE", "NOTICE", ".env.example", ".gitignore"}:
            continue
        if path.stat().st_size > 5_000_000:
            EXCLUDED.append({"path": path.relative_to(ROOT).as_posix(), "reason": "Oversized text; metadata only"})
            continue
        file(path, origin)


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k.lower() not in BLOCK_KEYS
                and not (k == "actions" and isinstance(v, (dict, list)))}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def report(path):
    path = ROOT / path
    if not path.is_file() or path.stat().st_size > 3_000_000:
        return
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    name = path.relative_to(ROOT).as_posix()
    add(name, encoded(scrub(data)), "saved_evidence_summary", name,
        "Raw payload/timeline fields removed; historical verdicts remain unchanged")


def numeric_data():
    import numpy as np
    import pyarrow.parquet as pq
    from royale_phase2.learning import actor_features, dataset_arrays, forward, decode_policy_output, action_from_index
    meta_path = ROOT / "outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    weights = ROOT / meta["weights"]["path"]
    if sha(weights.read_bytes()) != meta["weights"]["sha256"]:
        raise ValueError("Original model hash mismatch")
    parquet = ROOT / "outputs/phase2/learning_pipeline/export/trajectories.parquet"
    if sha(parquet.read_bytes()) != meta["training_data"]["parquet_sha256"]:
        raise ValueError("Training Parquet differs from selected checkpoint")
    table = pq.read_table(parquet, columns=["eligible_hard_e", "row_json"])
    rows = [json.loads(raw) for eligible, raw in zip(table["eligible_hard_e"].to_pylist(),
                                                    table["row_json"].to_pylist()) if eligible]
    if len(rows) != meta["training_data"]["eligible_decisions"]:
        raise ValueError("Eligible training count changed")
    arrays = {}
    counts = {}
    for split in ("train", "validation", "test"):
        subset = [row for row in rows if row["learning_split"] == split]
        values = dataset_arrays(subset, meta["feature_configuration"], meta["profile"])
        for key, value in zip(("x", "target", "xy", "masks"), values):
            arrays[split + "_" + key] = value
        counts[split] = {"decisions": len(subset), "episodes": len({r["source_episode_id"] for r in subset}),
                         "play_labels": sum(r["action"]["kind"] == "play_card" for r in subset)}
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    add("data/friend_numeric/training_arrays.npz", buffer.getvalue(), "derived_numeric_training_cache")
    add("models/friend_bc/checkpoint.json", meta_path.read_bytes(), "unchanged_checkpoint_alias",
        meta_path.relative_to(ROOT).as_posix())
    add("models/friend_bc/weights.npz", weights.read_bytes(), "unchanged_numeric_weights_alias",
        weights.relative_to(ROOT).as_posix())
    with np.load(weights, allow_pickle=False) as archive:
        parameters = {key: archive[key] for key in archive.files}
    selected = [
        next(r for r in rows if r["action"]["kind"] == "play_card"),
        next(r for r in rows if r["action"]["kind"] == "wait" and any(r["legal_action_mask"]["values"][1:])),
        next(r for r in rows if sum(r["legal_action_mask"]["values"]) == 1),
    ]
    samples = []
    for index, row in enumerate(selected):
        mask = np.asarray([row["legal_action_mask"]["values"]], dtype=bool)
        x = actor_features(row["actor"], meta["feature_configuration"])[None, :]
        _, probability, xy = forward(parameters, x, mask)
        action_index, point = decode_policy_output(probability, xy, mask, play_bias=meta.get("inference_play_bias", 0.))
        samples.append({"sample_id": "real-derived-" + str(index + 1), "actor": row["actor"],
                        "recorded_mask": row["legal_action_mask"]["values"],
                        "label": row["action"],
                        "source_row_sha256": row["row_sha256"],
                        "expected_deployment_decoding": action_from_index(row["actor"], int(action_index[0]), point[0])})
    add("examples/friend_native_samples.json", encoded({
        "scope": "Three anonymized derived public-actor examples; no raw replay or full action timeline",
        "source_parquet_sha256": sha(parquet.read_bytes()), "samples": samples}))
    add("data/friend_numeric/README.txt", (
        "这是项目生成的数值训练缓存，不是原始回放、游戏资源或官方轨迹真值。\n"
        "只含白名单actor特征x、监督target/xy及当时记录的mask；没有姓名、原始payload、"
        "来源对局ID、原生隐藏状态或完整动作日志。train/validation/test保持原整局分组。\n"
        "复现时重用已有质量筛选结果，不重新证明原生合法性或官方一致；原始证明的部分"
        "外部依赖另备。只有52次出牌，不能将18,440行当作强策略训练经验。\n"
        "TRAIN_FROM_CACHE.py使用实际项目的损失和梯度，并按原checkpoint配置重跑优化。\n"
        "数值缓存与权重均为非object数组，加载allow_pickle=False。\n"
        + json.dumps(counts, ensure_ascii=False, indent=2)).encode("utf-8"))
    return counts


def docs(counts, name):
    add("requirements-friend.txt", b"numpy==2.2.6\n")
    add("00_START_HERE.txt", (
        "皇家战争新版系统：朋友理解与复现包\n\n"
        "先双击00_START_HERE.html看目录和真实数值样例；无需安装或连接游戏。\n"
        "这是固定日期的源码快照：阶段二原生平台已有运行证据，阶段三仍在实施；"
        "研究设计与实施指令不等于已完成能力。原生BC仍是WAIT/火球/箭雨受限基线。\n\n"
        "Windows快速复现（推荐Python3.12，打包验证使用独立3.12环境）：\n"
        "  powershell -NoProfile -ExecutionPolicy Bypass -File .\\bootstrap_friend.ps1\n"
        "  .\\.venv-friend\\Scripts\\python.exe -B TRAIN_FROM_CACHE.py --epochs 20\n"
        "已有Python/非Windows：\n"
        "  python -m venv .venv-friend\n"
        "  <venv-python> -m pip install -r requirements-friend.txt\n"
        "  <venv-python> VERIFY_PACKAGE.py\n"
        "  <venv-python> SMOKE_TEST.py\n"
        "  <venv-python> TRAIN_FROM_CACHE.py --epochs 20\n"
        "bootstrap只在解压目录创建环境和缓存，依赖安装需要联网；后面的校验、测试和"
        "数值训练均离线执行，不接触原生RPC。\n\n"
        "复现层级：\n"
        "A. 阅读、自检、离线单元测试、保存模型推理、数值BC训练：包内材料齐备。\n"
        "B. 原始数据重建/严格合法性重审：需按EXTERNAL_ASSETS.json与版本清单补来源材料。\n"
        "C. 新的原生对局与画面：还需自备固定版本APK/libg/资源、探针构建产物、"
        "Android与虚拟化工具链；这些未包含，不能直接启动就声称原生复现成功。\n\n"
        "优先阅读：01_SYSTEM_MAP.txt → outputs/research_design/AI_SYSTEM_DESIGN_20261018.txt"
        " → phase2/PLATFORM_TUTORIAL_ZH.md → phase3/STATUS.md。\n"
        "实际运行证据见outputs/phase2/acceptance.json、outputs/platform_next/platform_acceptance.json。"
        "这些是历史证据，引用的全部大文件并非都随包。\n"
        "本包不自动执行历史CODEX_PROMPT文件，不自动启动VM、登录账号或安装系统组件。\n"
        "PACKAGE_MANIFEST.json为逐文件SHA256清单；VERIFY_PACKAGE.py不需要第三方依赖。\n"
        "reproduction/中是打包时实际离线测试与训练报告。新增运行写friend_runs，勿覆盖历史模型。\n"
        "所有源路径在包内按项目相对目录保留；文档里的D盘绝对路径是原机器的历史引用。\n\n"
        "明确排除：私钥/凭据/keystore、已有环境与缓存、VM镜像、APK/so/exe等运行二进制、"
        "游戏资源及提取catalog、原始大数据和完整动作/状态日志、第三方未授权源码快照。\n"
        "本地debug签名密码已在分发副本改为环境变量ROYALE_LOCAL_SIGNING_PASSWORD；"
        "原项目未改。自备资源后需另建本地签名材料。\n"
        "完整训练研究还可能需要Torch/PyArrow/Gym；不要运行旧根bootstrap来安装"
        "所有历史依赖。新手离线复现只需requirements-friend.txt。\n\n"
        "缓存统计：\n" + json.dumps(counts, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    add("01_SYSTEM_MAP.txt", (
        "当前系统与阅读路线\n\n"
        "royale_phase2/backend.py：固定版本原生接口与执行反馈。\n"
        "royale_phase2/visibility.py、battlefield.py：保守actor与研究战场投影。\n"
        "royale_phase2/platform.py、human.py、*.html：研究控制台与人机演示。\n"
        "royale_phase2/trajectories.py、learning.py：质量轨迹与NumPy BC。\n"
        "royale_phase3/：共用评估、观察契约、belief、动作与自采的实施中模块。\n"
        "tests_phase3/、tests/test_phase3_observation.py：部分依赖原机器证据；"
        "便携SMOKE_TEST只运行明确可自包含的子集。\n"
        "royale_ai/、royale_lab/：原12卡MVP与第一阶段近似引擎基线，不能与原生混报。\n"
        "third_party/phase2/firstlight/：Apache授权的封装/模型源码，"
        "不含游戏资源、权重、环境。LICENSE/NOTICE与来源锁保留。\n"
        "outputs/research_design/：三个新仓库审计与综合方案；未复制未明确授权的源码snapshot。\n"
        "CODEX_PHASE3_PROMPT.txt：十项改进任务书，配合phase3实际状态阅读。\n"
        "docs/research/：论文/项目精读与入口，仅含自有总结/索引，不分发论文全文。\n\n"
        "数值模型复现：原权重models/friend_bc；原始来源路径的checkpoint亦保留。"
        "别将别名metadata里的旧路径和version引用误认成外部依赖已经随包。\n"
        "native法术模型的高WAIT比例与有限动作范围是已知限制。离线推理重用记录的mask，"
        "不重新声明全卡牌合法性。\n\n"
        "原生恢复顺序：先看02_NATIVE_REPRODUCTION.txt，再核对资源指纹，"
        "最后在独占控制窗口启动。当前minimal无完整snapshot/fork，不能与别的控制者共用RPC。\n"
        "包是创建时快照，正在实施的阶段三后续修改不会自动进入本ZIP。\n").encode("utf-8"))
    add("02_NATIVE_REPRODUCTION.txt", (
        "原生对局的外部材料与恢复边界\n\n"
        "锁定Null's Royale client15.535.13 / content15.535.86 / ARM64，20Hz。"
        "具体以data/phase2/version_manifest.json的组件SHA为准，不能只比版本字符串。\n"
        "EXTERNAL_ASSETS.json列出版本锁引用但本包未包含的文件、大小和预期hash。"
        "这是恢复清单，不是下载或再分发许可。\n"
        "原机器使用项目内QEMU-WHPX→Ubuntu→Redroid→ARM翻译层；"
        "参考phase2/OFFLINE.md、SECONDARY_AUDIT.md、NATIVE_ENGINE_GUIDE.md、"
        "PLATFORM_TUTORIAL_ZH.md及tools/phase2_platform_runtime_README.txt。\n"
        "朋友可按其自有环境准备同版本运行时，但必须重新核验适配与确定性。"
        "不承诺Linux/macOS原生工具零改动通用。\n"
        "私钥、known_hosts、VM磁盘、安装包、原生库及游戏表/素材未复制。"
        "应生成朋友自己的SSH与本地debug签名材料，按其机器配置路径，"
        "不要复用原机器的凭据或空文件占位。\n"
        "已附受Apache许可覆盖的FirstLight源码和minimal probe源码；"
        "未复制许可未确认的Jason根wrapper及两个新库源码快照。"
        "它们的固定commit/文件入口见研究审计；需要时按上游许可取得。\n"
        "构建依赖SDK/NDK/JDK与QEMU等工具未包含；项目内依赖锁和来源记录保留。"
        "构建新的APK会改变整体hash，需根据合法本地构建重新建立身份记录，"
        "不能改报告绕过校验。\n"
        "原始回放与Parquet未打入包；数值训练缓存不能替代真实回放重建。"
        "现有原生质量报告和失败分母仍保留，不把未知改成已验证。\n"
        "完成外部材料准备后先doctor/版本核验和单局smoke，再批处理/训练。"
        "运行命令参照平台教程；本包便携自检不会启动或触碰本机VM。\n").encode("utf-8"))
    samples = json.loads(PAYLOAD["examples/friend_native_samples.json"])
    # A code/data reading index, not a native-rendering substitute.
    html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>皇家战争AI新版系统</title>
<style>body{font:16px/1.7 system-ui;max-width:1000px;margin:40px auto;padding:0 22px;color:#1c2838;background:#f5f7fa}
article,pre{background:white;padding:22px;border-radius:12px}a{color:#175da8}pre{white-space:pre-wrap;font-size:13px}
.note{border-left:4px solid #d99b22;padding:12px;background:#fff6df}li{margin:8px 0}</style>
<h1>皇家战争AI：新版系统理解与复现包</h1>
<p class="note">阶段二有原生运行历史证据；阶段三正在实施。此页为离线阅读入口和三个真实派生数值样例，不运行游戏。</p>
<article><h2>先从这里开始</h2><ol>
<li><a href="00_START_HERE.txt">复现步骤、范围与限制</a></li>
<li><a href="01_SYSTEM_MAP.txt">代码与资料导航</a></li>
<li><a href="outputs/research_design/AI_SYSTEM_DESIGN_20261018.txt">综合设计与三个仓库审计</a></li>
<li><a href="phase2/PLATFORM_TUTORIAL_ZH.md">原生平台教程</a> · <a href="phase3/STATUS.md">第三阶段状态快照</a></li>
<li><a href="02_NATIVE_REPRODUCTION.txt">原生运行所需外部材料</a></li></ol>
<p>命令：先运行 bootstrap_friend.ps1，再运行 TRAIN_FROM_CACHE.py --epochs 20。只需NumPy即可完成离线模型与训练复现。</p>
</article><h2>真实派生样例：输入、记录的mask、保存模型输出</h2><pre id="examples"></pre>
<script>const examples=__DATA__;document.getElementById('examples').textContent=JSON.stringify(examples,null,2);</script></html>"""
    add("00_START_HERE.html", html.replace("__DATA__", json.dumps(samples, ensure_ascii=False).replace("</", "<\\/")).encode("utf-8"))


def materialize(folder):
    for name, data in PAYLOAD.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {"schema": "royale-friend-snapshot.v1", "snapshot_utc": datetime.now(timezone.utc).isoformat(),
                "files": sorted(RECORDS.values(), key=lambda r: r["path"]),
                "excluded": EXCLUDED, "manifest_self_hash": "External ZIP verification report; avoids self-reference"}
    (folder / "PACKAGE_MANIFEST.json").write_bytes(encoded(manifest))


def run(folder, arguments, label):
    environment = dict(os.environ, PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
                       OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    result = subprocess.run([sys.executable, "-B", *arguments], cwd=folder, env=environment,
                            capture_output=True, encoding="utf-8", errors="replace", timeout=180)
    if result.returncode:
        raise RuntimeError(label + " failed:\n" + (result.stdout + result.stderr)[-10000:])
    return result


def main():
    capture_started = datetime.now(timezone.utc).isoformat()
    name = "RoyaleAI_NewSystem_" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    stage = ROOT / "outputs/share/release_work" / name
    stage.mkdir(parents=True, exist_ok=False)
    for folder in ("royale_ai", "royale_lab", "royale_phase2", "royale_phase3", "phase2", "phase3",
                   "docs", "examples", "tools", "tests", "tests_phase2", "tests_phase3", "tests_platform"):
        source_tree(folder)
    for path in sorted(ROOT.iterdir()):
        if path.is_file() and (path.suffix in TEXT or path.name == ".gitignore"):
            file(path)
    source_tree("outputs/research_design", "first_party_research_audit",
                exclude=("simulator_audit/snapshot/",))
    source_tree("third_party/phase2/firstlight", "Apache2_wrapper_source",
                exclude=("native_runner/data/", "native_runner/probe/out/", "docs/assets/", "user-provided/"))
    source_tree("third_party/phase2/jason/vendor/firstlight/probe", "Apache2_vendored_probe")
    for license_name in ("LICENSE", "NOTICE"):
        path = ROOT / "third_party/phase2/firstlight" / license_name
        add("third_party/phase2/jason/vendor/firstlight/" + license_name, path.read_bytes(),
            "Apache2_vendored_probe_license", path.relative_to(ROOT).as_posix())
    for support in ("VERIFY_PACKAGE.py", "SMOKE_TEST.py", "TRAIN_FROM_CACHE.py", "bootstrap_friend.ps1"):
        path = ROOT / "tools/friend_bundle_support" / support
        add(support, path.read_bytes(), "portable_release_entrypoint", path.relative_to(ROOT).as_posix())
    for name_in in ("version_manifest.json", "frozen_manifest.json", "action_subspace_proof.json"):
        path = ROOT / "data/phase2" / name_in
        file(path, "version_and_source_manifest")
    source_tree("data/phase2/learning", "split_and_source_manifest")
    for model in sorted((ROOT / "outputs/phase2/learning_pipeline").glob("model*")):
        if model.is_dir():
            for path in sorted(model.iterdir()):
                if path.suffix in {".json", ".npz"}:
                    file(path, "historical_numpy_model")
    for rel in ("outputs/phase2/acceptance.json", "outputs/phase2/formal_audit_complete.json",
                "outputs/platform_next/platform_acceptance.json",
                "outputs/platform_next/audit/platform_gap_audit.json",
                "outputs/phase2/learning_pipeline/export/export_manifest.json"):
        report(rel)
    for path in sorted((ROOT / "outputs/phase3").rglob("*.json")):
        if path.name not in {"actions.json", "states.json", "payload.json"} and path.stat().st_size < 2_000_000:
            report(path.relative_to(ROOT).as_posix())
    for path in sorted((ROOT / "references/library").glob("*")):
        if path.is_file() and path.name in {"manifest.json", "assets_manifest.json", "bibliography.bib"}:
            file(path, "research_index_only")
    counts = numeric_data()
    docs(counts, name)
    version = json.loads(PAYLOAD["data/phase2/version_manifest.json"])
    missing = []
    for asset in version.get("assets", []):
        if asset.get("path") not in PAYLOAD:
            missing.append(asset)
    add("EXTERNAL_ASSETS.json", encoded({"schema": "friend-external-assets.v1",
        "scope": "Version-lock references not distributed. No empty replacements or rights claims.",
        "assets": missing}))
    add("THIRD_PARTY_SOURCES.txt", (
        "附包FirstLight封装与vendored probe遵循所附Apache2 LICENSE/NOTICE。\n"
        "FirstLight GitLab71944e3993b594b3cc89b38768b5e817dfbdbe0e；"
        "GitHub研究对照3ade158c52bf33de3ffcea600ce380946b7de0d8。\n"
        "Jason-XII/clash-royale-simulator@82bcbf3088102318125fc0193f0e5bc38e7886b2，"
        "Jason-XII/cr-memory-reader@9af94ca2f273ce526dda6256bdbd5d5555fb82d4："
        "本包仅含自有审计，不复制未明确授权源码snapshot。\n"
        "数据来源见现有manifest与phase2/DATA_AUDIT.md；数值缓存为本项目转换输出，"
        "不含原始payload和完整源事件。游戏库与资源不在封装源码许可范围。\n").encode("utf-8"))
    EXCLUDED.extend([
        {"path": ".venv*/ .cache/ .tmp/ tmp/", "reason": "Machine-specific environment, caches, credentials, VM disks"},
        {"path": "APK/libg/probe binaries/game resources/catalog", "reason": "External runtime and redistribution boundary"},
        {"path": "raw Parquet / full action-state logs / third-party unlicensed snapshots", "reason": "Use indices, hashes and sanitized derived numeric cache"},
        {"path": "screenshots/videos/papers", "reason": "Portable text/data release; original assets/large media not redistributed"},
    ])
    # Freeze a bounded capture window before long-running validation. Later
    # development is allowed to continue; it is recorded, not silently merged.
    for row in RECORDS.values():
        if "source" in row and sha((ROOT / row["source"]).read_bytes()) != row["source_sha256"]:
            raise RuntimeError("Source changed within capture window; rebuild: " + row["source"])
    capture_finished = datetime.now(timezone.utc).isoformat()
    add("SNAPSHOT_NOTICE.txt", (
        "本包是明确时间窗口内捕获的固定源码快照，不会跟随工作区继续更新。\n"
        "捕获开始UTC：" + capture_started + "\n捕获结束UTC：" + capture_finished + "\n"
        "捕获结束前逐文件核对来源hash；随后离线测试和训练只运行已复制快照。\n"
        "验证期间原工作区若继续开发，差异记录在reproduction/LIVE_WORKSPACE_CHANGES.json；"
        "这些新改动不混入本包。阶段三仍是实施中状态。\n").encode("utf-8"))
    materialize(stage)
    run(stage, ["SMOKE_TEST.py"], "staged portable smoke")
    run(stage, ["TRAIN_FROM_CACHE.py", "--epochs", "20"], "staged 20-epoch numeric training")
    add("reproduction/packing_smoke_report.json", (stage / "friend_runs/smoke_report.json").read_bytes(),
        "actual_release_validation")
    training = json.loads((stage / "friend_runs/bc_reproduction/report.json").read_text(encoding="utf-8"))
    add("reproduction/packing_training_report.json", encoded(training), "actual_release_validation")
    workspace_changes = []
    for row in RECORDS.values():
        if "source" in row and sha((ROOT / row["source"]).read_bytes()) != row["source_sha256"]:
            workspace_changes.append({"path": row["source"], "captured_sha256": row["source_sha256"],
                                      "later_sha256": sha((ROOT / row["source"]).read_bytes())})
    add("reproduction/LIVE_WORKSPACE_CHANGES.json", encoded({
        "scope": "Live source updates after the snapshot was frozen; excluded from this release",
        "capture_started_utc": capture_started, "capture_finished_utc": capture_finished,
        "changed_files": workspace_changes}))
    materialize(stage)
    destination = ROOT / "outputs/share" / (name + ".zip")
    if destination.exists():
        raise FileExistsError(destination)
    partial = destination.with_suffix(".zip.partial")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted([*PAYLOAD, "PACKAGE_MANIFEST.json"]):
            archive.write(stage / relative, name + "/" + relative)
    partial.replace(destination)
    if destination.stat().st_size > 500_000_000:
        raise ValueError("Unexpectedly oversized delivery")
    restored = ROOT / "outputs/share/verification" / name
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC failure")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate ZIP paths")
        for member in archive.infolist():
            if not safe(member.filename):
                raise ValueError("Unsafe ZIP member")
            path = (restored / member.filename).resolve()
            path.relative_to(restored.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(member))
    unpacked = restored / name
    run(unpacked, ["SMOKE_TEST.py"], "relocated extracted ZIP smoke")
    run(unpacked, ["TRAIN_FROM_CACHE.py", "--epochs", "20", "--output", "friend_runs/recheck"],
        "relocated extracted ZIP training")
    rerun = json.loads((unpacked / "friend_runs/recheck/report.json").read_text(encoding="utf-8"))
    if rerun["parameters_sha256"] != training["parameters_sha256"]:
        raise ValueError("Relocated training is not deterministic")
    result = {"schema": "friend-release-verification.v1", "zip": destination.relative_to(ROOT).as_posix(),
              "bytes": destination.stat().st_size, "sha256": sha(destination.read_bytes()),
              "files": len(names), "uncompressed_bytes": sum(len(v) for v in PAYLOAD.values()),
              "crc": "pass", "all_payload_sha256": "pass", "credential_patterns": "pass",
              "relocated_offline_smoke": json.loads((unpacked / "friend_runs/smoke_report.json").read_text(encoding="utf-8")),
              "numeric_training_epochs": 20, "relocated_training_deterministic": True,
              "matches_original_saved_tensors": rerun["matches_saved_parameters"],
              "max_abs_parameter_difference": rerun["max_abs_parameter_difference"],
              "numeric_cache_counts": counts, "source_consistency": "capture_hashes_verified_before_validation",
              "capture_started_utc": capture_started, "capture_finished_utc": capture_finished,
              "live_workspace_updates_after_capture": workspace_changes,
              "native_runtime_tested_by_packaging": False}
    destination.with_suffix(".verification.json").write_bytes(encoded(result))
    destination.with_suffix(".zip.sha256").write_text(result["sha256"] + "  " + destination.name + "\n", encoding="ascii")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
