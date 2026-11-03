# openKylin x86_64 XR 开发虚拟机

复用既有 Debian QEMU 开发盘作为一次性装配辅助机，官方 openKylin 3.0 ISO 作为新系统输入。辅助盘使用 qcow2 写时复制；新开发盘独立保存，虚拟容量 64 GiB。所有服务与配置变更仅发生在来宾中，不访问 MIX，不更新实机验收状态。

## 已验证范围与限制

- KVM、Intel UHD 770、VirGL OpenGL 4.6、Venus Vulkan 1.4；1600×1000 UKUI X11 桌面及本地 VNC 像素传输。
- 私有 QEMU 11.1.3，开启 `honor-guest-pat=on`。发行版 QEMU 10.2.1 在 blob/hostmem 释放时出现 `cpr_delete_fd` 崩溃，保留系统程序并使用独立构建。
- Monado 模拟 HMD/双手柄、qwerty 键鼠设备及 Khronos `hello_xr -g Vulkan2`；通过的模拟会话明确使用 **lavapipe 软件渲染**。默认不自动从 Venus 回退。
- **Venus 直接运行 Monado 尚未通过**：外部图像的 optimal tiling 查询返回 `VK_ERROR_FORMAT_NOT_SUPPORTED`，Monado 没有可用交换链格式。Mesa Venus 的此路径要求 DRM format modifier；扩展枚举成功不能代替图像共享验收。
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

VNC `127.0.0.1:5994`，SSH `127.0.0.1:22244`；端口只监听回环。SSH 使用 workspace 的独立密钥和 known_hosts，默认用户 `xr`。这是空密码自动登录的本地开发账户，SSH 禁用密码登录。共享源码只读；没有宿主 GPU PCI 直通、USB 直通或头显维护通道。

`stop` 优先通过来宾 systemd 正常关机，避免 UKUI 消费 ACPI 电源键后只显示确认框。命令返回 `shutdown-requested` 表示已请求；用 `status` 确认 `stopped` 后才能离线操作磁盘。可选 `stop --wait-seconds 30`，超时不会强制退出 QEMU。原系统关机服务可能需要超过 30 秒。

## 原生构建与模拟会话

从宿主把 `install-sdk.sh` 送到来宾，以 root 在来宾运行；耗时构建使用来宾 systemd unit，避免 SSH 断开中断。完成时输出 `SDK_BUILD_COMPLETE`。

```sh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh --user root \
  'cat > /tmp/xr-sdk.sh; systemd-run --unit=xr-sdk-build --property=Type=exec /bin/bash -c "bash /tmp/xr-sdk.sh > /var/log/xr-sdk-build.log 2>&1"' \
  < "$tools_dir/tools/qemu/install-sdk.sh"
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh --user root \
  'cat > /usr/local/bin/xr-simulator && chmod 755 /usr/local/bin/xr-simulator' \
  < "$tools_dir/tools/qemu/run-simulator.sh"
```

已验证的源码提交见 `systems/openkylin/qemu-components.lock.json`；更新本地源码后需要明确同步来宾源码和重建，不把旧构建目录当作新输入。

Vulkan 硬件验证：

```sh
source /opt/xr-dev/env.sh
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json vulkaninfo --summary
VK_DRIVER_FILES=/usr/share/vulkan/icd.d/virtio_icd.json vkcubepp --wsi xcb --c 120
```

模拟 XR 功能验证，必须明确软件模式：

```sh
xr-simulator --renderer lavapipe --seconds 20
xr-simulator --renderer lavapipe --qwerty --seconds 20
```

交互使用需要终端输入：

```sh
python3 "$tools_dir/tools/qemu.py" --workspace "$vm_dir" ssh --tty \
  'xr-simulator --renderer lavapipe --qwerty'
```

qwerty 的键鼠操作说明在 Monado debug GUI 的 Help 中。不带 `--seconds` 时按终端任意键结束示例；入口清理自身服务和客户端。日志与原生 `metrics.pb` 位于用户 `.local/state/xr-simulator/`，`latest` 指向最近运行目录。`--renderer venus` 专用于复现尚未通过的共享图像路径。

`screenshot --output /work/desktop.png` 优先使用 QMP；GL 扫描输出没有 CPU surface 时改用来宾的 scrot。验证截图与生成概念图应分开保存。
