/* Syscall-heavy counterpart to cpubench.c.
 *
 * cpubench is a tight dependent loop: the best possible case for qemu-user,
 * which translates the loop once and then runs translated code. This one is
 * the opposite -- getpid() in a loop, which qemu-user must intercept and
 * marshal on every iteration, while a direct x86_64 emulator just runs the
 * guest kernel's syscall path. If the nested design has a weak spot, it is
 * here.
 *
 * getpid is chosen because it is trivial kernel-side, so the measurement is
 * dominated by the user/kernel transition rather than by kernel work.
 */
#ifndef ROUNDS
#define ROUNDS 0
#endif

static long sys_getpid(void)
{
    long r;
    __asm__ volatile("syscall" : "=a"(r) : "a"(39) : "rcx", "r11", "memory");
    return r;
}

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
    unsigned long acc;
    unsigned long i;
    long first, v;
    int ok = 1;
    char out[20];
    int j;

    /* The pid differs per guest, so it must not reach the checksum. Instead
       assert every call returned the same value and emit ROUNDS, which is a
       constant and therefore comparable across all three configurations. */
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
