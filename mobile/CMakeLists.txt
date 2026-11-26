# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# PDF Teleporter for phones: the .psdi engine in C++ over MuPDF, with a Qt
# Quick interface. Builds for Android through Qt for Android, and on the
# desktop for testing (the engine, its command-line tool and the app).

cmake_minimum_required(VERSION 3.24)

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" PDFT_VERSION)
string(STRIP "${PDFT_VERSION}" PDFT_VERSION)

project(PDFTeleporterMobile
    VERSION ${PDFT_VERSION}
    DESCRIPTION "PDF Teleporter: PDF documents over narrow-band radio links"
    LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# Every floating-point operation rounds on its own, as it does on the x86-64
# desktop builds the results are compared with: no fused multiply-add on
# ARM, where compilers would otherwise form them.
if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-ffp-contract=off)
endif()

# Phones with 16 KB memory pages load only libraries whose segments are
# aligned for them (Android 15 and later).
if(ANDROID)
    add_link_options("LINKER:-z,max-page-size=16384")
endif()

option(PDFT_BUILD_APP "Build the Qt Quick application" ON)
option(PDFT_BUILD_TOOLS "Build the command-line tool (desktop)" ON)
set(PDFT_THIRD_PARTY_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party" CACHE PATH
    "Where scripts/fetch-third-party.sh put the third-party sources")

# --- third party ---------------------------------------------------------------
set(MUPDF_DIR "${PDFT_THIRD_PARTY_DIR}/mupdf")
include(cmake/MuPDF.cmake)
include(cmake/Codecs.cmake)
include(cmake/JpegTurbo.cmake)

