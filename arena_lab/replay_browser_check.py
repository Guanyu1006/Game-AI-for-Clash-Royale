"""Optional real Chromium verification of a generated offline public-state replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import traceback


def check_replay(html: str | Path, output: str | Path, *, require_trained: bool = True) -> dict:
    root = Path(__file__).resolve().parents[1]
    html, output = Path(html).resolve(), Path(output).resolve()
    if not html.is_relative_to(root) or not output.is_relative_to(root):
        raise ValueError("Keep replay inputs and browser artifacts inside the repository")
    if output.exists():
        raise FileExistsError("Preserve previous browser-check artifacts")
    output.mkdir(parents=True)
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / ".cache/cloud/playwright"))
    library_root = root / ".cache/cloud/browser-libs/root"
    if library_root.is_dir():
        paths = [str(library_root / p) for p in ("usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu")]
        if os.environ.get("LD_LIBRARY_PATH"):
            paths.append(os.environ["LD_LIBRARY_PATH"])
        os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(paths)
    fonts = root / ".cache/cloud/browser-libs/fonts.conf"
    if fonts.is_file():
        os.environ.setdefault("FONTCONFIG_FILE", str(fonts))
    report = {"schema": "arena-public-replay-browser-check-v1", "status": "running",
              "html": str(html.relative_to(root)), "html_sha256": hashlib.sha256(html.read_bytes()).hexdigest(),
              "page_errors": [], "unexpected_network_requests": [], "checks": [],
              "scope": "Real offline Chromium controls and saved simulator-public observations; not official game pixels"}
    try:
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                report["browser_version"] = browser.version
                page = browser.new_page(viewport={"width": 1200, "height": 1100}, device_scale_factor=1)
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                def request_check(route):
                    if route.request.url.startswith(("http://", "https://")):
                        report["unexpected_network_requests"].append(route.request.url)
                        route.abort()
                    else:
                        route.continue_()
                page.route("**/*", request_check)
                page.goto(html.as_uri(), wait_until="load")
                payload = page.locator("#replay-data").evaluate("node => JSON.parse(node.textContent)")
                frames = payload["frames"]
                assert len(frames) > 2, "Three recorded frames required to verify controls"
                if require_trained:
                    assert payload["trained"] is True, "Expected a formally trained checkpoint replay"
                    expect(page.locator("#training")).to_contain_text("已完成训练")
                report["trained"] = payload["trained"]
                report["checkpoint_sha256"] = payload["checkpoint_sha256"]
                report["case_id"] = payload["episode"]["case_id"]
                report["frames"] = len(frames)
                expect(page.locator("#hand .card")).to_have_count(4)
                expect(page.locator("#timeline")).to_have_value("0")
                expect(page.locator("#sampling")).to_contain_text(str(payload["trace_every"]))
                expect(page.locator("#result")).to_contain_text(payload["episode"]["status"])
                report["checks"].append("trained_metadata_and_four_own_hand_slots")
                report["initial_clock"] = page.locator("#clock").inner_text()
                page.locator("#next").click()
                expect(page.locator("#timeline")).to_have_value("1")
                report["next_clock"] = page.locator("#clock").inner_text()
                page.locator("#back").click()
                expect(page.locator("#timeline")).to_have_value("0")
                report["checks"].append("next_and_previous_frame")
                middle = len(frames) // 2
                page.locator("#timeline").evaluate("(node,i)=>{node.value=String(i);node.dispatchEvent(new Event('input',{bubbles:true}));}", middle)
                expect(page.locator("#timeline")).to_have_value(str(middle))
                report["seek_clock"] = page.locator("#clock").inner_text()
                report["checks"].append("seek_to_fixed_middle_frame")
                page.locator("#speed").select_option("20")
                page.locator("#play").click()
                page.wait_for_function("i => Number(document.getElementById('timeline').value) > i", arg=middle, timeout=5000)
                page.locator("#play").click()
                expect(page.locator("#play")).to_have_text("播放")
                report["play_clock"] = page.locator("#clock").inner_text()
                report["checks"].append("twenty_times_playback_and_pause")
                # Reset screenshot selection to the same index on every run.
                page.locator("#timeline").evaluate("(node,i)=>{node.value=String(i);node.dispatchEvent(new Event('input',{bubbles:true}));}", middle)
                colors = page.locator("#arena").evaluate("canvas=>{const p=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;const c=new Set();for(let i=0;i<p.length;i+=400)c.add(p.slice(i,i+4).join(','));return c.size;}")
                assert colors > 8, "Battlefield canvas is blank or unexpectedly uniform"
                report["sampled_canvas_colors"] = colors
                fields = {name: index for index, name in enumerate(payload["entity_fields"])}
                rows = [dict(zip(sparse["indices"], sparse["values"])) for sparse in frames[middle]["visible_entities_sparse"]]
                has_no_hp = lambda row: row.get(fields.get("spell"), 0) > .5 or row.get(fields.get("projectile"), 0) > .5
                health_row = next(row for row in rows if not has_no_hp(row))
                canvas = page.locator("#arena")
                box = canvas.bounding_box()
                def click_entity(row):
                    x, y = row.get(fields["x"], 0), row.get(fields["y"], 0)
                    canvas.click(position={"x": (27 + x * 486) / 540 * box["width"],
                                           "y": (27 + (1 - y) * 846) / 900 * box["height"]})
                click_entity(health_row)
                expect(page.locator("#selected")).to_contain_text("坐标")
                expect(page.locator("#selected")).to_contain_text("血量比例")
                report["checks"].append("visible_entity_canvas_and_public_details")
                nonhealth_row = next((row for row in rows if has_no_hp(row)), None)
                if nonhealth_row:
                    click_entity(nonhealth_row)
                    expect(page.locator("#selected")).to_contain_text("血量：不适用")
                    report["checks"].append("spell_or_projectile_has_no_fabricated_health")
                    click_entity(health_row)
                page.screenshot(path=str(output / "replay.png"), full_page=True)
                report["screenshot"] = "replay.png"
                assert not report["page_errors"], "Browser emitted JavaScript errors"
                assert not report["unexpected_network_requests"], "Offline viewer attempted network requests"
                report["checks"].append("zero_javascript_errors_and_zero_network_requests")
                report["status"] = "passed"
            finally:
                browser.close()
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-untrained", action="store_true")
    args = parser.parse_args()
    result = check_replay(args.html, args.output, require_trained=not args.allow_untrained)
    print(json.dumps(result, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
