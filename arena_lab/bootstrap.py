"""Install the pinned simulator in a repository-local target, never the cloud venv."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "arena-lab"
TARGET = CACHE / "python"
PINNED = {"royalesim": "0.1.51", "royalegym": "0.1.29", "gymnasium": "1.4.0",
          "pettingzoo": "1.27.0", "msgspec": "0.22.0", "cloudpickle": "3.1.2",
          "farama-notifications": "0.0.6", "typing-extensions": "4.16.0"}
CRFORGE_COMMIT = "69277c1002499da7c23932c303c41ac00bd82eaa"
JDK_NAME = "jdk-21.0.13+9"
JDK_URL = ("https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.13%2B9/"
           "OpenJDK21U-jdk_x64_linux_hotspot_21.0.13_9.tar.gz")
JDK_SHA256 = "d7f5d5cf865a2902b70ff549b97586a7d3f234e016f041ab447ea740c7d66d1c"
CRFORGE_SHA256 = "8d4902400534abf85692a9f6f296c615f2f247d9942de31dfb3655c3839bee2c"


def activate() -> Path:
    """Prefer the isolated wheels and fail with an actionable command if absent."""
    if str(TARGET) not in sys.path:
        sys.path.insert(0, str(TARGET))
    distributions = {d.metadata["Name"].lower().replace("_", "-"): d.version
                     for d in importlib.metadata.distributions(path=[str(TARGET)])}
    missing = {k: v for k, v in PINNED.items() if distributions.get(k) != v}
    if missing:
        raise RuntimeError("Missing pinned arena simulator: " + repr(missing) +
                           "; run .venv-cloud/bin/python -m arena_lab.bootstrap install")
    return TARGET


def install() -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    wheels = CACHE / "wheels"
    wheels.mkdir(exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                    "--no-deps", "--dest", str(wheels), "--cache-dir", str(CACHE / "pip"),
                    "--require-hashes", "-r", str(ROOT / "requirements-arena-lab-lock.txt")], check=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
                    "--find-links", str(wheels), "--target", str(TARGET), "--upgrade",
                    "--require-hashes", "-r", str(ROOT / "requirements-arena-lab-lock.txt")], check=True)
    activate()
    receipt = {"pins": PINNED, "python": sys.version, "target": str(TARGET.relative_to(ROOT)),
               "wheels": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(wheels.glob("*.whl"))}}
    (CACHE / "installation.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def _download(name: str, url: str, sha256: str, limit: int) -> Path:
    destination = CACHE / "downloads" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        part = destination.with_suffix(destination.suffix + ".part")
        with urllib.request.urlopen(url, timeout=90) as source, part.open("wb") as output:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise RuntimeError(f"Download budget exceeded: {name}")
                output.write(chunk)
        part.replace(destination)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != sha256:
        raise RuntimeError(f"Downloaded archive hash mismatch: {name}")
    return destination


def install_crforge() -> dict:
    """Rebuild the pinned alternative, including data, with a local JDK and two CPUs."""
    install()  # shared Python dependencies; does not install into the host environment
    subprocess.run([sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--no-deps",
                    "--target", str(TARGET), "--upgrade", "--cache-dir", str(CACHE / "pip"),
                    "--require-hashes", "-r", str(ROOT / "requirements-arena-lab-crforge.txt")], check=True)
    archive = _download("jdk21.tar.gz", JDK_URL, JDK_SHA256, 250_000_000)
    java_root = CACHE / "jdk"
    if not (java_root / JDK_NAME).is_dir():
        java_root.mkdir(exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(java_root, filter="data")
    archive = _download("crforge.zip", "https://codeload.github.com/voonhous/crforge/zip/" +
                        CRFORGE_COMMIT, CRFORGE_SHA256, 100_000_000)
    source = CACHE / "crforge" / ("crforge-" + CRFORGE_COMMIT)
    if not source.is_dir():
        source.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as zipped:
            for entry in zipped.infolist():
                if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                    raise RuntimeError("Unsafe source archive member")
            zipped.extractall(source.parent)
    temporary = CACHE / "tmp"
    temporary.mkdir(exist_ok=True)
    env = dict(os.environ, JAVA_HOME=str(java_root / JDK_NAME),
               GRADLE_USER_HOME=str(CACHE / "gradle"), TMPDIR=str(temporary),
               JAVA_TOOL_OPTIONS=f"-XX:ActiveProcessorCount=2 -Xmx1024m -Djava.io.tmpdir={temporary}")
    log = CACHE / "crforge-build.log"
    with log.open("w") as output:
        subprocess.run(["bash", "gradlew", ":env:installDist", ":tables:dataVersionTables",
                        "-Pcrforge.assetSource=cdn", "-Pcrforge.cache=" + str(CACHE / "crforge-data"),
                        "--max-workers=2", "--no-daemon"], cwd=source, env=env,
                       stdout=output, stderr=subprocess.STDOUT, check=True)
    size = sum(p.stat().st_size for p in CACHE.rglob("*") if p.is_file())
    receipt = {"commit": CRFORGE_COMMIT, "jdk_sha256": JDK_SHA256,
               "source_sha256": CRFORGE_SHA256, "data_version": "16.635.8",
               "install": str((source / "env/build/install/env").relative_to(ROOT)),
               "tables": str((CACHE / "crforge-data/tables/16.635.8").relative_to(ROOT)),
               "cache_bytes": size, "build_log": str(log.relative_to(ROOT))}
    (CACHE / "installation-crforge.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if size > 2_000_000_000:
        raise RuntimeError("Arena cache exceeded its expected 2 GB footprint; inspect cache without deleting history")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "install-crforge", "check"], default="check", nargs="?")
    args = parser.parse_args()
    if args.command == "install":
        print(json.dumps(install(), indent=2))
    elif args.command == "install-crforge":
        print(json.dumps(install_crforge(), indent=2))
    else:
        activate()
        print(json.dumps({"target": str(TARGET), "pins": PINNED}, indent=2))


if __name__ == "__main__":
    main()
