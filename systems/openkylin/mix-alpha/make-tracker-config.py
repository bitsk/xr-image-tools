#!/usr/bin/env python3
from pathlib import Path
import re
import sys

source = Path('/usr/share/xr/a081/tracking/config6.xml').read_bytes()
# Keep the vendor XML (including comments and line endings) intact.
config, count = re.subn(rb'(<SFConfig>)(\r?\n)', rb'\1\2    <CvpConfig enable="false" />\2', source)
if count != 1:
    raise ValueError('Expected one SFConfig section in A081 tracking configuration')
Path(sys.argv[1]).write_bytes(config)
