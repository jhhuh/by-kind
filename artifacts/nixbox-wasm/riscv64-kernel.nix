let
  pkgs = import <nixpkgs> { };
  cross = pkgs.pkgsCross.riscv64;
  k = pkgs.lib.kernel;
in
cross.linux.override {
  structuredExtraConfig = with k; {
    BINFMT_MISC = yes;          # the point of the exercise
    RISCV_SBI_V01 = yes;        # Bellard's bbl64.bin speaks legacy SBI v0.1
    NET_9P = yes;
    NET_9P_VIRTIO = yes;
    V9FS_FS = yes;
    VIRTIO_MMIO = yes;
    VIRTIO_BLK = yes;
    VIRTIO_CONSOLE = yes;
  };
}
