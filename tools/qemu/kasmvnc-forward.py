#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add only the isolated KasmVNC NAT mapping to an already running VM."""
import argparse
import importlib.util
import ipaddress
from pathlib import Path
import sys

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--workspace', type=Path, required=True)
parser.add_argument('--listen', default='127.0.0.1', type=ipaddress.IPv4Address)
parser.add_argument('--port', type=int, default=8444)
parser.add_argument('--remove', action='store_true')
args = parser.parse_args()
if not 1 <= args.port <= 65535:
    parser.error('port must be between 1 and 65535')
spec = importlib.util.spec_from_file_location('xr_qemu', Path(__file__).parents[1] / 'qemu.py')
qemu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qemu)
action = 'hostfwd_remove' if args.remove else 'hostfwd_add'
endpoint = f'tcp:{args.listen}:{args.port}'
command = f'{action} n0 {endpoint}' + ('' if args.remove else '-:8444')
result = qemu.qmp(args.workspace, 'human-monitor-command', {'command-line': command})
if result.strip():
    sys.exit(result.strip())
print('KasmVNC mapping removed.' if args.remove else 'KasmVNC mapping added; repeat after a VM restart.')
