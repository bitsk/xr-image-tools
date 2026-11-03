#!/usr/bin/env python3
import argparse,hashlib,json,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);p.add_argument('--source',required=True,type=Path);p.add_argument('--sha256');a=p.parse_args()
if a.output.exists() or not a.source.is_file():p.error('Use an existing official base archive and a new output directory')
sidecar=a.source.with_name(a.source.name+'.sha256')
expected=a.sha256 or (sidecar.read_text().split()[0] if sidecar.exists() else None)
if not expected:p.error('Provide --sha256 or a source archive .sha256 sidecar')
h=hashlib.sha256()
with a.source.open('rb') as f:
 for b in iter(lambda:f.read(4194304),b''):h.update(b)
if h.hexdigest()!=expected:p.error('Base archive checksum mismatch')
a.output.mkdir();shutil.copyfile(a.source,a.output/'rootfs.tar')
(a.output/'prepare.json').write_text(json.dumps({'system':'openkylin','base_sha256':expected,'device_tested':False},indent=2)+'\n')
