# ARM64 SlicerVirtualReality OpenXR port

These port inputs target the locked Slicer 5.11 ARM64 runtime and its VTK 9.6.1,
CTK, and vtkAddon source revisions. The runtime archive contains shared libraries
but omits its development headers and CMake exports.

Configure the matching VTK source with ARM64 compilers, Qt 5, kits enabled,
legacy APIs removed, and Python wrapping disabled to generate the development
configuration and headers. Supply the packaged VTK kit libraries as its imported
runtime artifacts and matching native VTK wrapper tools for hierarchy generation.
Apply `patches/vtk-openxr-glx-lifetime.patch` before building the backend.

`backend/CMakeLists.txt` builds RenderingVRModels, RenderingVR and RenderingOpenXR.
It accepts `VTK_SOURCE` and the generated `VTK_DIR`, plus the target OpenXR SDK.
`extension/CMakeLists.txt` accepts `SLICER`, `CTK`, `VTKADDON`, `EXTENSION`,
`BACKEND`, and `RUNTIME` paths. Use an ARM64 CMake toolchain and a matching
openKylin dependency sysroot for both projects.

The extension build generates the omitted public configuration headers from
upstream templates and links the packaged Slicer libraries. Qt module controls
use their meta-object interfaces. Additional VTK VR Python wrappers are not
included in this port.

Install the extension libraries under
`/usr/lib/xr-slicer-virtualreality/lib/Slicer-5.11/qt-loadable-modules`, the VTK
backend libraries under its parent, and VTK's OpenXR JSON bindings under
`lib/Slicer-5.11/xr_actions` in that prefix. Create
`share/Slicer-5.11/qt-loadable-modules/VirtualReality` in the same prefix.
The `packaging` directory supplies the managed HyperSpace launcher and package
metadata. Install `OpenSpineXR.py` in `/usr/share/xr-slicer-virtualreality`.

The launcher uses Qt's offscreen platform so the native OpenXR scene is visible
without a desktop window. The bundled spine scene remains in `xr-slicer-spine`.
The patched shutdown keeps the GLX display alive through OpenXR session
destruction. VTK reports remaining resource leaks on process exit. Physical
controller interactions and extended stability require further validation.
