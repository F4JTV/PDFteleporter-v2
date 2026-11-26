# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# MuPDF, built from its own source lists.
#
# MuPDF ships a Makefile and an ndk-build script, not CMake. Both take their
# third-party file lists and flags from "Makelists", a plain list of
# "NAME += value" lines; this file reads the same lists, so the library built
# here is the one MuPDF's own build would make, on the desktop as under the
# Android NDK, without a second copy of those lists to keep in step.
#
# The configuration follows PyMuPDF 2.1, which the desktop version of the
# program runs on: MuPDF 1.28.6, release flags, TOFU_CJK_EXT. Features this
# program never touches -- JavaScript, OCR, barcodes, DOCX output and the
# document formats other than PDF, HTML and EPUB -- are left out. Keeping HTML
# and EPUB keeps the HTML engine, which sets right-to-left and Indic text, and
# the SIL fonts it lays text out in by default.
#
# Inputs:  MUPDF_DIR             the MuPDF source tree (with its submodules)
#          PDFT_MUPDF_FONTS      "full" (as PyMuPDF) or "base14" (smaller)
# Output:  target mupdf::mupdf   one static library, MuPDF and its third party

if(NOT EXISTS "${MUPDF_DIR}/Makelists")
    message(FATAL_ERROR
        "MuPDF sources not found in ${MUPDF_DIR}.\n"
        "Run scripts/fetch-third-party.sh first.")
endif()

set(PDFT_MUPDF_FONTS "full" CACHE STRING "Fonts built into MuPDF: full or base14")
set_property(CACHE PDFT_MUPDF_FONTS PROPERTY STRINGS full base14)

# --- Read Makelists ----------------------------------------------------------
# Only lines outside conditionals are taken: the conditional ones all belong
# to Tesseract, which is not built.
function(_mupdf_read_makelists)
    file(STRINGS "${MUPDF_DIR}/Makelists" _lines)
    set(_depth 0)
    set(_names "")
    foreach(_line IN LISTS _lines)
        if(_line MATCHES "^if(eq|neq|def|ndef)[ (]")
            math(EXPR _depth "${_depth} + 1")
            continue()
        elseif(_line MATCHES "^endif")
            math(EXPR _depth "${_depth} - 1")
            continue()
        endif()
        if(_depth GREATER 0)
            continue()
        endif()
        if(_line MATCHES "^([A-Z0-9_]+) \\+= (.+)$")
            set(_name "${CMAKE_MATCH_1}")
            set(_value "${CMAKE_MATCH_2}")
            string(STRIP "${_value}" _value)
            list(APPEND _MK_${_name} "${_value}")
            list(APPEND _names ${_name})
        endif()
    endforeach()
    list(REMOVE_DUPLICATES _names)
    foreach(_name IN LISTS _names)
        set(MK_${_name} "${_MK_${_name}}" PARENT_SCOPE)
    endforeach()
endfunction()

_mupdf_read_makelists()

# Splits a list of make-style flags into include directories, definitions and
# other options, expanding $(OTHER_FLAGS) references.
function(_mupdf_flags _out_prefix)
    set(_incs "")
    set(_defs "")
    set(_opts "")
    set(_queue ${ARGN})
    while(_queue)
        list(POP_FRONT _queue _flag)
        if(_flag MATCHES "^\\$\\(([A-Z0-9_]+)\\)$")
            list(PREPEND _queue ${MK_${CMAKE_MATCH_1}})
        elseif(_flag MATCHES "^-I(.+)$")
            list(APPEND _incs "${MUPDF_DIR}/${CMAKE_MATCH_1}")
        elseif(_flag MATCHES "^-D(.+)$")
            set(_def "${CMAKE_MATCH_1}")
            string(REPLACE "\\\"" "\"" _def "${_def}")
            list(APPEND _defs "${_def}")
        elseif(_flag MATCHES "^-std=")
            # Taken from the language standard properties instead.
        else()
            list(APPEND _opts "${_flag}")
        endif()
    endwhile()
    set(${_out_prefix}_INCLUDES "${_incs}" PARENT_SCOPE)
    set(${_out_prefix}_DEFINES "${_defs}" PARENT_SCOPE)
    set(${_out_prefix}_OPTIONS "${_opts}" PARENT_SCOPE)
