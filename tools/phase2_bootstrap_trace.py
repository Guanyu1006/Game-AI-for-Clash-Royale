"""Verify the exact static DEX call-site evidence for public bootstrap fields."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2/assets"


def main():
    dex_path = OUT / "dex_analysis.json"
    analysis = json.loads(dex_path.read_text(encoding="utf-8"))
    method = next(item for item in analysis["wildcat_and_direct_callers"] if item["class"] == "Lxyz/daniillnull/WildCat;" and item["name"] == "c")
    by_offset = {item["offset_bytes"]: item for item in method["instructions"]}
    expected = {0x0004: ('const-string', 'v0, "release"'),
                0x01a0: ('invoke-virtual', 'Landroid/content/Context;->getFilesDir()Ljava/io/File;'),
                0x01ca: ('invoke-virtual', 'Ljava/io/File;->getAbsolutePath()Ljava/lang/String;'),
                0x01d2: ('iget-object', 'v6, v4, Landroid/content/pm/PackageInfo;->packageName Ljava/lang/String;'),
                0x01d6: ('iget-object', 'v4, v4, Landroid/content/pm/PackageInfo;->versionName Ljava/lang/String;'),
                0x01da: ('invoke-static', 'v3, v6, v4, v0, Lxyz/daniillnull/WildCat;->meow')}
    evidence = []
    for offset, (mnemonic, fragment) in expected.items():
        instruction = by_offset[offset]
        if instruction["mnemonic"] != mnemonic or fragment not in instruction["output"]:
            raise ValueError("Pinned DEX call-site evidence changed")
        evidence.append({**instruction, "code_item_offset": method["code_offset"],
                         "dex_file_instruction_offset": method["code_offset"] + 16 + offset})
    package, version, channel = analysis["manifest"]["package"], analysis["manifest"]["versionName"], "release"
    records = {"status": "static_parameter_provenance_confirmed", "network_calls_by_this_tool": 0,
               "apk_executed": False, "native_executed": False, "remote_script_executed": False,
               "apk_sha256": analysis["apk_sha256"], "dex": analysis["dex"],
               "dex_analysis_sha256": hashlib.sha256(dex_path.read_bytes()).hexdigest(),
               "java_method": "xyz.daniillnull.WildCat.c()", "code_item_offset_hex": hex(method["code_offset"]),
               "java_meow_signature": "private static native String meow(String cacheDirectory, String packageName, String versionName, String releaseChannel)",
               "java_arguments": [{"index": 0, "register": "v3", "value": None, "expression": "new File(activity.getFilesDir(), 'wildcat').getAbsolutePath()", "scope": "local cache directory, not public URL argument"},
                                  {"index": 1, "register": "v6", "value": package, "expression": "PackageInfo.packageName", "scope": "public app identifier"},
                                  {"index": 2, "register": "v4", "value": version, "expression": "PackageInfo.versionName", "scope": "public APK version, not integer versionCode or content version"},
                                  {"index": 3, "register": "v0", "value": channel, "expression": "const-string release at +0x0004; normal load path", "scope": "release channel; not ABI or device ID"}],
               "public_url_arguments_in_order": [package, version, channel],
               "public_bootstrap_url": f"https://wildcat-api.dnull.xyz/v2/script/{package}/{version}/{channel}",
               "local_cache_key": f"wildcat4.1_{package}_{version}_{channel}",
               "native_url_mapping_corroborration": "root-agent ELF audit; see bootstrap_static.json and public_bootstrap_fetch.json",
               "java_request_semantics": "WildCat.request(String) parses local JSON type and dispatches registered handler; it is not Java HTTP fetch",
               "hostname_handler": {"class": "h0.c", "request_type": "getHostnamePreference", "returns": ["preferredHostname", "preferredPort"],
                                    "values": ["royale.server.dnull.xyz:9339", "nr.nullsproxy.com:9340"],
                                    "role": "game session host preferences, not identified asset CDN", "check_endpoint_called": False},
               "direct_asset_cdn_found_in_dex": False, "apk_base_content": analysis["apk_base_content_fingerprint"],
               "evidence": evidence}
    output = OUT / "dex_parameter_trace.json"
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: records[key] for key in ("status", "public_url_arguments_in_order", "public_bootstrap_url", "local_cache_key", "direct_asset_cdn_found_in_dex")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
