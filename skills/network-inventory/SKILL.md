---
name: network-inventory
description: 'Scan the user''s local network and turn the result into an inventory: every device, what it probably is, and anything unexpected. Use for "what is on my network", "list all devices", "network inventory".'
allowed-tools: network_scan
metadata:
  title: Network inventory
  category: IT & security
  specialist: it
  inputs: the network range, e.g. 192.168.1.0/24
---

# Network inventory

## Steps
1. Run network_scan on the range the user gave (private ranges only).
2. Group devices by what they probably are (router, printer, camera, PC, phone, server) from the
   vendor, open ports and host name; say "unknown" when nothing tells.
3. Flag anything worth a look: unknown vendors, remote-access ports (RDP, Telnet, VNC) open,
   devices with default-looking names, duplicate host names.

## Output
### Summary
How many devices, by type.
### Devices
| IP | Name | Vendor | Probably | Open ports |
### Worth a look
Each item with why and what to check.
