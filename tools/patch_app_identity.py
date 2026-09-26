"""Give the standalone app its own Android identity and the supplied icon."""
import argparse
import hashlib
from pathlib import Path
import json
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
import io
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OLD = "nullsroyale.rel.free"
PACKAGE = "com.firstlight.offline"
APP_LABEL = "FL Royale"
ICON = ROOT / "assets/branding/firstlight-icon.png"
ICON_SHA256 = hashlib.sha256(ICON.read_bytes()).hexdigest()
ANDROID = "{http://schemas.android.com/apk/res/android}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--decoded", type=Path)
    mode.add_argument("--verify-apk", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if args.verify_apk:
        with zipfile.ZipFile(args.verify_apk) as archive:
            entries = [n for n in archive.namelist() if re.fullmatch(r"res/drawable-nodpi(?:-v\d+)?/firstlight_launcher.png", n)]
            if len(entries) != 1:
                raise ValueError("Expected exactly one standalone launcher icon")
            packed = Image.open(io.BytesIO(archive.read(entries[0]))).convert("RGBA")
            source = Image.open(ICON).convert("RGBA")
            if packed.size != source.size or packed.tobytes() != source.tobytes():
                raise ValueError("Packed icon pixels differ from supplied image")
        print(json.dumps({"icon_pixels_verified": True, "source_sha256": ICON_SHA256}))
        return
    decoded = args.decoded.resolve()
    decoded.relative_to((ROOT / "build").resolve())
    if not args.receipt:
        raise ValueError("Patch receipt required")
    args.receipt.resolve().relative_to((ROOT / "build").resolve())
    manifest = decoded / "AndroidManifest.xml"
    text = manifest.read_text(encoding="utf-8")
    tree = ET.fromstring(text)
    if tree.get("package") != OLD or tree.find("application").get(ANDROID + "label") != "Firstlight Offline AI":
        raise ValueError("Expected the patched local app before identity isolation")
    # Class names/JNI entry points stay unchanged. Android package, provider
    # authorities and task affinities must all move together for coexistence.
    text = text.replace(OLD, PACKAGE)
    text, count = re.subn(r'(<application\b[^>]*\bandroid:label=)"[^"]*"',
                         lambda match: match.group(1) + '"' + APP_LABEL + '"', text)
    if count != 1:
        raise ValueError("Application label anchor changed")
    text, count = re.subn(r'(<application\b[^>]*\bandroid:icon=)"[^"]*"', r'\1"@drawable/firstlight_launcher"', text)
    if count != 1:
        raise ValueError("Application icon anchor changed")
    # The independent offline app must not compete for the original deep link.
    text = text.replace('android:scheme="nullsroyale"', 'android:scheme="firstlight-offline"')
    result = ET.fromstring(text)
    if (result.get("package") != PACKAGE or OLD in text
            or result.find("application").get(ANDROID + "label") != APP_LABEL):
        raise ValueError("Original application identity remains in manifest")
    icon = decoded / "res/drawable-nodpi/firstlight_launcher.png"
    icon.parent.mkdir(exist_ok=True)
    if icon.exists():
        raise ValueError("Standalone icon already exists")
    shutil.copyfile(ICON, icon)
    manifest.write_text(text, encoding="utf-8")
    receipt = {"schema": "firstlight-app-identity.v1", "package": PACKAGE, "label": APP_LABEL,
               "original_package_preserved": OLD, "icon_source": str(ICON), "icon_sha256": ICON_SHA256,
               "icon_resource": "@drawable/firstlight_launcher",
               "provider_authorities": [p.get(ANDROID + "authorities") for p in result.findall("application/provider")]}
    args.receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
