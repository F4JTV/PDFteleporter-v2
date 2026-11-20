# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# libjpeg-turbo 3.2.0, the JPEG library Pillow 13 bundles: decoding and
# encoding through it gives the pixels and the bytes of the desktop version.
#
# Its build system refuses add_subdirectory() and asks for ExternalProject,
# so it is configured as a project of its own with the same compiler (on
# Android, the NDK's toolchain file for the ABI being built). SIMD code is
# used where the compiler alone builds it (ARM); on x86 it needs NASM, and
# without it libjpeg-turbo falls back to its C code. The SIMD and C paths
# give identical results for the integer DCT the program uses.
#
# Output: target pdft::turbojpeg, the static libjpeg (IMPORTED).

include(ExternalProject)

set(_turbo_src "${PDFT_THIRD_PARTY_DIR}/libjpeg-turbo")
if(NOT EXISTS "${_turbo_src}/CMakeLists.txt")
    message(FATAL_ERROR "libjpeg-turbo sources not found in ${_turbo_src}.\n"
                        "Run scripts/fetch-third-party.sh first.")
endif()

set(_turbo_prefix "${CMAKE_BINARY_DIR}/third_party/libjpeg-turbo")
set(_turbo_lib "${_turbo_prefix}/lib/${CMAKE_STATIC_LIBRARY_PREFIX}jpeg${CMAKE_STATIC_LIBRARY_SUFFIX}")

set(_turbo_args
    -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_INSTALL_PREFIX=${_turbo_prefix}
    -DCMAKE_INSTALL_LIBDIR=lib
    -DCMAKE_INSTALL_INCLUDEDIR=include
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_SHARED=OFF
    -DENABLE_STATIC=ON
    -DWITH_TURBOJPEG=OFF
    -DWITH_TOOLS=OFF
    -DWITH_TESTS=OFF
)
if(ANDROID)
    list(APPEND _turbo_args
        -DCMAKE_TOOLCHAIN_FILE=${ANDROID_NDK}/build/cmake/android.toolchain.cmake
        -DANDROID_ABI=${ANDROID_ABI}
        -DANDROID_PLATFORM=${ANDROID_PLATFORM})
else()
    list(APPEND _turbo_args -DCMAKE_C_COMPILER=${CMAKE_C_COMPILER})
    if(CMAKE_TOOLCHAIN_FILE)
        list(APPEND _turbo_args -DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE})
    endif()
endif()

ExternalProject_Add(libjpeg_turbo_build
    SOURCE_DIR "${_turbo_src}"
    BINARY_DIR "${CMAKE_BINARY_DIR}/third_party/libjpeg-turbo-build"
    INSTALL_DIR "${_turbo_prefix}"
    CMAKE_ARGS ${_turbo_args}
    BUILD_BYPRODUCTS "${_turbo_lib}"
    UPDATE_COMMAND ""
    EXCLUDE_FROM_ALL TRUE)

# The include directory exists only once the project is installed; an
# IMPORTED target must name one that exists when it is configured.
file(MAKE_DIRECTORY "${_turbo_prefix}/include")
add_library(pdft_turbojpeg STATIC IMPORTED GLOBAL)
set_target_properties(pdft_turbojpeg PROPERTIES
    IMPORTED_LOCATION "${_turbo_lib}"
    INTERFACE_INCLUDE_DIRECTORIES "${_turbo_prefix}/include")
add_dependencies(pdft_turbojpeg libjpeg_turbo_build)
add_library(pdft::turbojpeg ALIAS pdft_turbojpeg)
