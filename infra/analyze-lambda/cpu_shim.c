/*
 * cpu_shim.c — LD_PRELOAD shim intercepting fopen/fopen64/open/open64/openat/openat64
 * for /sys/devices/system/cpu/possible and /sys/devices/system/cpu/present.
 *
 * glibc's fopen() calls openat() internally, so we must intercept openat too.
 * Returns a pipe/memstream with "0\n" so cpuinfo sees a single CPU (CPU #0).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdarg.h>
#include <errno.h>

static int is_cpu_path(const char *path) {
    return path &&
        (strcmp(path, "/sys/devices/system/cpu/possible") == 0 ||
         strcmp(path, "/sys/devices/system/cpu/present") == 0);
}

/* Return a read-only pipe fd containing "0\n" */
static int make_cpu_fd(void) {
    int pfd[2];
    if (pipe(pfd) != 0) return -1;
    write(pfd[1], "0\n", 2);
    close(pfd[1]);
    return pfd[0];
}

/* ── fopen / fopen64 ─────────────────────────────────────────────── */

FILE *fopen(const char *path, const char *mode) {
    static FILE *(*real)(const char *, const char *) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fopen");
    if (is_cpu_path(path)) {
        FILE *f = real(path, mode);
        if (f) return f;
        return fmemopen("0\n", 2, "r");
    }
    return real(path, mode);
}

FILE *fopen64(const char *path, const char *mode) {
    return fopen(path, mode);
}

/* ── open / open64 ───────────────────────────────────────────────── */

int open(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "open");
    if (is_cpu_path(path)) {
        int fd = real(path, flags);
        if (fd >= 0) return fd;
        return make_cpu_fd();
    }
    va_list ap;
    va_start(ap, flags);
    mode_t mode = va_arg(ap, mode_t);
    va_end(ap);
    return real(path, flags, mode);
}

int open64(const char *path, int flags, ...) {
    static int (*real)(const char *, int, ...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "open64");
    if (is_cpu_path(path)) {
        int fd = real(path, flags);
        if (fd >= 0) return fd;
        return make_cpu_fd();
    }
    va_list ap;
    va_start(ap, flags);
    mode_t mode = va_arg(ap, mode_t);
    va_end(ap);
    return real(path, flags, mode);
}

/* ── openat / openat64 ───────────────────────────────────────────── */
/* glibc's fopen() calls openat() internally — this is the critical one */

int openat(int dirfd, const char *path, int flags, ...) {
    static int (*real)(int, const char *, int, ...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "openat");
    if (is_cpu_path(path)) {
        int fd = real(dirfd, path, flags);
        if (fd >= 0) return fd;
        return make_cpu_fd();
    }
    va_list ap;
    va_start(ap, flags);
    mode_t mode = va_arg(ap, mode_t);
    va_end(ap);
    return real(dirfd, path, flags, mode);
}

int openat64(int dirfd, const char *path, int flags, ...) {
    return openat(dirfd, path, flags);
}
