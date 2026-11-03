# openKylin x86_64 XR 开发虚拟机

复用既有 Debian QEMU 开发盘作为一次性装配辅助机，官方 openKylin 3.0 ISO 作为新系统输入。辅助盘使用 qcow2 写时复制；新开发盘独立保存，虚拟容量 64 GiB。所有服务与配置变更仅发生在来宾中，不访问 MIX，不更新实机验收状态。

## 已验证范围与限制

- KVM、Intel UHD 770、VirGL OpenGL 4.6、Venus Vulkan 1.4；1600×1000 UKUI X11 桌面及本地 VNC 像素传输。
- 私有 QEMU 11.1.3，开启 `honor-guest-pat=on`。发行版 QEMU 10.2.1 在 blob/hostmem 释放时出现 `cpr_delete_fd` 崩溃，保留系统程序并使用独立构建。
- Monado 模拟 HMD/双手柄、qwerty 键鼠设备及 Khronos `hello_xr -g Vulkan2`；**Venus 硬件渲染**通过，单眼 1280×1440 的颜色/深度交换链、可见双眼画面、FOCUSED、有界退出及 Khronos 验证层零 VUID 错误。lavapipe 软件模式单独回测通过，不自动回退。
- Venus 的共享图像使用 DRM modifier：分配端查询实际导入/导出能力，IPC 传递逐图像 modifier，客户端按同一 modifier 重建 OPAQUE_FD 图像。入口显式设置 `XRT_VK_DRM_MODIFIERS=1`；分配支持限制为单内存平面，验证范围为原生 `XR_KHR_vulkan_enable2` 客户端。GL/EGL 和旧 Vulkan 设备创建路径未验收。
- 模拟器帧记录与虚拟显示节奏不代表 MIX 的性能、真实跟踪或物理呈现。UKUI Wayland 会话未在本开发镜像验收。

## 软件源与 SDK

保留官方 ISO 的 build 主源、更新源与安全源，加入公开和 build 的 `huanghe-proposed main cross pty`，全部使用官方 keyring 校验。构建快照 3172 用于匹配 ISO 中较新的基础库。

当前源仍缺少 OpenXR 与 Vulkan 工具包，部分 X11 开发包与已安装库的精确版本不一致。`install-sdk.sh` 将这些官方开发包的头文件、链接入口及 pkg-config 文件解包到 `/opt/xr-dev/sdk`，以实际系统运行库链接，不使用 `--force-depends`、不降级 UKUI 库。包摘要保存在来宾 `/opt/xr-dev/sdk-debs.sha256`。OpenXR loader、Vulkan Tools、Monado 与 hello_xr 均在 openKylin 内原生构建，安装到 `/opt/xr-dev`。

开发终端显式加载：

```sh
source /opt/xr-dev/env.sh
```

SDK 不替换系统全局 OpenXR runtime。完整 native 包依赖安装与这套 SDK 环境应分别记录。

## 准备新开发盘

宿主需要 QEMU、qemu-img、OpenSSH、xorriso、Python 3；KVM 和渲染节点需对当前用户可访问。设备被执行沙箱隐藏时，应从实际宿主运行入口。不要以 sudo 启动虚拟机。

```sh
vm_dir=/work/openkylin-vm
tools_dir=/work/xr-image-tools
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" prepare \
  --iso /archives/openKylin-Desktop-V3.0-20260918-x86_64.iso \
  --helper /work/xr-desktop/vm
```

ISO 长度、官方 MD5 和本地固定 SHA-256 见 `systems/openkylin/qemu-source.lock.json`；squashfs 必须再匹配 ISO 内 SHA256SUMS。提取复用现有 openKylin adapter，保留其大文件长度修正与强制校验。辅助机格式化只接受明确指定的 64 GiB `/dev/vdb`；不操作宿主块设备。

装配由辅助机内独立 systemd unit 执行，读取 `$vm_dir/prepare-guest.log`，确认 `PREPARED` 后正常关闭辅助机，再启动开发 VM：

```sh
ssh -F /dev/null -i /work/xr-desktop/vm/id_ed25519 -p 22243 \
  -o UserKnownHostsFile="$vm_dir/known_hosts" root@127.0.0.1 \
  'grep -q "^PREPARED " /mnt/devshare/prepare-guest.log && sync && poweroff'
```

