# XR rootfs

多系统 rootfs 的准备和组件组装。系统、型号与使用场景分别扩展，首批为 Debian / SteamOS 和 MIX A081。

```sh
python3 tools/rootfs.py plan --system debian --device a081 --profile debug
python3 tools/rootfs.py plan --system steamos --device a081 --profile vr
python3 tools/rootfs.py prepare --system steamos --source /archives/official-steamos.zip --output /new/prepared
python3 tools/rootfs.py prepare --system debian --source https://deb.debian.org/debian --output /new/prepared
python3 tools/rootfs.py assemble --system steamos --base-archive /prepared/rootfs.tar.zst --artifacts /private/artifact-map.json --output /new/bundle
```

prepare 仅在主机准备新目录：SteamOS 核对官方 archive/image/partition 摘要并保留元数据；Debian 使用 debootstrap foreign-stage（需要 debootstrap 和相应主机权限），第二阶段必须在目标架构完成。SteamOS Python 依赖版本见 systems/steamos/legacy/profile.json。

assemble 生成基础归档的原样副本、设备配置 overlay.tar 和锁定提交/摘要的 manifest.json。artifact-map 是本地 JSON 数组，每项含 source、根相对 destination、sha256，用于输入各组件构建产物；不会自动启用服务、安装厂商库、封装 recovery 或刷写设备。没有 --artifacts 时只有板级配置，不构成完整 VR 运行系统。

新系统：在 systems/<name>/ 添加 adapter.json、adapter.py；prepare.program/arguments 是统一入口，输出 rootfs 归档。新型号：devices/<model>/model.json 声明兼容系统、架构与文件 overlay。新组合：profiles/<name>.json 选择组件，components.lock.json 固定 Git 提交。计划与组装自动使用新增条目，不需要创建更多 OS 仓库。

