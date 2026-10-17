#!/usr/bin/env python3
"""Select existing driver wrappers for the target's backported cfg80211 APIs."""
import re
import sys
from pathlib import Path


def detect(header):
    # Use declarations rather than documentation comments mentioning old APIs.
    text = re.sub(r'/\*.*?\*/', '', header, flags=re.S)
    flags = []

    def member(name):
        match = re.search(r'\(\*' + name + r'\)\s*\((.*?)\);', text, re.S)
        if not match:
            raise ValueError(f'Cannot find cfg80211_ops.{name}')
        return match.group(1)

    def function(name):
        match = re.search(r'\b' + name + r'\s*\((.*?)\)\s*[;{]', text, re.S)
        if not match:
            raise ValueError(f'Cannot find declaration of {name}')
        return match.group(1)

    params = [member(name) for name in (
        'add_key', 'del_key', 'add_station', 'del_station',
        'change_station', 'get_station')]
    params += [function('cfg80211_new_sta'), function('cfg80211_del_sta')]
    wdev = [bool(re.search(r'struct\s+wireless_dev\s*\*', p)) for p in params]
    ndev = [bool(re.search(r'struct\s+net_device\s*\*', p)) for p in params]
    if all(wdev):
        flags.append('-DCM311_CFG80211_WDEV')
    elif not all(ndev):
        raise ValueError('Mixed/unknown wdev and net_device API; inspect headers before building')

    roc = member('remain_on_channel')
    mgmt = member('mgmt_tx')
    has_rx_addr = bool(re.search(r'\brx_addr\b', roc))
    if has_rx_addr:
        flags.append('-DCM311_CFG80211_ROC_RX_ADDR')

    def cookie_value(params):
        if re.search(r'\bu64\s*\*\s*cookie\b', params):
            return False
        if re.search(r'\bu64\s+cookie\b', params):
            return True
        raise ValueError('Unknown cfg80211 cookie parameter')

    roc_value, mgmt_value = cookie_value(roc), cookie_value(mgmt)
    if roc_value != mgmt_value or (roc_value and not has_rx_addr):
        raise ValueError('Unsupported mix of remain_on_channel/mgmt_tx cookie APIs')
    if roc_value:
        flags.append('-DCM311_CFG80211_COOKIE_VALUE')
    return ' '.join(flags)


if __name__ == '__main__':
    try:
        print(detect((Path(sys.argv[1]) / 'include/net/cfg80211.h').read_text()))
    except (IndexError, OSError, ValueError) as error:
        sys.exit(f'cfg80211 detection failed: {error}')