失败的装配不要直接重新格式化；`prepare-guest.sh SHARED_WORKSPACE /dev/vdb --resume` 只用于确认属于本任务的已挂载目标盘。装配实现选择保留现有镜像工具，不引入独立内核/rootfs 维护线。

准备只读源码共享目录：

```sh
mkdir -p "$vm_dir/source/monado"
git clone --depth 1 --branch release-1.1.54 https://github.com/KhronosGroup/OpenXR-SDK.git "$vm_dir/source/openxr-sdk"
git clone --depth 1 --branch release-1.1.54 https://github.com/KhronosGroup/OpenXR-SDK-Source.git "$vm_dir/source/openxr-sdk-source"
git clone --depth 1 --branch v1.4.341 https://github.com/KhronosGroup/Vulkan-Tools.git "$vm_dir/source/vulkan-tools"
git -C /work/xr-monado archive HEAD | tar -x -C "$vm_dir/source/monado"
```

Monado 输入需要包含可关闭的 `XRT_BUILD_DRIVER_A081` 选项；安装脚本关闭实际设备驱动及 SteamVR 插件，只保留模拟、qwerty 与 remote 驱动。记录输入提交和源码摘要，不把安装成功当作设备验收。

## 图形启动与连接

```sh
bash "$tools_dir/tools/qemu/build-host-qemu.sh" "$vm_dir"
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" run --graphics venus --rendernode /dev/dri/renderD128
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" status
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" stop
```

私有 QEMU 安装到 workspace，构建依赖仅从宿主软件源下载并在 workspace 解包；不安装宿主软件包。QEMU 归档长度/摘要及二进制摘要应与交付镜像一起记录。默认 6 vCPU、8 GiB RAM、4 GiB GPU hostmem。`--graphics virgl` 和 `--graphics software` 是显式选择，不会自动替换失败的 Venus 配置。

默认 VNC `127.0.0.1:5994`，SSH `127.0.0.1:22244`。SSH 使用 workspace 的独立密钥和 known_hosts，默认用户 `xr`，禁用密码登录。共享源码只读；没有宿主 GPU PCI 直通、USB 直通或头显维护通道。

局域网访问使用来宾 TigerVNC 分享实际 UKUI `:0`。先以桌面用户运行 `passwd` 和 `tigervncpasswd` 设置独立密码，将 `keep-awake.sh` 安装为 `/usr/local/bin/xr-dev-awake`，将 `configure-desktop.sh` 安装为 `/usr/local/bin/xr-configure-desktop` 并在该用户的桌面会话运行。该配置启用 `xr-desktop-vnc.service` 和防空闲服务，创建 OpenXR 桌面入口。

```sh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" run --graphics venus \
  --vnc-backend guest --vnc-listen HOST_LAN_IP --vnc-auth
```

管理器交互读取 8 字节 ASCII 的 QEMU 控制台密码，仅保存在进程内存；来宾 TigerVNC 密码由 `tigervncpasswd` 管理。主入口仍为 5994，原 QEMU 控制台移到回环 5995，SSH 继续只在回环监听。`access.json` 只保存地址、后端和认证开关，不保存密码；后续启动复用这些非敏感配置。认证测试覆盖错误密码拒绝和 ZRLE 8/16/32 bpp。RealVNC Viewer 可设置 `Quality=CUSTOM`、`ColorLevel=full`、`PreferredEncoding=ZRLE`，避免自动低色彩模式；桌面根窗口深度为 24 位。

可选的独立浏览器 GPU 桌面见 [KasmVNC 说明](../../docs/kasmvnc-qemu.md)。它使用独立用户和 `:1`，通过浏览器访问；3D 渲染加速与视频硬件编码分别验证。

`stop` 优先通过来宾 systemd 正常关机，避免 UKUI 消费 ACPI 电源键后只显示确认框。命令返回 `shutdown-requested` 表示已请求；用 `status` 确认 `stopped` 后才能离线操作磁盘。可选 `stop --wait-seconds 30`，超时不会强制退出 QEMU。原系统关机服务可能需要超过 30 秒。

## 原生构建与模拟会话

从宿主把 `install-sdk.sh` 送到来宾，以 root 在来宾运行；耗时构建使用来宾 systemd unit，避免 SSH 断开中断。完成时输出 `SDK_BUILD_COMPLETE`。