# --- the engine ------------------------------------------------------------------
add_library(pdft_core STATIC
    src/core/json.cpp
    src/core/pyutil.cpp
    src/core/lzma_codec.cpp
    src/core/format.cpp
    src/core/geometry.cpp
    src/core/mupdf_facade.cpp
)
target_include_directories(pdft_core PUBLIC src/core)
target_include_directories(pdft_core PRIVATE ${MUPDF_ZLIB_INCLUDE_DIRS} ${MUPDF_LIBJPEG_INCLUDE_DIRS})
target_compile_definitions(pdft_core PUBLIC PDFT_VERSION="${PDFT_VERSION}")
target_link_libraries(pdft_core PUBLIC mupdf::mupdf PRIVATE liblzma)
set_target_properties(pdft_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(pdft_core PRIVATE -Wall -Wextra -Wno-unused-parameter)
endif()

# --- images, as Pillow handles them --------------------------------------------------
# libImaging from Pillow 13.0.0 (HPND licence, src/imaging/pillow/LICENSE-Pillow.txt):
# the resampling, quantisation and conversion code the desktop version runs.
set(PDFT_PILLOW_SOURCES
    src/imaging/pillow/Access.c
    src/imaging/pillow/Chops.c
    src/imaging/pillow/Convert.c
    src/imaging/pillow/ConvertYCbCr.c
    src/imaging/pillow/Copy.c
    src/imaging/pillow/Crop.c
    src/imaging/pillow/Fill.c
    src/imaging/pillow/GetBBox.c
    src/imaging/pillow/Histo.c
    src/imaging/pillow/Mode.c
    src/imaging/pillow/Palette.c
    src/imaging/pillow/Paste.c
    src/imaging/pillow/Quant.c
    src/imaging/pillow/QuantHash.c
    src/imaging/pillow/QuantHeap.c
    src/imaging/pillow/QuantOctree.c
    src/imaging/pillow/Reduce.c
    src/imaging/pillow/Resample.c
    src/imaging/pillow/Storage.c
    src/imaging/pillow/pillow_glue.c
)
set_source_files_properties(${PDFT_PILLOW_SOURCES} PROPERTIES COMPILE_OPTIONS "-w")
# JPEG through libjpeg-turbo, in a library of its own: MuPDF links its own
# libjpeg, whose symbols are the same. This one exports pdft_jpeg_* alone.
add_library(pdft_jpeg SHARED src/imaging/jpeg_codec.c)
target_compile_definitions(pdft_jpeg PRIVATE PDFT_JPEG_BUILD)
target_link_libraries(pdft_jpeg PRIVATE pdft::turbojpeg)
target_link_options(pdft_jpeg PRIVATE "LINKER:--exclude-libs,ALL")
set_target_properties(pdft_jpeg PROPERTIES C_STANDARD 11 C_VISIBILITY_PRESET hidden)

add_library(pdft_imaging STATIC
    ${PDFT_PILLOW_SOURCES}
    src/imaging/image.cpp
    src/imaging/codecs.cpp
    src/imaging/deflate.cpp
    src/imaging/metrics.cpp
)
target_include_directories(pdft_imaging PUBLIC src/imaging)
# pillow/ for libImaging's <Python.h>, which is the shim of that name there.
target_include_directories(pdft_imaging PRIVATE src/imaging/pillow ${MUPDF_ZLIB_INCLUDE_DIRS})
target_link_libraries(pdft_imaging PUBLIC pdft_core PRIVATE pdft_jpeg zlib-ng webp libwebpmux webpdemux avif Threads::Threads)
set_target_properties(pdft_imaging PROPERTIES POSITION_INDEPENDENT_CODE ON C_STANDARD 11)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(pdft_imaging PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-Wall -Wextra -Wno-unused-parameter>)
endif()

# --- the conversions (engine.py) ------------------------------------------------------
add_library(pdft_engine STATIC
    src/engine/fonts.cpp
    src/engine/symbols.cpp
    src/engine/presets.cpp
    src/engine/drawings.cpp
    src/engine/text.cpp
    src/engine/payload.cpp
    src/engine/compress.cpp
    src/engine/pdfout.cpp
    src/engine/rebuild.cpp
)
target_include_directories(pdft_engine PUBLIC src/engine)
target_link_libraries(pdft_engine PUBLIC pdft_core pdft_imaging)
set_target_properties(pdft_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
# Where the desktop builds find fonts/Carlito-*.ttf; the app hands the engine
# its own copies instead (set_resource_loader).
if(NOT ANDROID)
    set(PDFT_RESOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../application/psditool" CACHE PATH
        "Directory holding fonts/ for the desktop builds")
    target_compile_definitions(pdft_engine PRIVATE PDFT_DEFAULT_RESOURCE_DIR="${PDFT_RESOURCE_DIR}")
endif()
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(pdft_engine PRIVATE -Wall -Wextra -Wno-unused-parameter)
endif()

# --- third-party code, kept inside -------------------------------------------------
# MuPDF and its libraries go into each program, under the names of libraries
# that Qt and the system have copies of: FreeType, libjpeg, zlib... A program
# uses its own copies and exports none: exported, they would stand in for the
# others' in Qt, fontconfig or HarfBuzz once the program is loaded. And after
# each link, a check that none was taken from another library either
# (cmake/CheckOwnSymbols.cmake).
set(PDFT_CHECK_OWN_SYMBOLS "${CMAKE_CURRENT_SOURCE_DIR}/cmake/CheckOwnSymbols.cmake")
function(pdft_keep_third_party_inside _target)
    target_link_options(${_target} PRIVATE "LINKER:--exclude-libs,ALL")
    if(NOT CMAKE_NM)
        message(WARNING "No nm program: ${_target} is linked without the check of its symbols.")
        return()
    endif()
    set(_archives "")
    foreach(_lib IN LISTS MUPDF_ARCHIVES)
        list(APPEND _archives "$<TARGET_FILE:${_lib}>")
    endforeach()
    list(JOIN _archives "|" _archives)
    add_custom_command(TARGET ${_target} POST_BUILD
        COMMAND "${CMAKE_COMMAND}" "-DNM=${CMAKE_NM}" "-DBINARY=$<TARGET_FILE:${_target}>"
                "-DARCHIVES=${_archives}" -P "${PDFT_CHECK_OWN_SYMBOLS}"
        COMMENT "Checking that ${_target} takes MuPDF's functions from MuPDF"
        VERBATIM)
endfunction()

# --- command-line tool -------------------------------------------------------------
if(PDFT_BUILD_TOOLS AND NOT ANDROID)
    add_executable(pdft-cli tools/pdft_cli.cpp tools/facade_dump.cpp tools/imaging_check.cpp)
    target_include_directories(pdft-cli PRIVATE tools)
    target_compile_definitions(pdft-cli PRIVATE PDFT_CLI_DUMP PDFT_CLI_ENGINE)
    target_link_libraries(pdft-cli PRIVATE pdft_core pdft_imaging pdft_engine)
    pdft_keep_third_party_inside(pdft-cli)
endif()

# --- the application ---------------------------------------------------------------
# Qt 6.12 for Android. On the desktop any Qt 6.4 or later builds it, for
# testing: the interface keeps to what 6.4 offers.
if(PDFT_BUILD_APP)
    find_package(Qt6 6.4 REQUIRED COMPONENTS Core Gui Qml Quick QuickControls2)
    qt_standard_project_setup()
    # Qt 6.6 and later: the Android paths below are written into a JSON file
    # as they are, which a project path without quotes or backslashes allows.
    if(DEFINED QT_KNOWN_POLICY_QTP0002)
        qt_policy(SET QTP0002 NEW)
    endif()

    qt_add_executable(pdfteleporter
        app/main.cpp
        app/backend.h app/backend.cpp
        app/logmodel.h app/logmodel.cpp
        app/platform.h app/platform.cpp
    )
    if(NOT ANDROID)
        target_sources(pdfteleporter PRIVATE app/tour.h app/tour.cpp)
    endif()
    target_include_directories(pdfteleporter PRIVATE app)
    target_link_libraries(pdfteleporter PRIVATE
        Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick Qt6::QuickControls2 pdft_engine)
    pdft_keep_third_party_inside(pdfteleporter)
    if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
        target_compile_options(pdfteleporter PRIVATE -Wall -Wextra -Wno-unused-parameter)
    endif()

    # The interface: one QML module, its files at its root in the resources.
    set(PDFT_QML_FILES
        qml/Main.qml
        qml/Theme.qml
        qml/IconPaths.js
        qml/Icon.qml
        qml/Card.qml
        qml/ActionButton.qml
        qml/BarButton.qml
        qml/NavButton.qml
        qml/ChoiceRow.qml
        qml/SwitchRow.qml
        qml/FileLine.qml
        qml/Notice.qml
        qml/ScrollPage.qml
        qml/CompressPage.qml
        qml/RebuildPage.qml
        qml/LogPage.qml
        qml/ProgressPopup.qml
        qml/Snackbar.qml
        qml/AboutDialog.qml
        qml/TextDialog.qml
    )
    foreach(_file IN LISTS PDFT_QML_FILES)
        get_filename_component(_name "${_file}" NAME)
        set_source_files_properties("${_file}" PROPERTIES QT_RESOURCE_ALIAS "${_name}")
    endforeach()
    set_source_files_properties(qml/Theme.qml PROPERTIES QT_QML_SINGLETON_TYPE TRUE)
    set_source_files_properties(qml/images/pdfteleporter.png PROPERTIES
        QT_RESOURCE_ALIAS images/pdfteleporter.png)
    qt_add_qml_module(pdfteleporter
        URI PdfTeleporter
        VERSION 1.0
        RESOURCE_PREFIX /qt/qml
        QML_FILES ${PDFT_QML_FILES}
        RESOURCES qml/images/pdfteleporter.png
    )

    # The engine's fonts, and the texts the About box shows.
    set(PDFT_FONTS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../application/psditool/fonts")
    set(_fonts Carlito-Regular.ttf Carlito-Bold.ttf Carlito-Italic.ttf Carlito-BoldItalic.ttf)
    set(_font_files "")
    foreach(_font IN LISTS _fonts)
        set_source_files_properties("${PDFT_FONTS_DIR}/${_font}" PROPERTIES QT_RESOURCE_ALIAS "fonts/${_font}")
        list(APPEND _font_files "${PDFT_FONTS_DIR}/${_font}")
    endforeach()
    qt_add_resources(pdfteleporter "engine_fonts" PREFIX "/engine" FILES ${_font_files})

    set(_licence "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE.txt")
    set(_notices "${CMAKE_CURRENT_SOURCE_DIR}/legal/THIRD_PARTY_NOTICES.txt")
    set_source_files_properties("${_licence}" PROPERTIES QT_RESOURCE_ALIAS LICENSE.txt)
    set_source_files_properties("${_notices}" PROPERTIES QT_RESOURCE_ALIAS THIRD_PARTY_NOTICES.txt)
    qt_add_resources(pdfteleporter "legal" PREFIX "/legal" FILES "${_licence}" "${_notices}")

    if(ANDROID)
        # 2.6.0 -> 20600: every release a higher code than the one before.
        math(EXPR PDFT_VERSION_CODE
            "${PROJECT_VERSION_MAJOR} * 10000 + ${PROJECT_VERSION_MINOR} * 100 + ${PROJECT_VERSION_PATCH}")
        set_target_properties(pdfteleporter PROPERTIES
            QT_ANDROID_PACKAGE_NAME "org.pdfteleporter.app"
            QT_ANDROID_APP_NAME "PDF Teleporter"
            QT_ANDROID_APP_ICON "@mipmap/ic_launcher"
            QT_ANDROID_VERSION_NAME "${PDFT_VERSION}"
            QT_ANDROID_VERSION_CODE "${PDFT_VERSION_CODE}"
            QT_ANDROID_MIN_SDK_VERSION 28
            QT_ANDROID_TARGET_SDK_VERSION 36
            QT_ANDROID_COMPILE_SDK_VERSION 36
            QT_ANDROID_PACKAGE_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/android"
        )
    endif()
endif()
