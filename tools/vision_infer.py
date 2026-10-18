"""CPU inference on real frames using pinned upstream unit-detector preprocessing.

This is a bbox/class/confidence diagnostic, not the full PixelAdapter contract.
The immutable source ZIP is used for numerical parity checks; neither its imports
nor its side classifier, device actions or tile calibration are executed.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "2f172925a15b17f973291f41a789b19a29f0fc43"
MODEL = ROOT / "references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx"
MODEL_SHA256 = "4cf422370650b1d18647cffc05e02bf75fdc2fbdb1f8ef983f4b8d6f8ea908b3"
SOURCE_ZIP = ROOT / f"references/projects/itzik123__ClashRoyaleAi/source-{COMMIT}.zip"
SOURCE_SHA256 = "e6f5e6efc746e59573dde383cc303dfeda88a8777a8efa18dccdbb4dac7e4b28"
LABELS = ROOT / "references/library/assets/unit_detector_labels.json"
Y_START, Y_END, MIN_CONF = 0.05, 0.80, 0.3


def workspace_path(value: str | Path, must_exist: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    path.relative_to(ROOT.resolve())
    if must_exist and not path.is_file():
        raise FileNotFoundError(path)
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preprocess(image: Image.Image, model_height: int, model_width: int):
    # Keep floating crop coordinates, integer resize truncation, asymmetric odd
    # padding and float16 in-place division exactly as the pinned source does.
    roi = (0, Y_START * image.height, image.width, Y_END * image.height)
    cropped = image.crop(roi)
    ratio = cropped.height / cropped.width
    if ratio > model_height / model_width:
        height, width = model_height, int(model_height / ratio)
    else:
        width, height = model_width, int(model_width * ratio)
    resized = cropped.resize((width, height))
    array = np.array(resized, dtype=np.float16)
    dx, dy = model_width - width, model_height - height
    right, bottom = dx // 2, dy // 2
    left, top = dx - right, dy - bottom
    padding = [left, right, top, bottom]
    array = np.pad(array, ((top, bottom), (left, right), (0, 0)), mode="constant", constant_values=114)
    padded_rgb = np.array(array, dtype=np.uint8)
    array = array.transpose(2, 0, 1)
    array /= 255
    tensor = np.expand_dims(array, axis=0)
    details = {"crop_xyxy_float": list(roi), "cropped_size_wh": list(cropped.size),
               "resized_size_wh": [width, height], "padding_left_right_top_bottom": padding,
               "resize": "Pillow RGB default BICUBIC, matching source call without explicit resample",
               "normalization": "float16 RGB -> pad constant 114 -> CHW -> inplace /255 -> batch",
               "tensor_shape": list(tensor.shape), "tensor_dtype": str(tensor.dtype),
               "tensor_sha256": hashlib.sha256(tensor.tobytes()).hexdigest()}
    return tensor, padding, details, padded_rgb


def restore_boxes(pred: np.ndarray, width: int, height: int, model_height: int, model_width: int, padding: list[int]):
    boxes = pred.copy()
    boxes[:, [0, 2]] -= padding[0]
    boxes[:, [1, 3]] -= padding[2]
    boxes[..., [0, 2]] *= width / (model_width - padding[0] - padding[1])
    boxes[..., [1, 3]] *= height / (model_height - padding[2] - padding[3])
    boxes[:, [1, 3]] *= Y_END - Y_START
    boxes[:, [1, 3]] += Y_START * height
    return boxes


def source_parity(image, tensor, padding, pred, model_height, model_width):
    """Compare copied numerical transforms against only the fixed pure methods."""
    if sha256(SOURCE_ZIP) != SOURCE_SHA256:
        raise ValueError("Pinned immutable source ZIP SHA256 mismatch")
    prefix = f"ClashRoyaleAi-{COMMIT}/perception/clashroyalebuildabot/detectors/"
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        sources = {name: archive.read(prefix + name) for name in ("onnx_detector.py", "unit_detector.py")}
    methods = {}
    source_records = []
    for name, content in sources.items():
        source_records.append({"path": prefix + name, "sha256": hashlib.sha256(content).hexdigest(),
                               "url": f"https://github.com/itzik123/ClashRoyaleAi/blob/{COMMIT}/perception/clashroyalebuildabot/detectors/{name}"})
        tree = ast.parse(content.decode("utf-8"))
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        wanted = {"resize", "pad", "resize_pad_transpose_and_scale", "fix_bboxes"} if name == "onnx_detector.py" else {"_preprocess"}
        for node in klass.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted:
                scope = {"np": np}
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE_ZIP) + ":" + name, "exec"), scope)
                methods[node.name] = scope[node.name]
        if name == "unit_detector.py":
            constants = {node.targets[0].id: ast.literal_eval(node.value) for node in klass.body
                         if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
            if [constants[key] for key in ("UNIT_Y_START", "UNIT_Y_END", "MIN_CONF")] != [Y_START, Y_END, MIN_CONF]:
                raise ValueError("Pinned upstream crop/threshold constants changed")
            post = next(node for node in klass.body if isinstance(node, ast.FunctionDef) and node.name == "_post_process")
            # Only the first two numeric bbox statements. Exclude side inference,
            # tile mapping and class construction, all outside this diagnostic.
            post.body = post.body[:2] + [ast.Return(value=ast.Name(id="pred", ctx=ast.Load()))]
            scope = {"np": np}
            exec(compile(ast.fix_missing_locations(ast.Module(body=[post], type_ignores=[])), str(SOURCE_ZIP) + ":" + name, "exec"), scope)
            methods["_post_process"] = scope["_post_process"]
    methods.update(model_height=model_height, model_width=model_width, UNIT_Y_START=Y_START, UNIT_Y_END=Y_END)
    detector = type("PurePinnedNumericalMethods", (), methods)()
    expected, expected_padding = detector._preprocess(image)
    expected_boxes = detector.fix_bboxes(pred.copy(), image.width, image.height, padding)
    expected_boxes = detector._post_process(expected_boxes, image.height, image)
    actual_boxes = restore_boxes(pred, image.width, image.height, model_height, model_width, padding)
    result = {"tensor_exact_equal": bool(np.array_equal(expected, tensor)),
              "padding_exact_equal": expected_padding == padding,
              "bbox_exact_equal": bool(np.array_equal(expected_boxes, actual_boxes)),
              "source": source_records, "source_zip_sha256": sha256(SOURCE_ZIP),
              "executed_scope": "isolated pure resize/pad/normalization/inverse-bbox methods only"}
    if not all(result[key] for key in ("tensor_exact_equal", "padding_exact_equal", "bbox_exact_equal")):
        raise ValueError("Copied preprocessing or bbox recovery differs from pinned upstream methods")
    return result


def overlay(image, detections, roi, output, is_part2):
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    font = ImageFont.load_default(size=14)
    draw.rectangle(tuple(round(v) for v in roi), outline=(0, 180, 255), width=3)
    for detection in detections:
        bbox = detection["bbox_xyxy_visual_clipped"]
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            continue
        draw.rectangle(bbox, outline=(255, 80, 20), width=3)
        text = f"{detection['model_label']} {detection['confidence']:.3f}"
        box = draw.textbbox((bbox[0], max(0, bbox[1] - 18)), text, font=font)
        draw.rectangle(box, fill=(15, 15, 15))
        draw.text((bbox[0], max(0, bbox[1] - 18)), text, fill=(255, 255, 255), font=font)
    # Append a caption; source image coordinates above remain unchanged.
    canvas = Image.new("RGB", (image.width, image.height + 66), (18, 23, 32))
    canvas.paste(annotated, (0, 0))
    caption = ImageDraw.Draw(canvas)
    caption.text((8, image.height + 6), f"Strict upstream ROI (blue); detections > {MIN_CONF}: {len(detections)}", font=font, fill="white")
    domain_caption = "Real part2 arena crop; full-screen pipeline domain mismatch" if is_part2 else "Caller image; full-screen pipeline compatibility unverified"
    caption.text((8, image.height + 26), domain_caption, font=font, fill=(255, 190, 80))
    caption.text((8, image.height + 46), "No ground-truth match, faction, HUD, HP or closed-loop claim", font=font, fill="white")
    canvas.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Image file under the workspace")
    parser.add_argument("--output", required=True, help="Output directory under the workspace")
    args = parser.parse_args()
    output = workspace_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    record = {"status": "pending", "source_commit": COMMIT,
              "test_scope": "real-frame unit detector inference, strict pinned upstream transforms",
              "accuracy_evaluated": False, "end_to_end_pixel_loop_verified": False,
              "missing_fields": ["faction", "hand", "elixir", "tower_hp", "unit_hp", "stable_track_id", "calibrated_tile_coordinates"],
              "python": sys.version, "numpy": np.__version__, "onnxruntime": ort.__version__, "pillow": pillow_version}
    try:
        image_path = workspace_path(args.image, must_exist=True)
        if sha256(MODEL) != MODEL_SHA256:
            raise ValueError("Pinned model SHA256 mismatch")
        with Image.open(image_path) as opened:
            original_mode = opened.mode
            image = opened.convert("RGB")
        is_part2 = "/images/part2/" in image_path.as_posix()
        annotation_check = {"matched_split_list": None, "bbox_annotations_downloaded_for_this_image": False,
                            "metrics_computed": False}
        split_list = ROOT / "references/library/assets/wty-yy__Clash-Royale-Detection-Dataset/images/part2/val_annotation_mini.txt"
        if is_part2 and split_list.is_file():
            listed_paths = [line.strip().removeprefix("./") for line in split_list.read_text(encoding="utf-8").splitlines() if line.strip()]
            annotation_check.update(split_list=split_list.relative_to(ROOT).as_posix(),
                                    split_list_sha256=sha256(split_list), split_list_count=len(listed_paths),
                                    matched_split_list=image_path.relative_to(split_list.parent).as_posix() in listed_paths)
        record.update(image=image_path.relative_to(ROOT).as_posix(), image_sha256=sha256(image_path),
                      image_size_wh=list(image.size), image_original_mode=original_mode,
                      input_domain="dataset part2: pre-cropped arena" if is_part2 else "caller supplied, domain unverified",
                      domain_compatibility="mismatch: upstream crops 5%-80% of full screenshot; this sample is already an arena crop" if is_part2 else "unverified",
                      annotation_matching=annotation_check,
                      model=MODEL.relative_to(ROOT).as_posix(), model_sha256=MODEL_SHA256)
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        start = time.perf_counter()
        session = ort.InferenceSession(str(MODEL), sess_options=options, providers=["CPUExecutionProvider"])
        record["model_load_ms"] = (time.perf_counter() - start) * 1000
        model_input = session.get_inputs()[0]
        if model_input.type != "tensor(float16)" or model_input.shape != [1, 3, 480, 352]:
            raise ValueError("Unexpected pinned model interface")
        _, _, model_height, model_width = model_input.shape
        tensor, padding, details, rgb = preprocess(image, model_height, model_width)
        start = time.perf_counter()
        raw = session.run([session.get_outputs()[0].name], {model_input.name: tensor})[0]
        record["inference_ms"] = (time.perf_counter() - start) * 1000
        if raw.shape != (1, 300, 6) or not np.all(np.isfinite(raw)):
            raise ValueError("Unexpected or nonfinite output")
        predictions = raw[0]
        # Probe all rows for numerical inverse-transform parity, including low
        # confidence rows, so an empty filtered prediction isn't vacuous evidence.
        record["upstream_parity"] = source_parity(image, tensor, padding, predictions, model_height, model_width)
        filtered = predictions[predictions[:, 4] > MIN_CONF]
        boxes = restore_boxes(filtered, image.width, image.height, model_height, model_width, padding)
        names = ast.literal_eval(session.get_modelmeta().custom_metadata_map.get("names", "{}"))
        source_labels = json.loads(LABELS.read_text(encoding="utf-8"))["labels"]
        detections = []
        for row in boxes:
            class_index = int(row[5])
            bbox = [float(value) for value in row[:4]]
            rounded = [round(value) for value in bbox]
            clipped = [max(0, min(image.width - 1, rounded[0])), max(0, min(image.height - 1, rounded[1])),
                       max(0, min(image.width - 1, rounded[2])), max(0, min(image.height - 1, rounded[3]))]
            source_label = source_labels[class_index]["unit_name"] if 0 <= class_index < len(source_labels) else "unknown"
            detections.append({"class_index": class_index, "model_label": names.get(class_index, "unknown"),
                               "source_label": source_label, "label_mapping_agrees": names.get(class_index) == source_label,
                               "confidence": float(row[4]), "bbox_xyxy_float": bbox,
                               "bbox_xyxy_rounded_upstream": rounded, "bbox_xyxy_visual_clipped": clipped,
                               "faction": None, "hp": None, "track_id": None, "tile": None})
        Image.fromarray(rgb).save(output / "model_input.png")
        overlay(image, detections, details["crop_xyxy_float"], output / "detections.png", is_part2)
        record.update(status="passed_real_frame_inference_only", providers=session.get_providers(),
                      preprocessing=details, confidence_threshold=MIN_CONF, threshold_comparison=">",
                      output_shape=list(raw.shape), output_dtype=str(raw.dtype), raw_output_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                      raw_output_rows=predictions.tolist(), max_confidence=float(predictions[:, 4].max()),
                      detection_count=len(detections), detections=detections,
                      model_license=session.get_modelmeta().custom_metadata_map.get("license"),
                      artifacts={"overlay": (output / "detections.png").relative_to(ROOT).as_posix(),
                                 "model_input": (output / "model_input.png").relative_to(ROOT).as_posix()})
    except Exception as error:
        record.update(status="failed", error=repr(error))
    report = output / "detections.json"
    report.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: record.get(key) for key in ("status", "image", "image_size_wh", "detection_count", "max_confidence", "inference_ms", "domain_compatibility", "error")}, ensure_ascii=False, indent=2))
    print("Report:", report)
    return 0 if record["status"].startswith("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