endfunction()

# --- Feature selection -------------------------------------------------------
set(MUPDF_FEATURE_DEFINES
    FZ_ENABLE_JS=0
    FZ_ENABLE_OCR_OUTPUT=0
    FZ_ENABLE_BARCODE=0
    FZ_ENABLE_DOCX_OUTPUT=0
    FZ_ENABLE_ODT_OUTPUT=0
    FZ_ENABLE_XPS=0
    FZ_ENABLE_SVG=0
    FZ_ENABLE_CBZ=0
    FZ_ENABLE_FB2=0
    FZ_ENABLE_MOBI=0
    FZ_ENABLE_OFFICE=0
    FZ_ENABLE_TXT=0
    FZ_ENABLE_MD=0
    TOFU_CJK_EXT
)
if(PDFT_MUPDF_FONTS STREQUAL "base14")
    list(APPEND MUPDF_FEATURE_DEFINES TOFU)
endif()

set(_mupdf_third_libs FREETYPE GUMBO HARFBUZZ JBIG2DEC LCMS2 LIBJPEG OPENJPEG ZLIB BROTLI)

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    set(_mupdf_c_options -ffunction-sections -fdata-sections -w)
else()
    set(_mupdf_c_options "")
endif()

# --- Third-party libraries ---------------------------------------------------
set(_mupdf_public_includes "${MUPDF_DIR}/include")
set(_mupdf_public_defines ${MUPDF_FEATURE_DEFINES})
set(_mupdf_third_targets "")

foreach(_lib IN LISTS _mupdf_third_libs)
    _mupdf_flags(_pub ${MK_${_lib}_CFLAGS})
    _mupdf_flags(_bld ${MK_${_lib}_BUILD_CFLAGS})
    set(_sources "")
    foreach(_src IN LISTS MK_${_lib}_SRC)
        list(APPEND _sources "${MUPDF_DIR}/${_src}")
    endforeach()
    string(TOLOWER "mupdf_third_${_lib}" _target)
    add_library(${_target} STATIC ${_sources})
    target_include_directories(${_target} PRIVATE ${_bld_INCLUDES} ${_pub_INCLUDES})
    target_compile_definitions(${_target} PRIVATE
        ${_bld_DEFINES} ${_pub_DEFINES} ${MUPDF_FEATURE_DEFINES} NDEBUG)
    target_compile_options(${_target} PRIVATE ${_bld_OPTIONS} ${_pub_OPTIONS} ${_mupdf_c_options})
    set_target_properties(${_target} PROPERTIES
        POSITION_INDEPENDENT_CODE ON C_STANDARD 99 CXX_STANDARD 11 CXX_EXTENSIONS ON)
    list(APPEND _mupdf_third_targets ${_target})
    list(APPEND _mupdf_public_includes ${_pub_INCLUDES})
    list(APPEND _mupdf_public_defines ${_pub_DEFINES})
endforeach()

# HarfBuzz is C++ built without exceptions or RTTI, as MuPDF builds it.
target_compile_options(mupdf_third_harfbuzz PRIVATE
    $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions -fno-rtti>)

# --- Embedded fonts ----------------------------------------------------------
# The base-14 fonts and the hyphenation patterns come pre-generated with the
# sources. The fallback fonts are turned into C arrays here, by MuPDF's own
# script, as its Makefile does for every platform without objcopy.
file(GLOB _mupdf_generated
    "${MUPDF_DIR}/generated/resources/fonts/urw/*.c"
    "${MUPDF_DIR}/generated/resources/hyphen/*.c")

set(_font_inputs "")
if(NOT PDFT_MUPDF_FONTS STREQUAL "base14")
    file(GLOB _noto "${MUPDF_DIR}/resources/fonts/noto/*.otf" "${MUPDF_DIR}/resources/fonts/noto/*.ttf")
    file(GLOB _sil "${MUPDF_DIR}/resources/fonts/sil/*.cff")
    list(APPEND _font_inputs ${_noto} ${_sil}
        "${MUPDF_DIR}/resources/fonts/droid/DroidSansFallback.ttf")
