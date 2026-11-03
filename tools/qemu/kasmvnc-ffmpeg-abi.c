/* SPDX-License-Identifier: GPL-3.0-or-later
 * KasmVNC 1.5.2 scans /usr/lib recursively and dlopens the first filename
 * containing each FFmpeg library stem, independently. On a multi-ABI system
 * this can allocate AVFrame using one ABI and pass it to another ABI.
 * Keep only this process's explicit FFmpeg dlopen calls on FFmpeg 8.
 * All other dlopen calls, dependencies and system library files are unchanged.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <string.h>

void *dlopen(const char *filename, int flags)
{
    static void *(*next_dlopen)(const char *, int);
    const char *base;
    if (!next_dlopen)
        next_dlopen = dlsym(RTLD_NEXT, "dlopen");
    if (!filename)
        return next_dlopen(filename, flags);
    base = strrchr(filename, '/');
    base = base ? base + 1 : filename;
    if (!strncmp(base, "libavcodec.so", 13))
        filename = "libavcodec.so.62";
    else if (!strncmp(base, "libavformat.so", 14))
        filename = "libavformat.so.62";
    else if (!strncmp(base, "libavutil.so", 12))
        filename = "libavutil.so.60";
    else if (!strncmp(base, "libswscale.so", 13))
        filename = "libswscale.so.9";
    return next_dlopen(filename, flags);
}
