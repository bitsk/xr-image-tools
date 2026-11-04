#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run UKUI XR as a scene in an already selected native OpenXR runtime.

This owns only the scene and its private desktop components. It never starts
a compositor runtime, hardware capture, radio, tracking producer or audio core.
"""
import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


STOP = False


def request_stop(_number, _frame):
    global STOP
    STOP = True


def atomic(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def birth(pid):
    try:
        return (Path('/proc') / str(pid) / 'stat').read_text().rsplit(') ', 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def command(argv, environment, timeout=3):
    result = subprocess.run(argv, env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + argv[0] + ': ' + result.stderr.strip())
    return result.stdout.strip()


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path('/usr'))
    parser.add_argument('--environment', type=Path)
    parser.add_argument('--state-directory', type=Path)
    parser.add_argument('--seconds', type=int, default=0)
    parser.add_argument('--debug-input', action='store_true')
    parser.add_argument('--validation', action='store_true')
    parser.add_argument('--no-xwayland', action='store_true')
    parser.add_argument('--no-ime', action='store_true')
    parser.add_argument('--ui-anchor', choices=('stage', 'head'), default='stage')
    parser.add_argument('--inside-private-bus', type=Path, help=argparse.SUPPRESS)
    opts = parser.parse_args()
    if opts.seconds < 0:
        parser.error('--seconds must be nonnegative')
    return opts


class Scene:
    def __init__(self, opts, private, run, original):
        self.opts, self.private, self.run, self.original = opts, private, run, original
        self.processes, self.logs, self.records = {}, {}, {}
        self.events = run / 'lifecycle.jsonl'
        self.result = {'schema': 1, 'kind': 'native-openxr-scene', 'ready': False,
                       'ui_anchor_mode': opts.ui_anchor,
                       'runtime_started': False, 'hardware_producer_started': False,
                       'audio_core_started': False, 'errors': [], 'children': {}}
        self.stopping = False

    def record(self, event, **values):
        row = {'event': event, 'monotonic_ns': time.monotonic_ns(), **values}
        line = json.dumps(row, ensure_ascii=False)
        with self.events.open('a') as stream:
            stream.write(line + '\n')
        print('XR_SHELL_SESSION ' + line, flush=True)

    def record_bus(self, stage, environment, bus_id=None, query=True):
        path = self.private / 'runtime/bus'
        row = {'stage': stage, 'address': environment.get('DBUS_SESSION_BUS_ADDRESS'),
               'xdg_runtime_dir': environment.get('XDG_RUNTIME_DIR'),
               'socket_path': str(path), 'socket_exists': False}
        try:
            info = path.lstat()
            row.update(socket_exists=True, socket_is_socket=stat.S_ISSOCK(info.st_mode), socket_uid=info.st_uid,
                       socket_inode=info.st_ino)
        except OSError as error:
            row['socket_error'] = str(error)
        pid_file = self.private / 'dbus-daemon.pid'
        try:
            pid = int(pid_file.read_text().strip())
            row.update(daemon_pid=pid, daemon_birth=birth(pid))
        except (OSError, ValueError) as error:
            row['daemon_identity_error'] = str(error)
        if query and bus_id is None:
            try:
                bus_id = command(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                                  '--object-path', '/org/freedesktop/DBus', '--method',
                                  'org.freedesktop.DBus.GetId'], environment)
            except Exception as error:
                row['get_id_error'] = str(error)
        row['get_id'] = bus_id
        self.result.setdefault('private_bus_observations', []).append(row)
        self.record('private-bus', **row)

    def start(self, role, argv, environment):
        log = (self.run / (role + '.log')).open('wb')
        self.logs[role] = log
        process = subprocess.Popen([str(p) for p in argv], env=environment,
                                   stdout=log, stderr=subprocess.STDOUT)
        self.processes[role] = process
        row = {'pid': process.pid, 'birth': birth(process.pid), 'argv': [str(p) for p in argv]}
        self.records[role] = row
        self.result['children'][role] = dict(row)
        atomic(self.run / 'children.json', self.records)
        self.record('child-start', role=role, **row)
        return process

    def wait_socket(self, path, role):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if STOP:
                raise InterruptedError('stop requested during startup')
            process = self.processes[role]
            if process.poll() is not None:
                raise RuntimeError(role + ' exited before socket readiness')
            if path.exists() and stat.S_ISSOCK(path.stat().st_mode):
                return
            time.sleep(.1)
        raise RuntimeError(role + ' socket readiness timed out')

    def component_environment(self):
        private = self.private
        for name in ('runtime', 'config', 'state', 'cache', 'data'):
            (private / name).mkdir(mode=0o700, exist_ok=name == 'runtime')
        # Keep approved WPS first-run settings across XR sessions without
        # changing the maintenance desktop's Office configuration.
        app_config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'xr-applications/Kingsoft'
        app_config.mkdir(mode=0o700, parents=True, exist_ok=True)
        (private / 'config/Kingsoft').symlink_to(app_config, target_is_directory=True)
        env = dict(os.environ)
        preferences = env.get('XR_SYSTEM_UI_PREFERENCES_FILE') or str(
            Path(env.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'xr-system-ui/preferences.json')
        env.update({
            'PATH': str(self.opts.prefix / 'bin') + ':' + env.get('PATH', '/usr/bin:/bin'),
            'DISPLAY': '', 'XAUTHORITY': '', 'SESSION_MANAGER': '', 'DESKTOP_STARTUP_ID': '',
            'XDG_RUNTIME_DIR': str(private / 'runtime'), 'XDG_CONFIG_HOME': str(private / 'config'),
            'XDG_STATE_HOME': str(private / 'state'), 'XDG_CACHE_HOME': str(private / 'cache'),
            'XDG_DATA_HOME': str(private / 'data'), 'XDG_CURRENT_DESKTOP': 'UKUI',
            'XDG_SESSION_TYPE': 'wayland', 'WAYLAND_DISPLAY': 'xr-wayland-0',
            'GDK_BACKEND': 'wayland', 'QT_QPA_PLATFORM': 'wayland', 'GTK_CSD': '0',
            'GTK_IM_MODULE': 'wayland', 'QT_IM_MODULE': '', 'XMODIFIERS': '',
            'PIPEWIRE_RUNTIME_DIR': str(self.original),
            'PIPEWIRE_REMOTE': os.environ.get('PIPEWIRE_REMOTE', 'pipewire-0'),
            'PULSE_SERVER': os.environ.get('PULSE_SERVER', 'unix:' + str(self.original / 'pulse/native')),
            'PULSE_RUNTIME_PATH': str(self.original / 'pulse'),
            'XR_SYSTEM_UI_ISOLATED_BUS': '1',
            'XR_SYSTEM_UI_PREFERENCES_FILE': preferences,
        })
        pulse = private / 'config/pulse'
        pulse.mkdir()
        (pulse / 'client.conf').write_text('autospawn = no\n')
        env['PULSE_CLIENTCONFIG'] = str(pulse / 'client.conf')
        # Only the private bus activation environment changes, never systemd --user.
        keys = ['DBUS_SESSION_BUS_ADDRESS', 'PATH', 'DISPLAY', 'XAUTHORITY', 'SESSION_MANAGER', 'DESKTOP_STARTUP_ID',
                'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME',
                'XDG_DATA_HOME', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE', 'WAYLAND_DISPLAY',
                'GDK_BACKEND', 'QT_QPA_PLATFORM', 'GTK_CSD', 'GTK_IM_MODULE', 'QT_IM_MODULE',
                'XMODIFIERS', 'PULSE_SERVER', 'PULSE_RUNTIME_PATH', 'PULSE_CLIENTCONFIG',
                'PIPEWIRE_RUNTIME_DIR', 'PIPEWIRE_REMOTE', 'XR_SYSTEM_UI_ISOLATED_BUS',
                'XR_SYSTEM_UI_PREFERENCES_FILE']
        self.record_bus('component-env-before', env)
        try:
            command(['dbus-update-activation-environment', *[key + '=' + env[key] for key in keys]], env)
        finally:
            self.record_bus('component-env-after', env)
        return env

    def start_ime(self, env):
        profile = self.private / 'config/fcitx5'
        profile.mkdir()
        (profile / 'profile').write_text(
            '[Groups/0]\nName=XR\nDefault Layout=us\nDefaultIM=pinyin\n'
            '[Groups/0/Items/0]\nName=keyboard-us\nLayout=\n'
            '[Groups/0/Items/1]\nName=pinyin\nLayout=\n[GroupOrder]\n0=XR\n')
        if not Path('/usr/share/fcitx5/inputmethod/pinyin.conf').is_file():
            raise RuntimeError('Fcitx pinyin input method is unavailable; use --no-ime explicitly if desired')
        self.start('ime', ['fcitx5', '--replace', '-D'], env)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if STOP:
                raise InterruptedError('stop requested during IME startup')
            if self.processes['ime'].poll() is not None:
                raise RuntimeError('Fcitx exited during startup')
            try:
                base = ['gdbus', 'call', '--session', '--dest', 'org.fcitx.Fcitx5',
                        '--object-path', '/controller', '--method']
                group = command(base + ['org.fcitx.Fcitx.Controller1.CurrentInputMethodGroup'], env, 1)
                info = command(base + ['org.fcitx.Fcitx.Controller1.InputMethodGroupInfo', 'XR'], env, 1)
                parsed = ast.literal_eval(info)
                if (ast.literal_eval(group) == ('XR',) and parsed[0] == 'us'
                        and {'keyboard-us', 'pinyin'} <= {item[0] for item in parsed[1]}):
                    (self.run / 'ime-group-info.txt').write_text(group + '\n' + info + '\n')
                    self.result['ime_group_verified'] = True
                    self.result['ime_preedit_verified'] = False
                    return
            except (RuntimeError, subprocess.TimeoutExpired, ValueError, SyntaxError):
                pass
            time.sleep(.1)
        raise RuntimeError('Fcitx did not load the actual XR input-method group')

    def wait_scenes(self, shell_socket, environment):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if STOP:
                raise InterruptedError('stop requested during scene startup')
            for role, process in self.processes.items():
                if process.poll() is not None:
                    raise RuntimeError(role + ' exited before scene readiness')
            try:
                snapshot = json.loads(command([str(self.opts.prefix / 'bin/xr-shellctl'),
                                              '--socket', str(shell_socket)], environment, 2))
                peers = snapshot.get('payload', {}).get('components', [])
                ready = {row['role'] for row in peers if row.get('node_count', 0) > 0}
                if {'dock', 'system_ui'} <= ready:
                    atomic(self.run / 'startup-snapshot.json', snapshot)
                    return
            except (RuntimeError, subprocess.TimeoutExpired, ValueError):
                pass
            time.sleep(.1)
        raise RuntimeError('Dock and System UI did not publish their actual Shell scenes')

    def execute(self):
        shell_socket = self.original / 'xr-desktop/shell.sock'
        if shell_socket.exists():
            raise RuntimeError('An existing Shell socket is present; its owner must stop first')
        runtime = os.environ.get('XR_RUNTIME_JSON')
        if not runtime or not Path(runtime).is_file():
            raise RuntimeError('Select the existing native OpenXR runtime with XR_RUNTIME_JSON')
        try:
            loader = ctypes.CDLL('libopenxr_loader.so.1')
            getattr(loader, 'xrGetInstanceProcAddr')
        except (OSError, AttributeError) as error:
            raise RuntimeError('Native OpenXR loader libopenxr_loader.so.1 is unavailable: '
                               + str(error) + '. Install the target system loader or provide '
                               'the verified native loader in the session LD_LIBRARY_PATH') from error
        self.result['openxr_loader_mappings'] = sorted({row.split()[-1] for row in
            Path('/proc/self/maps').read_text().splitlines() if 'libopenxr_loader' in row})
        for name in ('xr-shell', 'xr-shellctl', 'xr-window-server', 'xr-dock', 'xr-system-ui'):
            binary = self.opts.prefix / 'bin' / name
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise RuntimeError('Missing component: ' + str(binary))
        environment = self.opts.environment or self.opts.prefix / 'share/xr/environments/qingkong/environment.json'
        if not environment.is_file():
            raise RuntimeError('Missing environment descriptor: ' + str(environment))
        self.result['environment'] = {'path': str(environment),
                                     'sha256': hashlib.sha256(environment.read_bytes()).hexdigest()}
        captures = self.run / 'captures'
        captures.mkdir()
        component_env = self.component_environment()
        # Native runtime selection and the original XDG_RUNTIME_DIR are inherited
        # unchanged by Shell. It connects to the already running hardware runtime.
        shell_env = dict(os.environ)
        shell_env['XDG_RUNTIME_DIR'] = str(self.original)
        argv = [self.opts.prefix / 'bin/xr-shell', '--components', '--component-socket', shell_socket,
                '--environment', environment, '--capture-dir', captures,
                '--ui-anchor', self.opts.ui_anchor]
        if self.opts.debug_input:
            argv.append('--debug-input')
        if self.opts.validation:
            argv.append('--validation')
        self.start('shell', argv, shell_env)
        self.wait_socket(shell_socket, 'shell')
        self.result['shell_socket'] = str(shell_socket)
        argv = [self.opts.prefix / 'bin/xr-window-server', '--shell-socket', shell_socket,
                '--socket-name', 'xr-wayland-0']
        if not self.opts.no_xwayland:
            argv.append('--xwayland')
        self.start('window-server', argv, component_env)
        self.wait_socket(self.private / 'runtime/xr-wayland-0', 'window-server')
        if not self.opts.no_ime:
            self.start_ime(component_env)
        self.start('dock', [self.opts.prefix / 'bin/xr-dock', '--socket', shell_socket], component_env)
        self.start('system-ui', [self.opts.prefix / 'bin/xr-system-ui', 'run', '--socket', shell_socket,
                   '--state-file', self.run / 'system-ui-state.json', '--isolated-notifications'], component_env)
        self.wait_scenes(shell_socket, shell_env)
        self.result.update(ready=True, ever_ready=True, phase='running',
                           xr_runtime_json=runtime, component_runtime=str(self.private / 'runtime'),
                           pulse_server=component_env['PULSE_SERVER'],
                           children={role: dict(row) for role, row in self.records.items()})
        atomic(self.run / 'ready.json', self.result)
        self.record('ready', ready_file=str(self.run / 'ready.json'), shell_socket=str(shell_socket))
        deadline = time.monotonic() + self.opts.seconds if self.opts.seconds else None
        while not STOP and (deadline is None or time.monotonic() < deadline):
            if self.processes['shell'].poll() is not None:
                self.result['stop_reason'] = 'shell-exited'
                return
            for role, process in self.processes.items():
                if role == 'shell' or process.poll() is None:
                    continue
                # SessionEnding can let components finish just before Shell's
                # final GPU drain. Give the scene a bounded chance to finish.
                if process.returncode == 0:
                    limit = time.monotonic() + .5
                    while self.processes['shell'].poll() is None and time.monotonic() < limit:
                        time.sleep(.02)
                    if self.processes['shell'].poll() is not None:
                        self.result['stop_reason'] = 'shell-exited'
                        return
                raise RuntimeError(role + ' exited unexpectedly: ' + str(process.returncode))
            time.sleep(.1)
        self.result['stop_reason'] = 'signal' if STOP else 'duration'

    def stop(self):
        self.stopping = True
        self.result['ready'] = False
        self.result['phase'] = 'draining-shell'
        atomic(self.run / 'ready.json', self.result)
        shell = self.processes.get('shell')
        if shell is not None:
            if shell.poll() is None:
                shell.send_signal(signal.SIGTERM)
                self.record('shell-exit-requested', pid=shell.pid)
            try:
                shell.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.result['errors'].append('Shell did not finish its native XR/GPU shutdown')
                shell.kill()
                shell.wait(timeout=2)
            self.result['children']['shell']['exit'] = shell.returncode
            self.record('child-exit', role='shell', pid=shell.pid, status=shell.returncode)
            if shell.returncode:
                self.result['errors'].append('Shell exit ' + str(shell.returncode))
            summary = self.run / 'captures/summary.json'
            if summary.is_file():
                try:
                    self.result['shell_summary'] = json.loads(summary.read_text())
                    if not self.result['shell_summary'].get('cleanup', {}).get('resources_destroyed'):
                        self.result['errors'].append('Shell did not report GPU resource destruction')
                except (OSError, ValueError) as error:
                    self.result['errors'].append('Unreadable Shell summary: ' + str(error))
            elif self.result.get('ever_ready'):
                self.result['errors'].append('Shell summary is missing')
        # Only after Shell has ended its session and released its GPU do we
        # terminate remaining component children. Producer/runtime stay external.
        self.result['phase'] = 'draining-components'
        for role in ('system-ui', 'dock', 'ime', 'window-server'):
            process = self.processes.get(role)
            if process is not None and process.poll() is None:
                process.terminate()
                self.record('component-exit-requested', role=role, pid=process.pid)
        deadline = time.monotonic() + 4
        for role in ('system-ui', 'dock', 'ime', 'window-server'):
            process = self.processes.get(role)
            if process is None:
                continue
            try:
                process.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
                self.result['errors'].append(role + ' needed SIGKILL')
            self.result['children'][role]['exit'] = process.returncode
            self.record('child-exit', role=role, pid=process.pid, status=process.returncode)
            if process.returncode:
                self.result['errors'].append(role + ' exit ' + str(process.returncode))
        for log in self.logs.values():
            log.close()
        self.record_bus('exit', os.environ, query=False)
        self.result['phase'] = 'stopped'
        self.result['exit'] = 0 if not self.result['errors'] else 1
        atomic(self.run / 'ready.json', self.result)
        atomic(self.run / 'summary.json', self.result)
        self.record('scene-stopped', status=self.result['exit'])
        return self.result['exit']


def inside(opts):
    run = opts.inside_private_bus.resolve()
    launch = json.loads((run / 'launch.json').read_text())
    private, original = Path(launch['private']), Path(launch['original_runtime'])
    if private.is_symlink() or private.stat().st_uid != os.getuid():
        raise RuntimeError('Unexpected private component directory')
    atomic(private / 'helper.json', {'pid': os.getpid(), 'birth': birth(os.getpid())})
    bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')
    if not bus:
        raise RuntimeError('Private D-Bus address is missing')
    current_id = command(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                          '--object-path', '/org/freedesktop/DBus', '--method',
                          'org.freedesktop.DBus.GetId'], os.environ)
    if current_id == launch['original_bus_id']:
        raise RuntimeError('The component bus is not isolated')
    (private / 'session-bus-address').write_text(bus + '\n')
    scene = Scene(opts, private, run, original)
    scene.record_bus('inside-GetId', os.environ, bus_id=current_id)
    status = 1
    try:
        scene.execute()
    except InterruptedError as error:
        scene.result['stop_reason'] = str(error)
    except Exception as error:
        scene.result['errors'].append(str(error))
        scene.record('scene-error', error=str(error))
    finally:
        status = scene.stop()
    return status


def retire_inactive_desktop_status(run):
    """Release the UKUI singleton left on the inactive maintenance bus."""
    original_bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS', '').split(',guid=')[0]
    executable = Path('/usr/bin/kylin-status-manager')
    if not original_bus or not executable.is_file():
        return
    if subprocess.run(['pgrep', '-u', str(os.getuid()), '-x', 'kylin-wlcom'],
                      stdout=subprocess.DEVNULL).returncode == 0:
        return
    retired = []
    for root in Path('/proc').glob('[0-9]*'):
        try:
            if root.stat().st_uid != os.getuid() or (root / 'exe').resolve() != executable.resolve():
                continue
            env = dict(item.split('=', 1) for item in (root / 'environ').read_text().strip('\0').split('\0')
                       if '=' in item)
            if env.get('DBUS_SESSION_BUS_ADDRESS', '').split(',guid=')[0] != original_bus:
                continue
            pid = int(root.name)
            identity = birth(pid)
            if identity is None or birth(pid) != identity:
                continue
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while birth(pid) == identity and time.monotonic() < deadline:
                time.sleep(.05)
            retired.append({'pid': pid, 'birth': identity, 'gone': birth(pid) != identity})
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    atomic(run / 'inactive-desktop-status.json', {'retired': retired,
           'restore': 'The maintenance bus can activate its installed service after XR exits.'})


def outer(opts):
    if os.getuid() == 0:
        raise RuntimeError('Run the scene as the logged-in VR session user')
    original = Path(os.environ.get('XDG_RUNTIME_DIR', '/run/user/' + str(os.getuid()))).resolve()
    if not original.is_dir() or original.stat().st_uid != os.getuid():
        raise RuntimeError('An owned login runtime directory is required')
    state = opts.state_directory or Path.home() / '.local/state/xr-shell-session'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    run = state / ('run-' + uuid.uuid4().hex[:12])
    run.mkdir(mode=0o700)
    retire_inactive_desktop_status(run)
    (state / 'latest').write_text(str(run) + '\n')
    original_bus = ''
    if os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
        original_bus = command(['gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                                '--object-path', '/org/freedesktop/DBus', '--method',
                                'org.freedesktop.DBus.GetId'], os.environ)
    private = Path(tempfile.mkdtemp(prefix='xr-desktop-dev.', dir=original))
    os.chmod(private, 0o700)
    (private / 'runtime').mkdir(mode=0o700)
    bus_socket = private / 'runtime/bus'
    bus_wrapper = private / 'dbus-daemon-wrapper'
    bus_wrapper.write_text('#!/bin/sh\n'
        + 'printf \'%s\\n\' "$$" > ' + shlex.quote(str(private / 'dbus-daemon.pid')) + '\n'
        + 'exec /usr/bin/setpriv --inh-caps=-all --ambient-caps=-all /usr/bin/dbus-daemon --address=' + shlex.quote('unix:path=' + str(bus_socket))
        + ' "$@" 2>>' + shlex.quote(str(run / 'dbus.log')) + '\n')
    bus_wrapper.chmod(0o700)
    atomic(run / 'launch.json', {'schema': 1, 'private': str(private),
           'original_runtime': str(original), 'original_bus_id': original_bus,
           'private_bus_socket': str(bus_socket), 'private_bus_wrapper': str(bus_wrapper),
           'owner_pid': os.getpid(), 'owner_birth': birth(os.getpid()),
           'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
           'prefix': str(opts.prefix), 'xr_runtime_json': os.environ.get('XR_RUNTIME_JSON'),
           'native_environment': {key: os.environ[key] for key in (
               'XR_RUNTIME_JSON', 'VK_DRIVER_FILES', 'VK_ICD_FILENAMES', 'VK_LAYER_PATH',
               'VK_ADD_LAYER_PATH', 'VK_INSTANCE_LAYERS', 'LD_LIBRARY_PATH',
               'DISABLE_GAMESCOPE_WSI', 'DISABLE_VULKAN_FDM_INJECTION_LAYER') if key in os.environ},
           'environment': str(opts.environment) if opts.environment else None})
    argv = ['dbus-run-session', '--dbus-daemon=' + str(bus_wrapper), '--', sys.executable, str(Path(__file__).resolve()),
            '--inside-private-bus', str(run), *sys.argv[1:]]
    child = subprocess.Popen(argv, start_new_session=True)
    print('XR_SHELL_SESSION_RUN=' + str(run), flush=True)
    forwarded = False
    deadline = None
    stop_requested = None
    forced = False
    while child.poll() is None:
        if STOP:
            if deadline is None:
                stop_requested = time.monotonic()
                deadline = stop_requested + 30
            helper = private / 'helper.json'
            if not forwarded and helper.is_file():
                identity = json.loads(helper.read_text())
                if birth(identity['pid']) == identity['birth']:
                    try:
                        os.kill(identity['pid'], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    forwarded = True
            if time.monotonic() >= deadline:
                # This is a failed drain, never a successful scene exit. The
                # group was created by our unreaped child and contains no
                # external runtime or hardware producer.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                forced = True
                break
        time.sleep(.05)
    child.wait(timeout=2)
    cleanup = Path(__file__).resolve().parent / 'cleanup-private-activation.py'
    if not cleanup.is_file():
        # The source checkout retains one canonical shared helper.
        cleanup = Path(__file__).resolve().parent.parent / 'common/cleanup-private-activation.py'
    cleanup_status = 1
    try:
        with (run / 'private-activation-cleanup.json').open('w') as log:
            cleaned = subprocess.run([sys.executable, str(cleanup), '--private', str(private),
                                      '--runtime-root', str(original)], stdout=log, timeout=16)
        cleanup_status = cleaned.returncode
    except subprocess.TimeoutExpired:
        (run / 'private-cleanup-error.txt').write_text('Private activation cleanup exceeded 16 seconds\n')
    status = child.returncode or (1 if cleanup_status or forced else 0)
    if cleanup_status == 0:
        shutil.rmtree(private)
    atomic(run / 'session-result.json', {'schema': 1, 'exit': status, 'helper_bus_exit': child.returncode,
           'forced_group_stop': forced, 'private_cleanup_exit': cleanup_status,
           'stop_elapsed_seconds': time.monotonic() - stop_requested if stop_requested else None,
           'private_directory_removed': not private.exists(), 'external_runtime_stopped': False,
           'external_tracking_stopped': False})
    print('XR_SHELL_SESSION_EXIT=' + str(status), flush=True)
    return status


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    opts = arguments()
    opts.prefix = opts.prefix.resolve()
    if opts.environment:
        opts.environment = opts.environment.resolve()
    if opts.state_directory:
        opts.state_directory = opts.state_directory.resolve()
    try:
        return inside(opts) if opts.inside_private_bus else outer(opts)
    except Exception as error:
        print('XR_SHELL_SESSION_ERROR=' + str(error), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
