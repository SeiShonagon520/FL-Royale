"""Dump a full apk csv_logic table as decompressed plaintext."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_apk_pve_tables import sc_compression  # noqa: E402

import zipfile  # noqa: E402

APK = Path(r"D:\github\FirstLight_CR\build\cr-ai-offline.apk")


def dump(name, limit=None):
    with zipfile.ZipFile(APK) as zf:
        hit = next(n for n in zf.namelist() if n.endswith("/" + name))
        raw = zf.read(hit)
    plain, _, _ = sc_compression.decompress(raw)
    text = plain.decode("utf-8-sig", "replace").rstrip("\x00")
    lines = text.splitlines()
    print(f"=== {name} ({len(lines)} lines) ===")
    for line in (lines[:limit] if limit else lines):
        print(line)
    if limit and len(lines) > limit:
        print(f"... ({len(lines) - limit} more)")


if __name__ == "__main__":
    dump(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None)
