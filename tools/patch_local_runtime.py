"""Add the verified APK-local Python runtime and native battle coordinator."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re

parser=argparse.ArgumentParser()
parser.add_argument("--decoded",type=Path,required=True)
parser.add_argument("--runtime-decoded",type=Path,required=True)
parser.add_argument("--receipt",type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
for path in (args.decoded,args.runtime_decoded,args.receipt):
    path.resolve().relative_to((root/"build").resolve())
installed=[]
for smali in args.runtime_decoded.glob("smali*"):
    selected=list((smali/"com/chaquo").rglob("*.smali")) if (smali/"com/chaquo").is_dir() else []
    selected+=list((smali/"com/firstlight/offline").glob("LocalBattleRuntime*.smali"))
    for source in selected:
        relative=source.relative_to(smali)
        target=args.decoded/"smali"/relative
        if any((directory/relative).exists() for directory in args.decoded.glob("smali*")):
            raise ValueError(f"Duplicate runtime class: {relative}")
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        installed.append(str(relative))
if not installed or not any("LocalBattleRuntime.smali" in name for name in installed):
    raise ValueError("Runtime classes absent")
files={}
sources=list((args.runtime_decoded/"assets/chaquopy").rglob("*"))
sources+=list((args.runtime_decoded/"lib/arm64-v8a").glob("*.so"))
for source in sources:
    if not source.is_file(): continue
    relative=source.relative_to(args.runtime_decoded)
    target=args.decoded/relative
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError(f"Conflicting library/asset: {relative}")
    else:
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    files[relative.as_posix()]=digest
game=args.decoded/"smali/com/supercell/clashroyale/GameApp.smali"
text=game.read_text()
anchor="    invoke-static {p0}, Lcom/firstlight/offline/GameOrtBootstrap;->start(Landroid/content/Context;)V"
if text.count(anchor)!=1: raise ValueError("ORT startup anchor changed")
text=text.replace(anchor,anchor+"\n\n    invoke-static {p0}, Lcom/firstlight/offline/LocalBattleRuntime;->start(Landroid/content/Context;)V")
game.write_text(text,encoding="utf-8")
activity=args.decoded/"smali/com/supercell/titan/GameApp.smali"
text=activity.read_text(encoding="utf-8")
anchor=".method public onBackPressed()V\n    .locals 1"
if text.count(anchor)!=1: raise ValueError("Back button anchor changed")
text=text.replace(anchor,anchor+'''\n
    invoke-static {}, Lcom/firstlight/offline/LocalBattleRuntime;->onBackPressed()Z
    move-result v0
    if-eqz v0, :firstlight_stock_back
    return-void
    :firstlight_stock_back''')
activity.write_text(text,encoding="utf-8")
manifest=args.decoded/"AndroidManifest.xml"
text=manifest.read_text(encoding="utf-8")
text,count=re.subn(r'\s*<uses-permission android:name="android.permission.INTERNET"\s*/>',"",text)
if count!=1: raise ValueError("Expected one original INTERNET permission")
text,count=re.subn(r'(<application\b[^>]*\bandroid:label=)"[^"]*"',r'\1"Firstlight Offline AI"',text)
if count!=1: raise ValueError("Application label anchor changed")
manifest.write_text(text,encoding="utf-8")
transport=args.decoded/"smali/p/m.smali"
text=transport.read_text(encoding="utf-8")
if '.class public final Lp/m;' not in text or 'NativeHTTPConnection' not in text or '.catch Ljava/io/IOException;' not in text:
    raise ValueError("Pinned native HTTP worker changed")
offline_run=''' .method public final run()V
    .locals 2
    const/4 v0, 0x4
    iput v0, p0, Lp/m;->j:I
    new-instance v0, Landroid/os/Message;
    invoke-direct {v0}, Landroid/os/Message;-><init>()V
    iput-object p0, v0, Landroid/os/Message;->obj:Ljava/lang/Object;
    iget-object v1, p0, Lp/m;->d:Landroid/os/Handler;
    invoke-virtual {v1, v0}, Landroid/os/Handler;->sendMessage(Landroid/os/Message;)Z
    return-void
.end method'''.lstrip()
text,count=re.subn(r'\.method public final run\(\)V.*?\.end method',offline_run,text,flags=re.S)
if count!=1: raise ValueError("HTTP run anchor changed")
transport.write_text(text,encoding="utf-8")
args.receipt.write_text(json.dumps({"schema":"firstlight.local-runtime-patch.v1","classes":len(installed),"files":files},indent=2),encoding="utf-8")
print(json.dumps({"classes":len(installed),"files":len(files)}))
