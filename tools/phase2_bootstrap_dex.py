"""Read-only DEX disassembly for WildCat bootstrap parameter provenance.

No APK classes/native libraries/scripts are executed, and no app/network endpoint
is contacted. Androguard is used strictly as a local binary-format parser.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APK = ROOT / "third_party/phase2/firstlight/user-provided/nulls-royale.apk"
TARGET = "Lxyz/daniillnull/WildCat;"
RELATED = {"Lh0/c;", "Lxyz/daniillnull/connect/sdk/NTConnect;"}


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1048576), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def workspace(value):
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    path.relative_to(ROOT)
    return path


def method_record(method):
    code = method.get_code()
    instructions = []
    offset = 0
    for instruction in method.get_instructions():
        instructions.append({"offset_bytes": offset, "offset_code_units": offset // 2,
                             "mnemonic": instruction.get_name(), "output": instruction.get_output(),
                             "raw_hex": instruction.get_raw().hex()})
        offset += instruction.get_length()
    return {"class": method.get_class_name(), "name": method.get_name(), "descriptor": method.get_descriptor(),
            "access": method.get_access_flags_string(), "method_index": method.get_method_idx(),
            "code_offset": method.get_code_off(), "registers": code.get_registers_size() if code else None,
            "incoming_register_words": code.get_ins_size() if code else None, "instructions": instructions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default=str(DEFAULT_APK))
    parser.add_argument("--output", default="outputs/phase2/assets/dex_analysis.json")
    args = parser.parse_args()
    from loguru import logger
    logger.remove()
    from androguard.core.dex import DEX
    from androguard.core.axml import AXMLPrinter
    from lxml import etree
    import androguard
    apk, output = workspace(args.apk), workspace(args.output)
    records = []
    strings = set()
    dex_hashes = []
    with zipfile.ZipFile(apk) as archive:
        xml = etree.fromstring(AXMLPrinter(archive.read("AndroidManifest.xml")).get_xml())
        android = "{http://schemas.android.com/apk/res/android}"
        manifest = {"package": xml.get("package"), "versionCode": xml.get(android + "versionCode"),
                    "versionName": xml.get(android + "versionName"), "wildcat_metadata": []}
        for node in xml.iter("meta-data"):
            if "wildcat" in node.get(android + "name", "").lower():
                manifest["wildcat_metadata"].append({"name": node.get(android + "name"), "value": node.get(android + "value")})
        for name in archive.namelist():
            if not re.fullmatch(r"classes(?:\d+)?\.dex", name):
                continue
            blob = archive.read(name)
            dex_hashes.append({"name": name, "bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()})
            dex = DEX(blob)
            strings.update(str(value) for value in dex.get_strings())
            for klass in dex.get_classes():
                for method in klass.get_methods():
                    relevant = klass.get_name() == TARGET or klass.get_name() in RELATED
                    if not relevant:
                        relevant = any(TARGET in instruction.get_output() for instruction in method.get_instructions())
                    if relevant:
                        item = method_record(method)
                        item["dex"] = name
                        records.append(item)
        fingerprint_blob = archive.read("assets/fingerprint.json")
        fingerprint = json.loads(fingerprint_blob)
        fingerprint_summary = {"version": fingerprint.get("version"), "content_sha1_declared": fingerprint.get("sha"),
                               "file_count": len(fingerprint.get("files", [])), "fingerprint_json_sha256": hashlib.sha256(fingerprint_blob).hexdigest()}
    public_strings = sorted(value for value in strings if ("wildcat" in value.lower()
                            or (value.startswith(("https://", "http://")) and any(key in value.lower() for key in ("cdn", "asset", "fingerprint")))))
    report = {"scope": "read-only DEX/AXML parse; APK/native/script not executed; zero network calls",
              "apk": apk.relative_to(ROOT).as_posix(), "apk_bytes": apk.stat().st_size, "apk_sha256": digest(apk),
              "python": sys.version, "androguard": androguard.__version__, "manifest": manifest,
              "dex": dex_hashes, "apk_base_content_fingerprint": fingerprint_summary,
              "selected_public_strings": public_strings, "wildcat_and_direct_callers": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    text_output = output.with_suffix(".txt")
    sections = []
    for method in records:
        sections.append(f"{method['class']}->{method['name']}{method['descriptor']} [{method['access']}] code@{method['code_offset']:#x} registers={method['registers']} incoming={method['incoming_register_words']}")
        sections.extend(f"  {item['offset_bytes']:04x}  {item['mnemonic']:<24} {item['output']}" for item in method["instructions"])
        sections.append("")
    text_output.write_text("\n".join(sections), encoding="utf-8")
    print(json.dumps({"apk_sha256": report["apk_sha256"], "manifest": manifest, "dex": dex_hashes,
                      "selected_methods": len(records), "report": output.relative_to(ROOT).as_posix(),
                      "disassembly": text_output.relative_to(ROOT).as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
