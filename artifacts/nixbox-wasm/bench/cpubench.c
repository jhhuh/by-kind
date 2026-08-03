/* A fixed integer workload for comparing x86_64 emulators.
 *
 * No libc: freestanding _start and raw syscalls only. That matters because
 * the two guests differ (musl-based Alpine vs a busybox rootfs), and glibc's
 * static startup does real work -- including getrandom(), which is exactly
 * what hung the 6.12 guest. With -nostdlib the only syscalls are the final
 * write and exit, so wall time is dominated by the loop.
 *
 * ROUNDS is compile-time so there is no argv parsing. Build one binary with
 * ROUNDS=0 and one with the real count; the difference between their run
 * times is the compute, with process startup subtracted out.
 *
 * The printed checksum depends on every iteration, so the loop cannot be
 * optimised away, and it must match across native / Bellard / nested.
 */
#ifndef ROUNDS
#define ROUNDS 0
#endif

static void sys_write(const char *buf, unsigned long len)
{
    __asm__ volatile("syscall"
                     :
                     : "a"(1), "D"(1), "S"(buf), "d"(len)
                     : "rcx", "r11", "memory");
}

static void sys_exit(int code)
{
    __asm__ volatile("syscall" : : "a"(60), "D"(code));
    __builtin_unreachable();
}

void _start(void)
{
    /* xorshift64* mixed with a multiply-accumulate: a dependent chain, so
       this measures interpreter dispatch rather than host superscalar width */
    unsigned long x = 0x123456789abcdefUL;
    unsigned long acc = 0;
    unsigned long i;
    char out[20];   /* "CK=" + 16 hex digits + '\n' */
    int j;

    for (i = 0; i < (unsigned long)ROUNDS; i++) {
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        acc += x * 0x2545F4914F6CDD1DUL;
        acc ^= acc >> 31;
    }

    out[0] = 'C'; out[1] = 'K'; out[2] = '=';
    for (j = 0; j < 16; j++)
        out[3 + j] = "0123456789abcdef"[(acc >> (60 - 4 * j)) & 0xf];
    out[19] = '\n';
    sys_write(out, sizeof(out));
    sys_exit(0);
}