```sh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh --user root \
  'cat > /tmp/xr-sdk.sh; systemd-run --unit=xr-sdk-build --property=Type=exec /bin/bash -c "bash /tmp/xr-sdk.sh > /var/log/xr-sdk-build.log 2>&1"' \
  < "$tools_dir/tools/qemu/install-sdk.sh"

```

运行入口使用同版 `xr-image-builder` 包安装的 `tools/qemu` 与 `tools/common`，或完整版本目录。自 `0.1.6` 起不能只复制一个薄入口到 `/usr/local/bin`；包提供 `xr-simulator`、`xr-shell-simulator` 和 `xr-desktop-dev` 命令。更新使用完整版本目录加原子 symlink/rename，旧 Bash 进程保留原 inode。不要覆盖活动脚本；安装本身不会启动会话。

已验证的源码提交见 `systems/openkylin/qemu-components.lock.json`；更新本地源码后需要明确同步来宾源码和重建，不把旧构建目录当作新输入。

Vulkan 硬件验证：

```sh
source /opt/xr-dev/env.sh
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json vulkaninfo --summary
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json vkcubepp --wsi xcb --c 120
```

硬件模拟 XR 功能验证：

```sh
xr-simulator --renderer venus --scale 200 --seconds 40
xr-simulator --renderer venus --qwerty --scale 100 --seconds 20
```

`--scale 200` 在当前模拟 HMD 中对应单眼最大 1280×1440；默认 30 用于轻量开发。日志明确记录 renderer 与 runtime prefix。软件对照模式：

```sh
xr-simulator --renderer lavapipe --seconds 20
xr-simulator --renderer lavapipe --qwerty --seconds 20
```

交互使用需要终端输入：

```sh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh --tty \
  'xr-simulator --renderer venus --qwerty --scale 100'
```

qwerty 的键鼠操作说明在 Monado debug GUI 的 Help 中。不带 `--seconds` 时按终端任意键结束示例；入口清理自身服务和客户端。日志与原生 `metrics.pb` 位于用户 `.local/state/xr-simulator/`，`latest` 指向最近运行目录。`--runtime-prefix DIR` 可选择独立构建或回退版本；当前 VM 的原软件运行时及摘要保留在 `/opt/xr-dev/rollback/monado-8d4e76`。IPC 布局有变化，服务与客户端必须来自同一次构建。

滑块调试使用 `xr-simulator --sliders --scale 100`，或桌面上的 **OpenXR Debug**。面板的 Head / view、Left controller、Right controller 分别提供 XYZ 位置和 Yaw/Pitch/Roll 角度；手柄同时提供握力、扳机、摇杆和按键。可以拖动滑块，或 Ctrl+点击输入数值，Reset pose / Reset all devices 恢复初值。握力 `Squeeze Force` 对应 hello_xr 示例的抓握动作。关闭控制面板会结束本次示例并清理其 Monado 服务。

同机的第二个独立桌面用户可使用 `--remote-port 4243`，保留默认会话的 4242；IPC、配置和日志按用户隔离。`configure-kasm-xr.sh` 应在 Kasm 桌面账户内运行，创建使用第二端口的入口。远程输入端口不加入宿主转发。

Kasm 桌面的入口同时使用 `--wsi-copy`：仅给 Monado service 设置 `MESA_VK_WSI_DEBUG=sw,linear`，修复 Venus Vulkan 的原生 WSI 在该 X server 中黑屏的问题。应用与合成器仍选择 Venus GPU，最后的虚拟显示传输使用 CPU 拷贝；原 `:0` 会话保留原生 WSI 路径。浏览器使用 `?resize=scale` 可以保持 1600×1000 调试桌面的布局。

## Rust Shell 与自定义客户端

`run-simulator.sh --client PATH --client-arg ARG ...` 保留既有 Monado、renderer 与滑块配置，仅替换默认的 `hello_xr -g Vulkan2 -v`。参数以 argv 数组传递，不经过 shell 求值。自定义客户端必须处理 SIGTERM；`--seconds` 到期或关闭滑块时，入口先通知客户端，等待其 OpenXR/GPU 清理，再停止本次启动的 GUI 和 Monado。15 秒仍未退出会记录 `XR_CLIENT_GRACE_TIMEOUT`，终止自己的客户端并报告失败，不能视为优雅清理通过。

