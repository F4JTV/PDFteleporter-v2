# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# The image and compression libraries beside MuPDF, each built from its own
# CMake project at the version the desktop version's Pillow 13 bundles:
#
#   xz 5.8.4       liblzma, the raw LZMA2 streams of every archive
#   zlib-ng 2.3.3  the deflate streams of PNG payloads (its own API, beside
#                  the zlib MuPDF links)
#   libwebp 1.6.0  WebP payloads of compact archives
#   libavif 1.4.2  AVIF payloads of compact archives, with libaom 3.x and
#                  libyuv as libavif pins them
#
# On Android everything comes from the sources fetched next to this project
# (scripts/fetch-third-party.sh puts libaom and libyuv under libavif/ext,
# where libavif looks for them). A desktop build may use the system's libaom
# and libyuv instead, which is how the program is tested without network
# access to their hosts.

set(PDFT_AVIF_DEPS "AUTO" CACHE STRING "libaom and libyuv: LOCAL (fetched sources), SYSTEM or AUTO")
set_property(CACHE PDFT_AVIF_DEPS PROPERTY STRINGS AUTO LOCAL SYSTEM)

set(_saved_shared ${BUILD_SHARED_LIBS})
set(BUILD_SHARED_LIBS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# --- xz -----------------------------------------------------------------------------
set(XZ_NLS OFF CACHE BOOL "" FORCE)
set(XZ_DOC OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_XZ OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_XZDEC OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_LZMADEC OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_LZMAINFO OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_SCRIPTS OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_SYMLINKS OFF CACHE BOOL "" FORCE)
set(XZ_TOOL_SYMLINKS_LZMA OFF CACHE BOOL "" FORCE)
set(XZ_SANDBOX "no" CACHE STRING "" FORCE)
set(BUILD_TESTING OFF CACHE BOOL "" FORCE)
add_subdirectory("${PDFT_THIRD_PARTY_DIR}/xz" "${CMAKE_BINARY_DIR}/third_party/xz" EXCLUDE_FROM_ALL SYSTEM)
# xz gives liblzma's header to its own sources only. What links liblzma gets
# this lzma.h, the one of the library it links -- and not a copy the system
# may have, which an Android sysroot has not.
target_include_directories(liblzma INTERFACE "$<BUILD_INTERFACE:${PDFT_THIRD_PARTY_DIR}/xz/src/liblzma/api>")

# --- zlib-ng ------------------------------------------------------------------------
set(ZLIB_COMPAT OFF CACHE BOOL "" FORCE)
set(ZLIB_ALIASES OFF CACHE BOOL "" FORCE)
set(WITH_GZFILEOP OFF CACHE BOOL "" FORCE)
set(WITH_NATIVE_INSTRUCTIONS OFF CACHE BOOL "" FORCE)
add_subdirectory("${PDFT_THIRD_PARTY_DIR}/zlib-ng" "${CMAKE_BINARY_DIR}/third_party/zlib-ng" EXCLUDE_FROM_ALL SYSTEM)

# --- libwebp ------------------------------------------------------------------------
foreach(_opt ANIM_UTILS CWEBP DWEBP GIF2WEBP IMG2WEBP VWEBP WEBPINFO WEBPMUX EXTRAS FUZZTEST WEBP_JS)
    set(WEBP_BUILD_${_opt} OFF CACHE BOOL "" FORCE)
endforeach()
set(WEBP_BUILD_LIBWEBPMUX ON CACHE BOOL "" FORCE)
add_subdirectory("${PDFT_THIRD_PARTY_DIR}/libwebp" "${CMAKE_BINARY_DIR}/third_party/libwebp" EXCLUDE_FROM_ALL SYSTEM)

# --- libavif ------------------------------------------------------------------------
if(PDFT_AVIF_DEPS STREQUAL "AUTO")
    if(EXISTS "${PDFT_THIRD_PARTY_DIR}/libavif/ext/aom/CMakeLists.txt")
        set(_avif_deps LOCAL)
    else()
        set(_avif_deps SYSTEM)
    endif()
else()
    set(_avif_deps ${PDFT_AVIF_DEPS})
endif()
message(STATUS "PDF Teleporter: libaom and libyuv from ${_avif_deps}")
set(AVIF_CODEC_AOM ${_avif_deps} CACHE STRING "" FORCE)
set(AVIF_LIBYUV ${_avif_deps} CACHE STRING "" FORCE)
# Sharp YUV is only used when asked for, which Pillow never does.
set(AVIF_LIBSHARPYUV OFF CACHE STRING "" FORCE)
set(AVIF_CODEC_DAV1D OFF CACHE STRING "" FORCE)
set(AVIF_BUILD_APPS OFF CACHE BOOL "" FORCE)
set(AVIF_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(AVIF_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(AVIF_JPEG OFF CACHE STRING "" FORCE)
set(AVIF_ZLIBPNG OFF CACHE STRING "" FORCE)
set(AVIF_LIBXML2 OFF CACHE STRING "" FORCE)
# Encoding high bit depths is never asked for, and libaom is lighter without.
set(CONFIG_AV1_HIGHBITDEPTH 0 CACHE STRING "" FORCE)
# For an ARM target, libaom sets "as" from the PATH as the assembler when none
# is set yet: on a build for Android, the computer's own, which cannot
# assemble for the phone. Enabled here, the assembler is the NDK's clang.
if(ANDROID)
    enable_language(ASM)
endif()
add_subdirectory("${PDFT_THIRD_PARTY_DIR}/libavif" "${CMAKE_BINARY_DIR}/third_party/libavif" EXCLUDE_FROM_ALL SYSTEM)

set(BUILD_SHARED_LIBS ${_saved_shared})
