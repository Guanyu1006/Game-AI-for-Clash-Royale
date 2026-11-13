"""Fetch individually hash-locked CPU vision assets; no executable unpickling."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def fetch(destination):
    root = Path(__file__).resolve().parents[1]
    destination = Path(destination).resolve(); destination.relative_to(root)
    lock = json.loads(Path(__file__).with_suffix(".json").read_text())
    result = []
    for row in lock["files"]:
        path = (destination/row["relative_path"]).resolve(); path.relative_to(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            partial = path.with_suffix(path.suffix+".partial")
            if partial.exists():
                raise FileExistsError("Preserved partial download; inspect before explicitly retrying: "+str(partial))
            with urllib.request.urlopen(row["source"], timeout=60) as source, partial.open("xb") as target:
                count = 0
                while chunk := source.read(1024*1024):
                    count += len(chunk)
                    if count > row["bytes"]: raise ValueError("Download exceeds locked size")
                    target.write(chunk)
            if partial.stat().st_size != row["bytes"] or hashlib.sha256(partial.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError("Downloaded asset hash/size mismatch; partial preserved")
            partial.rename(path)
        if path.stat().st_size != row["bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("Existing asset differs; preserve it and choose a new destination")
        result.append({"path": str(path.relative_to(root)), "sha256": row["sha256"], "bytes": row["bytes"]})
    return {"schema": lock["schema"], "model_license": lock["unit_model_embedded_license"], "files": result}


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--destination", default=".cache/arena-lab/vision")
    args = p.parse_args(); print(json.dumps(fetch(args.destination), indent=2))


if __name__ == "__main__": main()