`run-shell.sh` 是薄 profile，可在原位置通过 `bash` 运行，或安装为 `xr-shell-simulator`。它默认启动现有视角/手柄滑块，并将 `--environment`、`--capture-dir`、`--validation` 传入 Rust Shell。默认 compositor scale 为 100，可显式调整；`DISPLAY=:1` 默认使用端口 4243 和 WSI CPU copy，其他显示使用 4242。需要显式指定未加入 PATH 的 Shell 二进制：

```sh
xr-shell-simulator --shell-binary /opt/xr-shell/bin/xr-shell \
  --environment /usr/share/xr/environments/qingkong/environment.json \
  --capture-dir "$HOME/.local/state/xr-shell/captures" --validation --seconds 30

# 原位置运行源码脚本时可明确选择模拟器入口。
bash tools/qemu/run-shell.sh --simulator /usr/local/bin/xr-simulator \
  --shell-binary /opt/xr-shell/bin/xr-shell --seconds 30
```

扩展的 Rust CLI 参数可以用重复的 `--shell-arg ARG` 原样传递。`--seconds` 由模拟会话计时，退出通过 SIGTERM 进入 Rust 的正常会话结束流程，不要求客户端读取 stdin。

参数化姿态验收可使用 `xr-shell-simulator --headless-input`：它选择 `xr-simulator --remote`，只启动 remote 模拟设备，不启动或操作 GUI。姿态由对应固定版本的 Monado remote 客户端直接输入；`--seconds`、客户端退出和本会话 runtime 清理仍采用同一条路径。默认交互入口继续使用 `--sliders`；日志中的 `XR_CONTROLS_GUI=false` 标明无 GUI 模式。

入口记录每个直接子进程的 PID 和 `/proc` 启动标识，只向身份仍匹配的本次子进程发信号；不使用全局 `pkill`。当前用户 runtime 锁与已有 IPC socket 检查阻止覆盖活动会话。客户端、runtime、GUI 均不继承锁 FD；计时器是可独立回收的直接子进程。

日志目录中，默认示例写 `hello-xr.log`，自定义客户端写 `client.log`。`lifecycle.log` 记录客户端 argv、PID/启动标识、renderer、runtime JSON、ICD、WSI 路径、停止原因及各进程退出状态。最后的 `XR_SESSION_EXIT=0` 表示启动入口正常结束；仍需结合客户端的资源清理记录、实际 socket/process 状态以及画面证据判断验收。

开发桌面通过 GSettings 关闭自动休眠、变暗和屏保，X11 屏保 timeout 为 0、DPMS 关闭；`xr-dev-awake --hold` 持续持有 UKUI 标准 idle/suspend inhibitor，处理当前会话缓存的空闲定时器。原桌面由用户服务启动，独立 Kasm 会话由用户自启动项启动。停止防空闲服务或移除对应自启动项可以撤销持续抑制；持久化设置需按需恢复。

## 多组件开发桌面

`run-desktop.sh` 仅在带 `openkylin-qemu-amd64` 标记的开发 VM 中运行。它组合已有 `run-shell.sh`/`xr-simulator`，保留其 Monado/客户端生命周期；这是开发 profile，不是替代生产 `xr-session-manager` 的新接口。按同目录临时文件加 rename 原子安装入口，或直接运行完整脚本目录。

```sh
XR_DESKTOP_PREFIX=/opt/xr-desktop-candidate \
  bash tools/qemu/run-desktop.sh --seconds 120 \
  --capture-dir "$HOME/.local/state/xr-desktop-captures" --validation --debug-input
```

`XR_DESKTOP_PREFIX` 默认 `/usr`，候选目录应包含 `bin/xr-shell`、`xr-window-server`、`xr-dock` 和 `xr-system-ui`。`--simulator PATH` 可以选择本次版本的模拟器；环境、renderer、Monado prefix、remote port 与 WSI 开关继续由既有 profile 处理。`--xwayland` 显式请求窗口服务的可选 Xwayland 支持；`--no-ime`、`--no-audio` 仅用于未启用对应能力的阶段，不表示这些能力已验收。

