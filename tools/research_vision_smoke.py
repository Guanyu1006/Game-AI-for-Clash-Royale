"""Validate the pinned game ONNX interface on a zero tensor, not game accuracy."""
import ast
import hashlib
import json
from pathlib import Path
import sys
import time
import numpy as np
import onnx
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx"
COMMIT = "2f172925a15b17f973291f41a789b19a29f0fc43"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    source = ROOT / "third_party/ClashRoyaleAi/perception/clashroyalebuildabot"
    constant_file, units_file = source / "constants.py", source / "namespaces/units.py"
    constants = ast.parse(constant_file.read_text(encoding="utf-8"))
    unitnames = {}
    for node in ast.walk(ast.parse(units_file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "Unit" and call.args and isinstance(call.args[0], ast.Constant):
                unitnames[node.target.id] = call.args[0].value
    values = next(n.value for n in constants.body if isinstance(n, ast.Assign) and any(isinstance(t,ast.Name) and t.id=="DETECTOR_UNITS" for t in n.targets))
    labels = [{"class_index": i, "symbol": n.attr, "unit_name": unitnames.get(n.attr, "unknown")} for i,n in enumerate(values.elts)]
    label_record = {"repository": "itzik123/ClashRoyaleAi", "commit": COMMIT, "extraction": "read-only AST, source never imported",
                    "constants_url": f"https://raw.githubusercontent.com/itzik123/ClashRoyaleAi/{COMMIT}/perception/clashroyalebuildabot/constants.py",
                    "constants_sha256": digest(constant_file), "units_sha256": digest(units_file), "labels": labels,
                    "caveat": "source ordering is documented; zero input cannot prove correspondence to model training labels"}
    outfile = ROOT / "references/library/assets/unit_detector_labels.json"
    outfile.write_text(json.dumps(label_record, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {"model": MODEL.relative_to(ROOT).as_posix(), "sha256": digest(MODEL), "bytes": MODEL.stat().st_size,
              "onnx": onnx.__version__, "onnxruntime": ort.__version__, "numpy": np.__version__, "python": sys.version,
              "labels": outfile.relative_to(ROOT).as_posix(), "label_count": len(labels), "source_commit": COMMIT,
              "test": "checker + CPU zero tensor only", "real_game_accuracy_verified": False, "status": "pending"}
    try:
        graph = onnx.load(str(MODEL))
        onnx.checker.check_model(graph)
        record.update(ir_version=graph.ir_version, opsets=[{"domain":o.domain,"version":o.version} for o in graph.opset_import], checker="passed")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        start = time.perf_counter()
        session = ort.InferenceSession(str(MODEL), sess_options=options, providers=["CPUExecutionProvider"])
        record["load_ms"] = (time.perf_counter() - start) * 1000
        record["providers"] = session.get_providers()
        record["inputs"] = [{"name":a.name,"shape":a.shape,"type":a.type} for a in session.get_inputs()]
        record["outputs_declared"] = [{"name":a.name,"shape":a.shape,"type":a.type} for a in session.get_outputs()]
        record["custom_metadata"] = session.get_modelmeta().custom_metadata_map
        embedded_names = ast.literal_eval(record["custom_metadata"].get("names", "{}"))
        mismatches = [{"index":i,"source":label["unit_name"],"embedded":embedded_names.get(i)}
                      for i,label in enumerate(labels) if label["unit_name"] != embedded_names.get(i)]
        record["label_mapping_matches_embedded_metadata"] = not mismatches and len(embedded_names)==len(labels)
        record["label_mismatches"] = mismatches
        label_record.update(model_names=embedded_names, mapping_matches_embedded_metadata=record["label_mapping_matches_embedded_metadata"],
                            model_sha256=record["sha256"], model_license=record["custom_metadata"].get("license"))
        outfile.write_text(json.dumps(label_record, ensure_ascii=False, indent=2), encoding="utf-8")
        feed = {}
        for a in session.get_inputs():
            if not all(isinstance(d,int) and d>0 for d in a.shape):
                raise ValueError("Dynamic dimensions need an explicit documented test shape")
            dtype = {"tensor(float)":np.float32,"tensor(float16)":np.float16}.get(a.type)
            if dtype is None:
                raise ValueError("Unsupported smoke input dtype: " + a.type)
            feed[a.name] = np.zeros(a.shape, dtype=dtype)
        durations, outputs = [], None
        for _ in range(3):
            start=time.perf_counter()
            outputs=session.run(None, feed)
            durations.append((time.perf_counter()-start)*1000)
        record["inference_ms"] = durations
        record["zero_outputs"] = [{"shape":list(a.shape),"dtype":str(a.dtype),"finite":bool(np.all(np.isfinite(a))),
                                   "min":float(a.min()) if a.size else None,"max":float(a.max()) if a.size else None,
                                   "sha256":hashlib.sha256(a.tobytes()).hexdigest()} for a in outputs]
        if not all(x["finite"] for x in record["zero_outputs"]):
            raise ValueError("Nonfinite values in zero-input output")
        record["status"] = "passed_interface_smoke_only"
    except Exception as error:
        record.update(status="failed", error=repr(error))
    out = ROOT / "outputs/research/vision_model_smoke.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    assets_path = ROOT / "references/library/assets_manifest.json"
    if assets_path.exists():
        assets = json.loads(assets_path.read_text(encoding="utf-8"))
        for asset in assets["items"]:
            if asset.get("kind") == "game_unit_detector_onnx":
                asset.update(license=record.get("custom_metadata",{}).get("license",asset.get("license")),
                             execution_status=record["status"], smoke_report=out.relative_to(ROOT).as_posix())
        assets_path.write_text(json.dumps(assets, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0 if record["status"].startswith("passed") else 1

if __name__ == "__main__":
    raise SystemExit(main())
