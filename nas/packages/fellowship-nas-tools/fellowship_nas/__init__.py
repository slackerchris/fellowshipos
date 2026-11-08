"""fellowship_nas: storage, sharing and protection for FellowshipNAS, behind
the fnas command.

This is the privilege boundary for the web UI, so every argument is validated
here and commands are never built through a shell. It passes -f to zpool only
to accept disks of different sizes in one group: ZFS keeps refusing everything
else it thinks is unsafe, and old data is only cleared by an explicit --wipe.
"""
