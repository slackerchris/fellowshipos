#!/bin/sh
# One pass of the responsiveness probe, run inside the guest.
#
#   sh measure.sh [runs] [tag]
#
# Prints `tag: metric value` lines and ends with MEASURE_DONE. Everything it
# reports is measured through the session itself — fellowship-msg over the
# control socket — so the same numbers mean the same thing whether the GPU
# is the software one or virgl, with or without a working screendump.
#
# What the metrics separate:
#   dri-mapped     which Mesa driver the compositor actually loaded:
#                  swrast_dri.so is llvmpipe (CPU), virtio_gpu_dri.so is
#                  virgl on the host GPU. Guessing this from the VM flags
#                  is how the last session ended up unsure.
#   super-open-ms / super-close-ms   the lone-Super launcher tap, timed
#                  end to end from the keypress to the launcher drawn.
#                  Repeat with the pointer parked (stationary) — a delivery
#                  stall shows up as one run of 1000ms+ while the rest are
#                  fast; a slow renderer shifts every run together.
#   motion-*       a scripted pointer sweep: steps, wall time, and the
#                  frames the compositor asked for (redraws) against the
#                  frames it presented (painted). painted far below redraws
#                  means every frame costs more than one vblank.
#   idle-frames    frames presented during two idle seconds: should be ~0;
#                  anything else is something repainting for no reason.
RUNS=${1:-5}
TAG=${2:-run}
say() { echo "$TAG: $1 $2"; }
fail() { say RESULT "$1"; echo MEASURE_DONE; exit 1; }

SOCK=
i=0
while [ $i -lt 90 ]; do
    SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock 2>/dev/null | head -1)
    [ -n "$SOCK" ] && fellowship-msg --socket "$SOCK" frames >/dev/null 2>&1 && break
    SOCK=
    i=$((i + 1))
    sleep 1
done
[ -n "$SOCK" ] || fail no-session

FM="fellowship-msg --socket $SOCK"
fr() { $FM frames | grep -o "\"$1\": [0-9]*" | cut -d' ' -f2; }
launcher_drawn() {
    $FM layers | awk '/"namespace": "fellowship-launcher"/{f=1}
                      f && /"drawn"/ && /true/{found=1}
                      END{exit !found}'
}

COMP=$(pidof fellowship-comp | cut -d' ' -f1)
# Mesa 25 puts every gallium driver behind one libdril_dri.so, so the mapped
# modules say nothing about which renderer was picked. The kernel's feature
# line does: +virgl means the GPU the compositor talks to is accelerated by
# the host, -virgl (or no virtio_gpu at all) means llvmpipe on the CPU.
say gpu-features "$(sudo dmesg | grep -o 'features:.*' | tail -1)"
say dri-mapped "$(grep -o 'dri/[a-zA-Z0-9_-]*\.so\|libdril[a-zA-Z0-9_.-]*' /proc/$COMP/maps 2>/dev/null | sort -u | tr '\n' ',')"
say cursor-shape "$($FM cursor | grep -o '"shape": "[^"]*"' | cut -d'"' -f4)"
say output "$(basename "$SOCK" .sock | cut -d- -f2-)"

# Idle: the screen should not be repainting at all.
p0=$(fr painted); r0=$(fr redraws); t0=$(date +%s%N)
sleep 2
p1=$(fr painted); r1=$(fr redraws); t1=$(date +%s%N)
say idle-painted $((p1 - p0))
say idle-redraws $((r1 - r0))

# The launcher tap, open and close, timed. Between runs the launcher is
# closed again and the pointer left where it is: nothing else should be
# waking the clients.
#
# wall is keypress to the launcher drawn, ask the round trip of the tap's
# own fellowship-msg request, frames the compositor presentations seen in
# between. Caveat for reading them: every one of these probes flushes the
# compositor's clients, so a pure delivery stall cannot fully show up in
# guest-side numbers — vm-measure.py's blind taps, sent through the QEMU
# monitor and seen only through screendump, are the mask-free measurement.
tap() { # $1 expectation: open|close ; echoes "wall ask frames ok"
    p=$(fr painted)
    t0=$(date +%s%N)
    $FM key super
    ask=$(( ( $(date +%s%N) - t0 ) / 1000000 ))
    ok=no
    while :; do
        if [ "$1" = open ]; then launcher_drawn; else ! launcher_drawn; fi && { ok=yes; break; }
        t=$(date +%s%N)
        [ $((t - t0)) -gt 8000000000 ] && break
    done
    t1=$(date +%s%N)
    frames=$(( $(fr painted) - p ))
    echo "$(( (t1 - t0) / 1000000 )) $ask $frames $ok"
}

i=0
while [ $i -lt "$RUNS" ]; do
    if launcher_drawn; then $FM key super; sleep 1; fi
    set -- $(tap open)
    say super-open-ms "wall=$1 ask=$2 frames=$3 ok=$4"
    set -- $(tap close)
    say super-close-ms "wall=$1 ask=$2 frames=$3 ok=$4"
    i=$((i + 1))
done

# A pointer sweep: 100 absolute steps across the screen, and what it cost.
p0=$(fr painted); r0=$(fr redraws); pr0=$(fr pointer); t0=$(date +%s%N)
i=0
while [ $i -lt 100 ]; do
    x=$((i * 16))
    y=540
    [ $i -ge 50 ] && x=$(( (100 - i) * 16 ))
    $FM move "$x" "$y"
    i=$((i + 1))
done
p1=$(fr painted); r1=$(fr redraws); pr1=$(fr pointer); t1=$(date +%s%N)
wall=$(( (t1 - t0) / 1000000 ))
say motion-steps 100
say motion-wall-ms "$wall"
say motion-redraws $((r1 - r0))
say motion-painted $((p1 - p0))
say motion-pointer-redraws $((pr1 - pr0))

# A picture, taken through the compositor so it works on either GPU; the
# shared out/ folder is visible on the host.
$FM screenshot /mnt/out/$TAG.png && say screenshot "out/$TAG.png" || say screenshot failed
say RESULT ok
echo MEASURE_DONE
