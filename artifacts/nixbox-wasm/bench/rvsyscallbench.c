/* The riscv64-native counterpart to syscallbench.c.
 *
 * Same loop, same structure, but built for riscv64 and run directly in the
 * guest with no qemu-user in the path. Counting TinyEMU's instructions for
 * this splits the ~1,774 riscv64 instructions that an emulated x86_64
 * syscall costs into:
 *
 *   this measurement          = the riscv64 kernel's own entry/exit path
 *   1774 - this measurement   = qemu-user's do_syscall marshalling
 *
 * Freestanding, so the loop is exactly what the disassembly says and glibc
 * startup contributes nothing.
 */
#ifndef ROUNDS
#define ROUNDS 0
#endif

#define SYS_write  64
#define SYS_exit   93
#define SYS_getpid 172

/* gcc emits a memset for the local buffer even at -O2; -nostdlib has none */
void *memset(void *d, int c, unsigned long n)
{
    unsigned char *p = (unsigned char *)d;
    while (n--) *p++ = (unsigned char)c;
    return d;
}

static long sys_getpid(void)
{
    register long a7 __asm__("a7") = SYS_getpid;
    register long a0 __asm__("a0");
    __asm__ volatile("ecall" : "=r"(a0) : "r"(a7) : "memory");
    return a0;
}

static void sys_write(const char *buf, unsigned long len)
{
    register long a7 __asm__("a7") = SYS_write;
    register long a0 __asm__("a0") = 1;
    register const char *a1 __asm__("a1") = buf;
    register unsigned long a2 __asm__("a2") = len;
    __asm__ volatile("ecall" : "+r"(a0) : "r"(a7), "r"(a1), "r"(a2) : "memory");
}

static void sys_exit(int code)
{
    register long a7 __asm__("a7") = SYS_exit;
    register long a0 __asm__("a0") = code;
    __asm__ volatile("ecall" : : "r"(a7), "r"(a0) : "memory");
    __builtin_unreachable();
}

void _start(void)
{
    unsigned long acc;
    unsigned long i;
    long first, v;
    int ok = 1;
    char out[20];
    int j;

    first = sys_getpid();
    for (i = 0; i < (unsigned long)ROUNDS; i++) {
        v = sys_getpid();
        if (v != first) ok = 0;
    }
    acc = ok ? (unsigned long)ROUNDS : 0;

    out[0] = 'S'; out[1] = 'C'; out[2] = '=';
    for (j = 0; j < 16; j++)
        out[3 + j] = "0123456789abcdef"[(acc >> (60 - 4 * j)) & 0xf];
    out[19] = '\n';
    sys_write(out, sizeof(out));
    sys_exit(0);
}
