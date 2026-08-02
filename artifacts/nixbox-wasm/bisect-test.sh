#!/usr/bin/env bash
# PASS/FAIL for one candidate kernel: does an x86_64 binary run under binfmt?
# usage: bisect-test.sh <kernel-store-path> [label]
set -u
K="${1:?usage: bisect-test.sh <kernel-store-path> [label]}"
LABEL="${2:-$(basename "$K")}"
HERE="$(cd "$(dirname "$0")" && pwd)"
D="$HERE/../jhhuh-tinyemu/jslinux-2019-12-21"

cat > "$HERE/rv-bisect.cfg" <<EOF
{
    version: 1,
    machine: "riscv64",
    memory_size: 256,
    bios: "$D/bbl64.bin",
    kernel: "$K/Image",
    cmdline: "console=hvc0 root=/dev/vda rw",
    drive0: { file: "$D/root-riscv64.bin" },
    fs0: { file: "$HERE/share" },
}
EOF

OUT=$(timeout 300 script -qec "{ \
  sleep 15; \
  printf 'mount -t proc none /proc; mount -t 9p -o trans=virtio,version=9p2000.L,msize=8192 /dev/root /mnt; sh /mnt/setup-binfmt3.sh\n'; \
  sleep 35; \
  printf '/mnt/busybox echo BISECT_PASS\n'; \
  sleep 230; \
} | $HERE/../tinyemu-2019-12-21/temu -append 'init=/bin/sh' $HERE/rv-bisect.cfg" /dev/null 2>&1)

img=$(stat -c%s "$K/Image" 2>/dev/null)
if echo "$OUT" | grep -q BISECT_PASS; then
  echo "PASS  $LABEL  (Image $img)"
  exit 0
else
  reg=$(echo "$OUT" | grep -c REGISTER_OK)
  echo "FAIL  $LABEL  (Image $img, binfmt registered: $reg)"
  echo "$OUT" | tail -4 | sed 's/^/      /'
  exit 1
fi
