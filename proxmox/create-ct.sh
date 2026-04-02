#!/bin/bash
# FellowshipOS - Create a Proxmox LXC container
# Run this on your Proxmox node
#
# Usage:
#   ./create-ct.sh                        # interactive
#   ./create-ct.sh --flavor base          # base node
#   ./create-ct.sh --flavor docker        # docker node
#   ./create-ct.sh --flavor base --id 200 --ip 192.168.1.50/24 --gw 192.168.1.1

set -e

TEMPLATE="/var/lib/vz/template/cache/fellowshipos-trixie-amd64-1.0_lxc.tar.gz"
BRIDGE="vmbr0"
STORAGE="local-lvm"
NAMESERVER="1.1.1.1"
SEARCHDOMAIN="local"
FLAVOR="base"
IP="dhcp"
GW=""

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --flavor)   FLAVOR="$2";    shift 2 ;;
        --id)       VMID="$2";      shift 2 ;;
        --hostname) HOSTNAME="$2";  shift 2 ;;
        --ip)       IP="$2";        shift 2 ;;
        --gw)       GW="$2";        shift 2 ;;
        --bridge)   BRIDGE="$2";    shift 2 ;;
        --storage)  STORAGE="$2";   shift 2 ;;
        --dns)      NAMESERVER="$2";shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check template exists
if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template not found at $TEMPLATE"
    echo "Copy your fellowshipos LXC tarball to /var/lib/vz/template/cache/ first."
    exit 1
fi

# Auto-pick next available VMID if not set
if [ -z "$VMID" ]; then
    VMID=$(pvesh get /cluster/nextid)
fi

# Set defaults per flavor
case "$FLAVOR" in
    base)
        HOSTNAME="${HOSTNAME:-fellowshipos}"
        CORES=2
        MEMORY=2048
        SWAP=512
        DISK=8
        UNPRIVILEGED=1
        FEATURES=""
        ;;
    docker)
        HOSTNAME="${HOSTNAME:-fellowshipos-docker}"
        CORES=4
        MEMORY=4096
        SWAP=1024
        DISK=32
        UNPRIVILEGED=0
        FEATURES="--features keyctl=1,nesting=1"
        ;;
    monitor)
        HOSTNAME="${HOSTNAME:-fellowshipos-monitor}"
        CORES=2
        MEMORY=2048
        SWAP=512
        DISK=16
        UNPRIVILEGED=1
        FEATURES=""
        ;;
    storage)
        HOSTNAME="${HOSTNAME:-fellowshipos-storage}"
        CORES=2
        MEMORY=4096
        SWAP=512
        DISK=16
        UNPRIVILEGED=0
        FEATURES=""
        ;;
    *)
        echo "Unknown flavor: $FLAVOR"
        echo "Available: base, docker, monitor, storage"
        exit 1
        ;;
esac

# Build network string
if [ "$IP" = "dhcp" ]; then
    NETCONFIG="name=eth0,bridge=${BRIDGE},firewall=1,ip=dhcp"
else
    if [ -z "$GW" ]; then
        echo "ERROR: --gw required when using static IP"
        exit 1
    fi
    NETCONFIG="name=eth0,bridge=${BRIDGE},firewall=1,ip=${IP},gw=${GW}"
fi

echo ""
echo "Creating FellowshipOS CT:"
echo "  VMID:     $VMID"
echo "  Hostname: $HOSTNAME"
echo "  Flavor:   $FLAVOR"
echo "  Cores:    $CORES"
echo "  Memory:   ${MEMORY}MB"
echo "  Disk:     ${DISK}GB"
echo "  IP:       $IP"
echo ""

pct create "$VMID" "$TEMPLATE" \
    --hostname "$HOSTNAME" \
    --cores "$CORES" \
    --memory "$MEMORY" \
    --swap "$SWAP" \
    --rootfs "${STORAGE}:${DISK}" \
    --net0 "$NETCONFIG" \
    --nameserver "$NAMESERVER" \
    --searchdomain "$SEARCHDOMAIN" \
    --unprivileged "$UNPRIVILEGED" \
    --onboot 1 \
    $FEATURES

echo ""
echo "Created CT $VMID ($HOSTNAME)"
echo ""
echo "Start it:   pct start $VMID"
echo "Console:    pct console $VMID"
echo "Flavor:     pct exec $VMID -- homelabos-flavor $FLAVOR"
