#!/usr/bin/env python3
"""portcheck.py <port> - exit 0 if something is listening on 127.0.0.1:<port>.
Used by the start scripts on both Windows and Linux (no netstat parsing)."""
import socket
import sys

s = socket.socket()
s.settimeout(1)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