systems/*/legacy/ 保留原有设备侧初始化/配置和镜像配方；其中 /mnt/osstore 等路径属于既有 A081 安装流程，不能当作通用主机命令直接运行。相邻 openkylin-xr-integration 的 devices/a081/ 保存已用的音频、传感器、热控来源；网络凭据、厂商固件和库不进入 Git。

这次是源码拆分和离线构建迁移，未重新验收硬件；已验证系统与 recovery 回退保持原状。工程架构、迁移状态、Wiki 与 Blog 见 [openkylin-xr-docs](https://gitee.com/bigbits/openkylin-xr-docs)。

## 固定版本原厂二进制

原厂依赖改由独立公开 [xr-vendor-assets](https://gitee.com/bigbits/xr-vendor-assets) 的 Release 提供；本仓库只保存 devices/a081/vendor.lock.json 中的资产 ID、归档与逐文件 SHA-256。无需反复提取原厂镜像：

```sh
python3 tools/vendor.py fetch --output /new/vendor-cache
python3 tools/vendor.py verify --cache /new/vendor-cache
python3 tools/rootfs.py assemble --system steamos --base-archive /prepared/rootfs.tar.zst --vendor-cache /new/vendor-cache --artifacts /private/native-artifacts.json --output /new/bundle
```

下载使用现有 gh 登录；之后 verify/assemble 只读取本地缓存。`--bundle firmware` 可只下载板级固件，供 maintenance profile 使用。所有 native artifacts 仍由对应源码构建，厂商包不包含它们；per-device persist 校准单独本机备份并注入。型号包缺少校准时不代表可完成跟踪验收。未提供 --vendor-cache 的组装仍允许生成配置包，manifest 会明确 vendor_included=false。

通用启动环境不包含安装分区 UUID。启动时通过 `xr.osstore_uuid=<UUID>`，或私有 initramfs 的 `/etc/xr-device.conf` 中的 `osstore_uuid` 提供本机身份；缺少身份时停止启动，不跳过分区检查。包含私人身份或校准的镜像只在本地保管。

## openKylin 打包

本仓库包含独立的 `debian/` 配方。使用目标发行线的干净构建环境运行 `dpkg-buildpackage -us -uc -b`，预先准备 Build-Depends 和离线构建输入。包构建、目标系统安装和设备验收分别记录。

当前迁移不自动启用竞争采集、切换活动运行时或写设备启动分区；已有验证基线继续保留。

## 原生 XR 场景会话

`xr-shell-session` 是已经运行的真实 OpenXR 会话中的前台场景应用入口，消费父会话提供的 `XR_RUNTIME_JSON`、Vulkan ICD 和兼容层环境。它不启动模拟器、硬件采集、跟踪生产者或音频服务，也不修改全局运行时选择。以已登录的普通用户运行：

该入口由独立的 `xr-shell-session` 包提供，目标设备不需要安装带镜像制作依赖的 `xr-image-builder`。原生 loader 必须以 `libopenxr_loader.so.1` 可被动态链接器查找到；入口会检查并记录实际映射。可用目标发行版的系统包，或由父会话通过私有 `LD_LIBRARY_PATH` 提供经过核验的本机运行时 loader；本包不会下载、替换或重新选择运行时。

```sh
xr-shell-session --debug-input
xr-shell-session --prefix /opt/xr-development --seconds 30
```

所选 prefix 必须提供 `xr-shell`、`xr-shellctl`、`xr-window-server`、`xr-dock`、`xr-system-ui`，以及 `share/xr/environments/qingkong/environment.json`；可用 `--environment` 指定其他环境描述。Shell 保持真实登录用户的 `XDG_RUNTIME_DIR`，组件单独使用私有 Wayland socket、运行与配置目录及 D-Bus。默认带 Xwayland 和 Fcitx 的 `keyboard-us`/`pinyin` 组；缺少依赖会明确失败，可显式用 `--no-xwayland` 或 `--no-ime` 关闭对应功能。Pulse 客户端指向原登录用户的服务，禁止自动派生新音频服务。

外层收到 SIGTERM 后只通知其拥有的内部 helper。helper 先请求 Shell 正常退出并等待 OpenXR/GPU 清理，再回收组件；最后私有 D-Bus 退出并清理身份匹配的激活进程与挂载。正常退出码为零，超时或异常退出会保留失败状态，不将强制终止标为正常。状态默认写入 `~/.local/state/xr-shell-session/run-*`，`latest` 记录最近目录：`ready.json` 和 `startup-snapshot.json` 记录真实组件场景发布，`captures/` 保存 Shell 产物，`summary.json`、`children.json`、`lifecycle.jsonl` 与 `session-result.json` 记录进程、退出顺序和清理结果。运行时本体与跟踪服务不由此入口停止。Fcitx 组配置检查不代表中文预编辑已验收。

## openKylin 3.0 ARM64 基础系统

`systems/openkylin/source.lock.json` 固定官方桌面 ISO、长度和摘要。下载后核对官方 MD5 及锁定的 SHA-256，并把 SHA-256 写入 ISO 同名的 `.sha256` 文件：

```sh
python3 tools/rootfs.py prepare --system openkylin --profile maintenance --source /archives/openkylin-arm64.iso --output /new/prepared
python3 tools/rootfs.py assemble --system openkylin --profile maintenance --base-archive /new/prepared/rootfs.tar --output /new/bundle
```

主机需要 `xorriso` 与 `squashfs-tools-ng` 的 `sqfs2tar`。直接转换保留数值所有权、硬链接、权限和扩展属性，无需 root 或 fakeroot，也不会漏掉普通用户不可读取的源文件。官方 ARM64 ISO 的 ISO9660 大文件长度有 4 GiB 截断；适配器检查 squashfs 头和边界，恢复连续数据区后强制校验 ISO 内 SHA-256，任何不一致均停止。

产物是官方 live 文件系统。设备安装另需板级固件、与启动内核一致的模块、公开 `huanghe` 软件源及原生系统配置；镜像中的工厂构建代理源不能当作公开更新源。A081 原生配置入口属于相邻 `openkylin-xr-integration/devices/a081/openkylin/`。共用 `systems/steamos/legacy/init` 支持一次性 `next-system=openkylin`，消费选择器后回退默认 Debian。私有 osstore UUID 从 `xr-device.conf` 或启动参数注入。包构建、原生安装、系统启动、桌面输出与佩戴验收分别记录。

原生与通用桌面启动器在隔离 XDG 配置前保留用户配置根，并向 System UI
传入 `XR_SYSTEM_UI_PREFERENCES_FILE`；仅自身 taskbar 偏好跨会话保留。
Wayland、D-Bus、输入法及其余组件配置仍使用原有私有目录和清理生命周期。