endif()
find_program(BASH_EXECUTABLE bash REQUIRED)
foreach(_font IN LISTS _font_inputs)
    file(RELATIVE_PATH _rel "${MUPDF_DIR}" "${_font}")
    set(_out "${CMAKE_CURRENT_BINARY_DIR}/mupdf-generated/${_rel}.c")
    get_filename_component(_outdir "${_out}" DIRECTORY)
    add_custom_command(
        OUTPUT "${_out}"
        COMMAND ${CMAKE_COMMAND} -E make_directory "${_outdir}"
        COMMAND ${BASH_EXECUTABLE} "${MUPDF_DIR}/scripts/hexdump.sh" "${_font}" > "${_out}"
        DEPENDS "${_font}" "${MUPDF_DIR}/scripts/hexdump.sh"
        COMMENT "Embedding ${_rel}"
        VERBATIM)
    list(APPEND _mupdf_generated "${_out}")
endforeach()

# Arrays of data: no optimisation to do, and -O0 builds them in a fraction of
# the time, as MuPDF's Makefile does.
add_library(mupdf_fonts STATIC ${_mupdf_generated})
set_target_properties(mupdf_fonts PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_compile_options(mupdf_fonts PRIVATE -O0 -w)

# --- MuPDF itself --------------------------------------------------------------
file(GLOB _mupdf_core_sources
    "${MUPDF_DIR}/source/fitz/*.c"
    "${MUPDF_DIR}/source/pdf/*.c"
    "${MUPDF_DIR}/source/html/*.c"
    "${MUPDF_DIR}/source/reflow/*.c"
    "${MUPDF_DIR}/source/cbz/*.c")
list(SORT _mupdf_core_sources)

add_library(mupdf_core STATIC ${_mupdf_core_sources})
target_include_directories(mupdf_core PRIVATE ${_mupdf_public_includes})
target_compile_definitions(mupdf_core PRIVATE ${_mupdf_public_defines} NDEBUG)
target_compile_options(mupdf_core PRIVATE ${_mupdf_c_options})
set_target_properties(mupdf_core PROPERTIES POSITION_INDEPENDENT_CODE ON C_STANDARD 99 C_EXTENSIONS ON)
set_source_files_properties("${MUPDF_DIR}/source/fitz/memento.c"
    PROPERTIES COMPILE_DEFINITIONS MEMENTO_MUPDF_HACKS)

find_package(Threads REQUIRED)
add_library(mupdf INTERFACE)
add_library(mupdf::mupdf ALIAS mupdf)
target_include_directories(mupdf INTERFACE "${MUPDF_DIR}/include")
target_compile_definitions(mupdf INTERFACE ${MUPDF_FEATURE_DEFINES})
# FreeType goes in whole. Qt for Android links its own FreeType into
# libQt6Gui, and FreeType declares its functions visible whatever the build
# asks, so libQt6Gui exports them all. Met there first by lld, a function that
# nothing else drew from MuPDF's archive stayed Qt's: FT_Add_Default_Modules,
# alone in its file, filled MuPDF's FreeType with Qt's font drivers, built
# with other options than MuPDF's (slimftoptions.h), and the app crashed on
# the first glyph it measured. Linked whole, every function is MuPDF's own,
# wherever Qt stands on the link line.
set(_mupdf_grouped ${_mupdf_third_targets})
list(REMOVE_ITEM _mupdf_grouped mupdf_third_freetype)
# The others reference one another: the linker is asked to go round them until
# every symbol is found.
target_link_libraries(mupdf INTERFACE
    "$<LINK_LIBRARY:WHOLE_ARCHIVE,mupdf_third_freetype>"
    "$<LINK_GROUP:RESCAN,mupdf_core,mupdf_fonts,${_mupdf_grouped}>"
    Threads::Threads m)
# For the check after each link (cmake/CheckOwnSymbols.cmake).
set(MUPDF_ARCHIVES mupdf_core mupdf_fonts ${_mupdf_third_targets})
# For the program's own C++ code: the zlib and libjpeg MuPDF carries are the
# ones it compresses PNG and encodes JPEG with, so that only one copy of each
# goes into the binary.
set(MUPDF_ZLIB_INCLUDE_DIRS "${MUPDF_DIR}/thirdparty/zlib")
set(MUPDF_LIBJPEG_INCLUDE_DIRS "${MUPDF_DIR}/thirdparty/libjpeg" "${MUPDF_DIR}/scripts/libjpeg")
