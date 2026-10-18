"""Rebuild plots and descriptive statistics from the frozen platform evidence.

Run with .venv-audit (matplotlib), not the main runtime environment.
All caches and outputs are deliberately confined to the project directory.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/platform/figures"
for key, relative in {"MPLCONFIGDIR": ".cache/matplotlib", "TEMP": ".tmp", "TMP": ".tmp"}.items():
    folder = ROOT / relative
    folder.mkdir(parents=True, exist_ok=True)
    os.environ[key] = str(folder)
os.environ["MPLBACKEND"] = "Agg"

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ORDER = ["random", "greedy", "search", "learned", "ppo", "search_no_response", "ppo_sample"]
LABELS = {
    "random": "Random", "greedy": "Greedy", "search": "Search + response",
    "learned": "Behavior cloning", "ppo": "PPO argmax", "search_no_response": "Search (no response)",
    "ppo_sample": "PPO sampling",
}
BLUE, TEAL, ORANGE, GRAY = "#244c78", "#168578", "#ce733c", "#6e7a89"
COLORS = [GRAY, BLUE, TEAL, BLUE, ORANGE, TEAL, ORANGE]


def read_json(relative):
    return json.loads((ROOT / relative).read_text(encoding="utf-8-sig"))


def read_csv(relative):
    with (ROOT / relative).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wilson(k, n, z=1.959963984540054):
    p = k / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1-p) / n + z*z/(4*n*n)) / denominator
    return [max(0.0, center-half), min(1.0, center+half)]


def save(fig, name):
    fig.savefig(OUT / (name + ".png"), dpi=180, bbox_inches="tight", facecolor="white")
    fig.savefig(OUT / (name + ".svg"), bbox_inches="tight", facecolor="white", metadata={"Date": None})
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = read_csv("outputs/evaluation/main/summary.csv")
    summary = {r["policy"]: r for r in rows}
    report = read_json("outputs/evaluation/main/report.json")
    scenarios = read_json("outputs/evaluation/main/scenarios.json")
    with (ROOT / "outputs/evaluation/main/records.jsonl").open(encoding="utf-8-sig") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    assert len(records) == 700 and set(summary) == set(ORDER)
    assert len({(r["policy"], r["index"]) for r in records}) == 700
    assert not any(r.get("crash", False) for r in records)
    by_policy = {p: sorted([r for r in records if r["policy"] == p], key=lambda r: r["index"]) for p in ORDER}
    assert all([r["index"] for r in rs] == list(range(100)) for rs in by_policy.values())
    for i in range(0, 100, 2):
        a, b = scenarios[i], scenarios[i+1]
        assert a["side"] == 0 and b["side"] == 1
        assert {k:v for k,v in a.items() if k not in ("index", "side")} == {k:v for k,v in b.items() if k not in ("index", "side")}

    policy_metrics = []
    for p in ORDER:
        rs = by_policy[p]
        wins = sum(r["result"] == "win" for r in rs)
        assert wins == int(summary[p]["wins"])
        for key in ("accepted", "rejected", "wait_frames", "play_frames"):
            assert sum(r[key] for r in rs) == int(summary[p][key])
        latencies = np.array([v for r in rs for v in r["_latencies"]])
        assert np.isclose(np.percentile(latencies, 95), float(summary[p]["latency_p95_ms"]), atol=1e-10)
        wall = sum(r["wall_seconds"] for r in rs)
        policy_metrics.append({
            "policy": p, "games": len(rs), "wins": wins, "win_rate": wins/len(rs),
            "wilson_95": wilson(wins, len(rs)), "wait_frames": sum(r["wait_frames"] for r in rs),
            "play_frames": sum(r["play_frames"] for r in rs), "worker_seconds": wall,
            "games_per_worker_second": len(rs)/wall,
            "simulated_seconds_per_worker_second": sum(r["simulated_seconds"] for r in rs)/wall,
            "latency_p95_ms": float(np.percentile(latencies, 95)),
            "decision_latency_observations": len(latencies),
        })

    # Resample whole same-seed/side-swap pairs, rather than treating both sides as independent.
    rng = np.random.default_rng(20261014)
    bootstrap_indices = rng.integers(0, 50, size=(20000, 50))
    paired = []
    for comparison in report["paired_comparisons"]:
        base, other = comparison["base"], comparison["other"]
        scores = lambda p: np.array([1 if r["result"] == "win" else .5 if r["result"] == "draw" else 0 for r in by_policy[p]])
        differences = scores(other)-scores(base)
        clusters = differences.reshape(50, 2).mean(axis=1)
        draws = clusters[bootstrap_indices].mean(axis=1)
        paired.append({"base":base, "other":other, "paired_games":100, "side_swap_clusters":50,
                       "score_difference": float(differences.mean()),
                       "cluster_bootstrap_95": np.percentile(draws, [2.5, 97.5]).tolist(),
                       "other_better_games":int((differences > 0).sum()),
                       "base_better_games":int((differences < 0).sum()),
                       "equal_games":int((differences == 0).sum())})

    bc = read_json("outputs/training/bc/manifest.json")
    bc_curve = read_csv("outputs/training/bc/curve.csv")
    ppo = read_json("outputs/training/ppo/manifest.json")
    ppo_curve = read_csv("outputs/training/ppo/curve.csv")
    ppo_episodes = read_json("outputs/training/ppo/episodes.json")
    assert len(bc_curve) == 20 and len(ppo_curve) == 16
    assert int(ppo_curve[-1]["steps"]) == ppo["total_steps"] == 4096
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":10,
                        "axes.spines.top":False, "axes.spines.right":False,
                        "axes.titleweight":"bold", "axes.labelcolor":"#243649",
                        "text.color":"#243649", "axes.edgecolor":"#9da7b2",
                        "grid.color":"#e3e8ed", "grid.alpha":.9, "svg.fonttype":"none"})

    fig, ax = plt.subplots(figsize=(10, 5.4))
    y = np.arange(len(ORDER))
    rates = np.array([r["win_rate"] for r in policy_metrics])*100
    cis = np.array([r["wilson_95"] for r in policy_metrics])*100
    ax.barh(y, rates, color=COLORS, height=.6, alpha=.9)
    ax.errorbar(rates, y, xerr=np.maximum(0,np.vstack((rates-cis[:,0], cis[:,1]-rates))), fmt="none", ecolor="#25374a", capsize=4)
    for i, r in enumerate(policy_metrics):
        ax.text(max(rates[i]+11, cis[i,1]+2), i, f'{r["wins"]}/100  [{cis[i,0]:.1f}, {cis[i,1]:.1f}]', va="center", fontsize=9)
    ax.set(yticks=y, yticklabels=[LABELS[p] for p in ORDER], xlim=(0, 100), xlabel="Win rate (%)", title="Frozen evaluation: 100 games per policy")
    ax.invert_yaxis();ax.grid(axis="x");ax.set_axisbelow(True)
    fig.text(.12, .012, "Error bars: Wilson 95% intervals; descriptive, not official-game performance. Shared side-swap scenarios are correlated.", fontsize=8)
    fig.tight_layout(rect=(0,.05,1,1));save(fig,"wins")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    epochs = [int(r["epoch"])+1 for r in bc_curve]
    for key, label, color in [("validation_accuracy","Overall",GRAY),("wait_accuracy","WAIT",BLUE),("play_accuracy","PLAY",ORANGE)]:
        axes[0].plot(epochs, [float(r[key])*100 for r in bc_curve], label=label, color=color, marker="o", markersize=3)
    axes[0].set(title="Whole-episode validation", xlabel="Epoch", ylabel="Exact candidate accuracy (%)", ylim=(-3,105));axes[0].legend();axes[0].grid()
    final=bc["final"]
    accuracy=[final["validation_accuracy"],final["wait_accuracy"],final["play_accuracy"]]
    ns=[final["wait_frames"]+final["play_frames"],final["wait_frames"],final["play_frames"]]
    axes[1].bar(["Overall","WAIT","PLAY"],np.array(accuracy)*100,color=[GRAY,BLUE,ORANGE],width=.6)
    for i,(a,n) in enumerate(zip(accuracy,ns)):
        axes[1].text(i,a*100+2,f"{a*100:.1f}%\nn={n}",ha="center",va="bottom",fontsize=9)
    axes[1].set(title="Final validation: class imbalance",ylabel="Exact candidate accuracy (%)",ylim=(0,118));axes[1].grid(axis="y");axes[1].set_axisbelow(True)
    fig.text(.1,.012,"327 held-out decisions: 272 WAIT, 55 PLAY. Final PLAY matches: 16/55. Candidate accuracy does not measure game strength.",fontsize=8)
    fig.tight_layout(rect=(0,.05,1,1));save(fig,"BC_wait_play_accuracy")

    fig, axes = plt.subplots(2, 2, figsize=(10, 6.3), sharex=True)
    steps=[int(r["steps"]) for r in ppo_curve]
    for ax,key,label,color in zip(axes.flat,["mean_recent_return","value_loss","entropy","policy_loss"],
                                 ["Mean recent episode return","Value loss (logged)","Entropy (all training states)","Clipped policy loss"],
                                 [TEAL,ORANGE,BLUE,GRAY]):
        ax.plot(steps,[float(r[key]) for r in ppo_curve],color=color,marker="o",markersize=3)
        ax.set_title(label,fontsize=10);ax.grid();ax.set_xlabel("Environment steps")
    fig.suptitle("PPO CPU sanity run: 4,096 steps, one seed, 27 completed episodes",fontweight="bold")
    fig.text(.09,.012,"Training uses categorical sampling. Curves include forced-WAIT states; they do not establish convergence.",fontsize=8)
    fig.tight_layout(rect=(0,.05,1,.96));save(fig,"ppo_training_curves")

    fig, ax = plt.subplots(figsize=(10, 5.2))
    lat=np.array([r["latency_p95_ms"] for r in policy_metrics])
    ax.barh(y,lat,color=COLORS,height=.6,alpha=.9)
    for i,v in enumerate(lat):ax.text(v*1.13,i,f"{v:.3f} ms",va="center",fontsize=9)
    ax.axvline(15,color="#9b5350",linestyle="--",linewidth=1.2,label="Search soft budget: 15 ms")
    ax.set(yticks=y,yticklabels=[LABELS[p] for p in ORDER],xscale="log",xlim=(.008,60),xlabel="P95 decision latency (ms; log scale)",title="Measured decision latency across all evaluation frames")
    ax.invert_yaxis();ax.grid(axis="x",which="both");ax.set_axisbelow(True);ax.legend(loc="lower right",fontsize=9)
    fig.text(.12,.012,"P95 uses pooled per-decision measurements, including WAIT. Host: i9-14900HX; evaluation workers: 4; inference/training: CPU.",fontsize=8)
    fig.tight_layout(rect=(0,.05,1,1));save(fig,"latency_p95")

    dependencies=["matplotlib","numpy","contourpy","cycler","fonttools","kiwisolver","packaging","pillow","pyparsing","python-dateutil","six"]
    versions={name:importlib.metadata.version(name) for name in dependencies}
    (OUT/"requirements-plot.txt").write_text("\n".join(f"{k}=={v}" for k,v in versions.items())+"\n",encoding="utf-8")
    inputs=["outputs/evaluation/main/summary.csv","outputs/evaluation/main/report.json","outputs/evaluation/main/records.jsonl",
            "outputs/evaluation/main/scenarios.json","outputs/training/bc/manifest.json","outputs/training/bc/curve.csv",
            "outputs/training/ppo/manifest.json","outputs/training/ppo/curve.csv","outputs/training/ppo/episodes.json",
            "outputs/platform/environment.json","models/platform/learned.pt","models/platform/ppo.pt"]
    metric_report={"evaluation_games":700,"policy_metrics":policy_metrics,"paired_comparisons":paired,
                   "bootstrap":{"seed":20261014,"replicates":20000,"unit":"50 same-seed side-swap pairs", "adjustment":"none for multiple comparisons"},
                   "scenario_opponents":dict(Counter(r["opponent"] for r in scenarios)),
                   "scenario_own_decks":dict(Counter(r["own_deck"] for r in scenarios)),
                   "training_ppo":{"episodes":len(ppo_episodes),"accepted":sum(r["accepted"] for r in ppo_episodes),
                                   "wins":sum(r["result"]=="win" for r in ppo_episodes)},
                   "scope":"Frozen local approximate engine. Wilson intervals assume Bernoulli independence; paired bootstrap retains within-pair correlation only."}
    (OUT/"statistics.json").write_text(json.dumps(metric_report,ensure_ascii=False,indent=2),encoding="utf-8")
    manifest={"generator":"tools/plot_platform_experiments.py", "script_sha256":sha(Path(__file__)),
              "python":platform.python_version(),"matplotlib_backend":matplotlib.get_backend(),"versions":versions,
              "inputs":[{"path":p,"sha256":sha(ROOT/p)} for p in inputs],
              "outputs":[{"path":str(p.relative_to(ROOT)).replace("\\","/"),"sha256":sha(p)} for p in sorted(OUT.glob("*")) if p.suffix in (".png",".svg")],
              "validation":"700 unique policy/index records; 100 matched scenarios per policy; summary counts and pooled latency percentiles verified before plotting",
              "interval_reference":"https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm"}
    (OUT/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"outputs":len(manifest["outputs"]),"policies":policy_metrics,"paired":paired},indent=2))


if __name__ == "__main__":
    main()
