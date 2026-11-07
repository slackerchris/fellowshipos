#!/bin/sh
# ZED runs this for every ZFS event. Most are routine, so only these reach a person.
[ -f "${ZED_ZEDLET_DIR}/zed.rc" ] && . "${ZED_ZEDLET_DIR}/zed.rc"
. "${ZED_ZEDLET_DIR}/zed-functions.sh"

pool="${ZEVENT_POOL:-unknown}"
notify=/usr/sbin/fellowship-nas-notify
vdev="${ZEVENT_VDEV_PATH:-a disk}"

case "$ZEVENT_SUBCLASS" in
    statechange)
        case "$ZEVENT_VDEV_STATE_STR" in
            FAULTED|DEGRADED|UNAVAIL|REMOVED)
                "$notify" --severity critical "Pool $pool: disk $ZEVENT_VDEV_STATE_STR" \
                    "$vdev is $ZEVENT_VDEV_STATE_STR. See: sudo fnas pool status $pool"
                ;;
        esac
        ;;
    checksum|io|data)
        # A bad disk raises these by the thousand
        zed_rate_limit "fellowship-$ZEVENT_SUBCLASS-$pool" || exit 0
        "$notify" --severity critical "Pool $pool: $ZEVENT_SUBCLASS errors" \
            "ZFS found errors on $vdev. See: sudo fnas pool status $pool"
        ;;
    scrub_finish|resilver_finish)
        name="${ZEVENT_SUBCLASS%_finish}"
        if /usr/sbin/zpool status -x "$pool" | grep -q "is healthy"; then
            "$notify" --severity info "Pool $pool: $name finished" "No problems found."
        else
            "$notify" --severity critical "Pool $pool: $name finished with problems" \
                "See: sudo fnas pool status $pool"
        fi
        ;;
esac
exit 0
