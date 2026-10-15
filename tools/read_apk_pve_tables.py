"""Read Supercell-compressed csv_logic tables out of the base APK.

`sc_compression` fails to import on Windows hosts: its lzham support shim
imports `Writer` from `sc_compression.utils.writer`, but the package exports
`ByteWriter` from that module. Stubbing the shim lets the LZMA branch of the
decompressor load, which is what every csv_logic table actually uses.
"""
import io
import sys
import types
import zipfile
from pathlib import Path

if "sc_compression" not in sys.modules:
    _shim = types.ModuleType("sc_compression.support.lzham")

    class _LZHAM:  # pragma: no cover - never reached, the tables are LZMA
        pass

    _shim.LZHAM = _LZHAM
    sys.modules["sc_compression.support.lzham"] = _shim

import sc_compression  # noqa: E402

APK = Path(r"D:\github\FirstLight_CR\build\cr-ai-offline.apk")

WANTED = (
    "pve_waves.csv",
    "spawn_groups.toml",
    "npcs.csv",
    "tutorials_npc.csv",
    "pve_arena.csv",
    "tilemap_pve.csv",
    "card_delivery_battles.csv",
    "predefined_decks.csv",
)


def main():
    wanted = set(sys.argv[1:]) or set(WANTED)
    with zipfile.ZipFile(APK) as zf:
        names = zf.namelist()
        print(f"apk entries: {len(names)}")
        for want in sorted(wanted):
            hits = [n for n in names if n.endswith("/" + want) or n == want]
            if not hits:
                print(f"\n### {want}: NOT IN APK")
                continue
            for hit in hits:
                raw = zf.read(hit)
                print(f"\n### {hit}  ({len(raw)} bytes compressed)")
                try:
                    plain, sig, ver = sc_compression.decompress(raw)
                except Exception as error:
                    print(f"  decompress FAILED: {type(error).__name__}: {error}")
                    continue
                text = plain.decode("utf-8-sig", "replace").rstrip("\x00")
                print(f"  -> {len(plain)} bytes plaintext, signature={sig} version={ver}")
                lines = text.splitlines()
                print(f"  -> {len(lines)} lines")
                for line in lines[:6]:
                    print("  | " + line[:300])
                if len(lines) > 6:
                    print(f"  | ... ({len(lines) - 6} more lines)")


if __name__ == "__main__":
    main()
