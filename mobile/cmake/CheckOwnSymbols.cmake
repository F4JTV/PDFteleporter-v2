# Copyright (C) 2026 The PDF Teleporter contributors
# SPDX-License-Identifier: GPL-3.0-only
#
# Run after a link (cmake -P): fails when the binary leaves to another library
# a function that one of MuPDF's archives defines.
#
# The linker leaves such a function undefined, to be found when the program
# loads, when it met it in a shared library before it would have drawn it
# from an archive. Qt for Android links its own FreeType into libQt6Gui and
# exports it: MuPDF's FT_Add_Default_Modules came from there and filled
# MuPDF's FreeType with Qt's font drivers, and the app crashed on the first
# glyph it measured.
#
#   cmake -DNM=<nm> -DBINARY=<file> -DARCHIVES=<a.a|b.a|...> -P CheckOwnSymbols.cmake
#
# Only strong definitions count: a weak one (an inline C++ function) may
# rightly come from anywhere.

foreach(_var NM BINARY ARCHIVES)
    if("${${_var}}" STREQUAL "")
        message(FATAL_ERROR "CheckOwnSymbols: ${_var} is not set")
    endif()
endforeach()

function(_nm _out)
    execute_process(COMMAND "${NM}" -P ${ARGN}
        OUTPUT_VARIABLE _text ERROR_VARIABLE _error RESULT_VARIABLE _status)
    if(NOT _status EQUAL 0)
        message(FATAL_ERROR "CheckOwnSymbols: ${NM} ${ARGN}: ${_error}")
    endif()
    set(${_out} "${_text}" PARENT_SCOPE)
endfunction()

# The names the archives define, each alone on its line, to search in. nm -P
# writes "name type value size", and "archive[member]:" before each member's
# names; a strong definition's line is cut down to its name, the others keep
# their tail and so never match one.
set(_defined "")
string(REPLACE "|" ";" _archives "${ARCHIVES}")
foreach(_archive IN LISTS _archives)
    _nm(_text -g --defined-only "${_archive}")
    string(REGEX REPLACE "\n([^ \n]+) [TDBRGS][^\n]*" "\n\\1" _names "\n${_text}")
    string(APPEND _defined "${_names}\n")
endforeach()

_nm(_text -D --undefined-only "${BINARY}")
string(REGEX MATCHALL "[^\n]+" _lines "${_text}")
set(_taken "")
foreach(_line IN LISTS _lines)
    string(REGEX MATCH "^[^ @]+" _name "${_line}")
    if(_name STREQUAL "")
        continue()
    endif()
    string(FIND "${_defined}" "\n${_name}\n" _at)
    if(_at GREATER -1)
        list(APPEND _taken "${_name}")
    endif()
endforeach()

if(_taken)
    list(REMOVE_DUPLICATES _taken)
    list(LENGTH _taken _count)
    list(JOIN _taken ", " _names)
    message(FATAL_ERROR
        "${BINARY} takes ${_count} function(s) that MuPDF's archives define from "
        "another library: ${_names}.\n"
        "A shared library ahead of MuPDF's archives on the link line exports "
        "the same names; at run time the two copies would mix. Link the archive "
        "that defines them whole (see cmake/MuPDF.cmake, FreeType).")
endif()
