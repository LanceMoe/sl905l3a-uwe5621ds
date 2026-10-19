#!/usr/bin/env bash
set -u
mkdir -p dist/inbound-ping
exec >dist/inbound-ping/wireless-only-check.log 2>&1
date -Is
# Independent systemd timer restores Ethernet even if this shell is lost.
systemd-run --unit=cm311-ethernet-auto-restore --on-active=50s /usr/bin/nmcli device connect eth0 || exit 1
trap '/usr/bin/nmcli device connect eth0' EXIT
nmcli device disconnect eth0 || exit 1
sleep 3
ip -br addr
ip route
ip route get 1.1.1.1
timeout 8 ping -I wlan0 -c 4 -W 1 172.16.64.1
timeout 8 ping -I wlan0 -c 4 -W 1 1.1.1.1
timeout 5 getent ahostsv4 example.com
curl --interface wlan0 --connect-timeout 5 --max-time 10 -I https://example.com/
journalctl -k -b --no-pager -n 30
date -Is
