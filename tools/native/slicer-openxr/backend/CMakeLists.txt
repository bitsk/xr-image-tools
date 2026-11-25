cmake_minimum_required(VERSION 3.20)
project(SlicerOpenXRBackend LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(BUILD_SHARED_LIBS ON)
find_package(VTK 9.6 REQUIRED COMPONENTS
  CommonCore CommonSystem IOImage IOXMLParser InteractionStyle InteractionWidgets
  RenderingOpenGL2 RenderingVolumeOpenGL2 ImagingSources RenderingCore)
list(APPEND CMAKE_MODULE_PATH "${VTK_SOURCE}/CMake")
vtk_module_find_modules(modules "${VTK_SOURCE}/Rendering/VRModels"
  "${VTK_SOURCE}/Rendering/VR" "${VTK_SOURCE}/Rendering/OpenXR")
vtk_module_scan(MODULE_FILES ${modules}
  PROVIDES_MODULES requested WANT_BY_DEFAULT ON
  HIDE_MODULES_FROM_CACHE ON ENABLE_TESTS OFF)
vtk_module_build(MODULES ${requested} PACKAGE SlicerOpenXR
  INSTALL_EXPORT SlicerOpenXR
  LICENSE_DESTINATION share/licenses/SlicerOpenXR
  CMAKE_DESTINATION lib/cmake/SlicerOpenXR)