Shell 与 Monado 保持启动者原来的 `XDG_RUNTIME_DIR` 和显示输出。其组件 socket 始终为原 runtime 下的 `xr-desktop/shell.sock`，由各组件显式指定。profile 用 `dbus-run-session` 新建总线，并在原 runtime 的唯一 `xr-desktop-dev.*` 子目录内分配 0700 的组件 runtime/config/state/cache；Wayland display 固定为私有的 `xr-wayland-0`。启动任何组件前先比较总线 GetId，再用 `dbus-update-activation-environment` 仅更新这份新总线的服务激活环境（不使用 `--systemd`）：DISPLAY、旧 X authority/session 为空，Wayland、XDG、音频路径全部指向私有环境。直接进程与 D-Bus 自动激活服务共用同一份环境，Shell/Monado 的显示和 runtime 不变。`XR_SYSTEM_UI_ISOLATED_BUS=1` 与 System UI 的 `--isolated-notifications` 只在这份新总线中启用。

GTK 默认 `GTK_IM_MODULE=wayland`，通过真实的 Wayland text-input/input-method 链路接入预编辑与提交；可用 `XR_DESKTOP_GTK_IM_MODULE` 显式选择其他模块，但不能以 Fcitx 的 D-Bus IM 模块结果充当 Wayland preedit 验收。Qt 默认清空 `QT_IM_MODULE` 由 Wayland 平台选择，可用 `XR_DESKTOP_QT_IM_MODULE` 覆盖。Pulse 客户端使用私有配置并禁用 autospawn，避免 `--no-audio` 的查询动作隐式启动音频 daemon。`--no-ime` 也要求探测端不自动激活输入法服务；能力是否可用以本次实际进程和端点为准。

启动顺序为 Shell/socket → 私有 Wayland/socket → Fcitx/服务名 → 私有 PipeWire、Pulse、WirePlumber 与显式 `xr_desktop_null` sink → Dock → System UI。所有 socket 查询和 pactl 均使用私有 runtime/明确的 server；没有原用户音频 socket 的硬编码。无声卡也可以验证音量、静音与路由控制，但 null sink 不代表物理音频输出通过。若已安装 Fcitx 拼音方法，profile 在私有配置中选择该方法；预编辑、候选和提交仍需真实应用验收。

私有音频在 core 启动前通过 `pipewire.conf.d` 的 `context.objects` 配置 `adapter`/`support.null-audio-sink`，不依赖 Pulse 动态加载模块。必须实际列出 `xr_desktop_null` 并将默认 sink 读回为该名字后才记录就绪；失败会结束 profile 并按原有路径清理。`pipewire-null-sink.conf`、`audio-sinks.log` 和 `audio-default-sink.log` 保存运行证据。

profile 记录每个自己启动进程的 PID/启动标识。SIGTERM 先通知本次 Shell；随后终止并等待自己的组件和 daemon，最后等待既有模拟器完成客户端/Monado回收。超时只向身份仍匹配的自有 PID 发信号；不调用全局 pkill 或删除原 runtime。外层等待私有 D-Bus 会话退出后删除本次临时子目录。日志和抓帧保留在用户 state/capture 目录，`xr-desktop-dev/latest` 指向最近日志；异常退出状态应结合各 child exit 与实际端点/进程结果判断，不能仅靠 READY 判断整套能力通过。

验收使用的 Khronos Vulkan validation layer 由官方 Ubuntu `vulkan-validationlayers` 包隔离解包到 `/opt/xr-validation`，未安装进来宾 dpkg；它仅用于诊断。Vulkan loader 的实际 instance/device layer 插入日志与零 VUID 计数一起保存。原生 Monado metrics 中应用帧、合成帧、GPU 时间分别记录，不将合成节奏或 GPU 时间等同物理帧率、延迟或 MIX 性能。

`screenshot --output /work/desktop.png` 优先使用 QMP；GL 扫描输出没有 CPU surface 时改用来宾的 scrot。验证截图与生成概念图应分开保存。

QEMU运行入口现在调用同仓 `../common` 中唯一的生命周期实现；仍检查原有 VM 标记。部署时保留 `tools/qemu` 与 `tools/common` 的完整相对布局，不能只复制单个薄脚本。原生 ARM64 开发必须通过独立 `tools/native` 入口并显式 opt-in，不写入或伪造 VM 标记。
