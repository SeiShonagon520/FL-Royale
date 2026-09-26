"""Pack the local game update for the first-launch installer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from build_config import ROOT, load


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    source = load()["runtime_update"]
    if not (source / "fingerprint.json").is_file():
        raise FileNotFoundError(f"Missing {source / 'fingerprint.json'}; see docs/build.md.")
    out = ROOT / "build/resource-pack"
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in source.rglob("*") if path.is_file())
    hashes = {path.relative_to(source).as_posix(): sha256(path) for path in files}
    bundle = out / "runtime_update.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with path.open("rb") as stream, archive.open(info, "w", force_zip64=True) as target:
                while block := stream.read(1024 * 1024):
                    target.write(block)
    manifest = {
        "schema": "firstlight-runtime-update-bundle.v1",
        "runtime_content_version": "15.535.86",
        "fingerprint_sha256": hashes["fingerprint.json"],
        "file_count": len(files),
        "uncompressed_bytes": sum(path.stat().st_size for path in files),
        "bundle_sha256": sha256(bundle),
        "files_sha256": hashes,
    }
    (out / "runtime_update.asset_manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"Packed {len(files)} resource files: {bundle}")


if __name__ == "__main__":
    main()
