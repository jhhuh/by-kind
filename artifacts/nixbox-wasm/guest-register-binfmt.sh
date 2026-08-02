#!/bin/sh
mount -t binfmt_misc none /proc/sys/fs/binfmt_misc 2>/dev/null
cat /mnt/binfmt-alpine   # Alpine's conf verbatim, interpreter path rewritten > /proc/sys/fs/binfmt_misc/register && echo REGISTER_OK || echo REGISTER_FAILED
