# A minimal riscv64 kernel for TinyEMU.
#
# nixpkgs' pkgsCross.riscv64.linux starts from the NixOS config and yields a
# 44.6 MB Image. Bellard's is 3.98 MB. This starts from the kernel's own riscv
# defconfig, strips what a TinyEMU guest cannot use, and adds what it needs.
let
  pkgs = import <nixpkgs> { };
  cross = pkgs.pkgsCross.riscv64;
  inherit (pkgs) lib;

  version = "6.12.77";
  src = pkgs.fetchurl {
    url = "mirror://kernel/linux/kernel/v6.x/linux-${version}.tar.xz";
    hash = "sha256-NYg26+XK70HnrpSS5/vN9b5uU+5DyZdSrr2oHhss/2c=";
  };

  # what we turn ON
  on = [
    "BINFMT_MISC" "BINFMT_ELF" "BINFMT_SCRIPT"
    "RISCV_SBI_V01"
    "NET_9P" "NET_9P_VIRTIO" "9P_FS" "NETFS_SUPPORT"
    "VIRTIO" "VIRTIO_MMIO" "VIRTIO_BLK" "VIRTIO_CONSOLE" "VIRTIO_NET"
    "EXT4_FS" "EXT4_USE_FOR_EXT2" "TMPFS" "DEVTMPFS" "DEVTMPFS_MOUNT"
    "SERIAL_EARLYCON_RISCV_SBI"
    "PRINTK" "TTY"
  ];

  # what we turn OFF — bulk that a TinyEMU guest can never reach
  off = [
    "MODULES" "SMP" "NUMA"
    "DRM" "FB" "SOUND" "USB_SUPPORT" "PCI" "MMC" "MTD" "INPUT"
    "WLAN" "WIRELESS" "BT" "ETHERNET" "NETDEVICES"
    "SCSI" "ATA" "NVME_CORE" "MD" "BLK_DEV_LOOP"
    "BTRFS_FS" "XFS_FS" "F2FS_FS" "NFS_FS" "NFSD" "CIFS" "SQUASHFS"
    "IPV6" "NETFILTER" "BRIDGE" "VLAN_8021Q"
    "SECURITY_SELINUX" "SECURITY_APPARMOR" "AUDIT"
    "KALLSYMS_ALL" "DEBUG_INFO" "DEBUG_KERNEL" "FTRACE" "KPROBES"
    "CRYPTO_MANAGER" "PROFILING" "PERF_EVENTS"
    "SUSPEND" "HIBERNATION" "PM" "CPU_FREQ" "CPU_IDLE"
    "VIRTIO_PCI" "VIRTIO_BALLOON" "RISCV_ISA_V" "HVC_RISCV_SBI"
    # riscv SoC/platform support a TinyEMU guest never sees
    "SOC_SIFIVE" "SOC_STARFIVE" "SOC_MICROCHIP_POLARFIRE" "SOC_CANAAN" "SOC_VIRT"
    "ARCH_THEAD" "ARCH_SOPHGO" "ARCH_RENESAS" "ARCH_SPACEMIT" "ARCH_MICROCHIP"
    "ERRATA_SIFIVE" "ERRATA_THEAD" "ERRATA_ANDES"
    "CLK_SIFIVE" "PINCTRL" "GPIOLIB" "RESET_CONTROLLER" "REGULATOR" "I2C" "SPI"
    "RTC_CLASS" "DMADEVICES" "IIO" "HWMON" "THERMAL" "WATCHDOG" "MAILBOX" "PWM"
    "EFI" "ACPI" "OF_OVERLAY" "CMA" "DMA_CMA"
    # networking beyond what 9p-over-virtio needs
    "INET" "PACKET" "UNIX_DIAG" "XFRM_USER" "NET_SCHED"
    # misc bulk
    "CRYPTO_HW" "XZ_DEC" "ZSTD_COMPRESS" "KEXEC" "CRASH_DUMP"
    "JBD2" "FS_ENCRYPTION" "QUOTA" "AUTOFS_FS" "FUSE_FS" "OVERLAY_FS"
    "MAGIC_SYSRQ" "SLUB_DEBUG" "DEBUG_MISC" "STRIP_ASM_SYMS"
    "BLK_DEV_INITRD"
  ];

  configfile = cross.stdenv.mkDerivation {
    name = "linux-config-riscv64-tinyemu-${version}";
    inherit src;
    depsBuildBuild = [ pkgs.buildPackages.stdenv.cc ];
    nativeBuildInputs = with pkgs.buildPackages; [ bison flex perl bc ];
    buildPhase = ''
      export ARCH=riscv
      export CROSS_COMPILE=${cross.stdenv.cc.targetPrefix}
      make defconfig
      ${lib.concatMapStringsSep "\n" (o: "bash scripts/config --enable ${o}") on}
      ${lib.concatMapStringsSep "\n" (o: "bash scripts/config --disable ${o}") off}
      make olddefconfig
    '';
    installPhase = "cp .config $out";
  };
in
# Build the kernel directly and take arch/riscv/boot/Image. nixpkgs'
# linuxManualConfig install phase runs modules_install, which fails with
# CONFIG_MODULES=n, and we only want the flat Image anyway.
cross.stdenv.mkDerivation {
  name = "linux-riscv64-tinyemu-${version}";
  inherit src;
  depsBuildBuild = [ pkgs.buildPackages.stdenv.cc ];
  nativeBuildInputs = with pkgs.buildPackages; [ bison flex perl bc pahole elfutils openssl ];
  configurePhase = ''
    cp ${configfile} .config
    export ARCH=riscv CROSS_COMPILE=${cross.stdenv.cc.targetPrefix}
    make olddefconfig
  '';
  buildPhase = ''
    export ARCH=riscv CROSS_COMPILE=${cross.stdenv.cc.targetPrefix}
    make -j$NIX_BUILD_CORES Image
  '';
  installPhase = ''
    mkdir -p $out
    cp arch/riscv/boot/Image $out/Image
    cp .config $out/config
  '';
}
