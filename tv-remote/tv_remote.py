#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""TV remote (HDMI-CEC), a plugin for Gexis Player: the TV's own remote drives
the player. Its arrows, OK and Back move on the player's screen; play, pause
and skip reach what plays.

The player joins the TV's HDMI-CEC bus as a playback device and answers it as
one. The plugin asks the player for `cec` (its CEC devices, /dev/cec*); the
player's controls are its HTTP API, open to anything on the network. Standard
library only.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import glob
import json
import os
import socket
import struct
import threading
import time
import urllib.request

ID = "tv-remote"
NAME = "TV remote (HDMI-CEC)"
API = os.environ.get("GEXIS_API", "http://127.0.0.1:8090")


def log(text: str) -> None:
    print(f"tv-remote: {text}", flush=True)


# ---- <linux/cec.h> -------------------------------------------------------

class CecMsg(ctypes.Structure):
    _fields_ = [("tx_ts", ctypes.c_uint64), ("rx_ts", ctypes.c_uint64), ("len", ctypes.c_uint32),
                ("timeout", ctypes.c_uint32), ("sequence", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("msg", ctypes.c_uint8 * 16), ("reply", ctypes.c_uint8), ("rx_status", ctypes.c_uint8),
                ("tx_status", ctypes.c_uint8), ("tx_arb_lost_cnt", ctypes.c_uint8),
                ("tx_nack_cnt", ctypes.c_uint8), ("tx_low_drive_cnt", ctypes.c_uint8),
                ("tx_error_cnt", ctypes.c_uint8)]


class CecLogAddrs(ctypes.Structure):
    _fields_ = [("log_addr", ctypes.c_uint8 * 4), ("log_addr_mask", ctypes.c_uint16),
                ("cec_version", ctypes.c_uint8), ("num_log_addrs", ctypes.c_uint8),
                ("vendor_id", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("osd_name", ctypes.c_char * 15),
                ("primary_device_type", ctypes.c_uint8 * 4), ("log_addr_type", ctypes.c_uint8 * 4),
                ("all_device_types", ctypes.c_uint8 * 4), ("features", (ctypes.c_uint8 * 12) * 4)]


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("a") << 8) | nr


_READ, _WRITE = 2, 1
G_PHYS_ADDR = _ioc(_READ, 1, 2)
G_LOG_ADDRS = _ioc(_READ, 3, ctypes.sizeof(CecLogAddrs))
S_LOG_ADDRS = _ioc(_READ | _WRITE, 4, ctypes.sizeof(CecLogAddrs))
TRANSMIT = _ioc(_READ | _WRITE, 5, ctypes.sizeof(CecMsg))
RECEIVE = _ioc(_READ | _WRITE, 6, ctypes.sizeof(CecMsg))
S_MODE = _ioc(_WRITE, 9, 4)
MODE_INITIATOR_AND_FOLLOWER = 0x01 | 0x10
NO_ADDRESS = 0xFFFF
BROADCAST = 0x0F
CEC_VERSION_1_4 = 5
PLAYBACK = 4            # its primary device type
PLAYBACK_ADDRESS = 3    # the kind of logical address it claims
ALL_PLAYBACK = 0x10
HDMI_VENDOR_ID = 0x000C03


class Bus:
    """One CEC adapter, as a playback device that follows the bus."""

    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)

    def close(self) -> None:
        os.close(self.fd)

    def physical(self) -> int:
        buf = bytearray(2)
        fcntl.ioctl(self.fd, G_PHYS_ADDR, buf, True)
        return struct.unpack("=H", bytes(buf))[0]

    def logical(self) -> int | None:
        addrs = CecLogAddrs()
        fcntl.ioctl(self.fd, G_LOG_ADDRS, addrs, True)
        return addrs.log_addr[0] if addrs.num_log_addrs and addrs.log_addr_mask else None

    def join(self, name: str) -> None:
        """Claim a playback address under `name` - after clearing one left by
        an earlier run - without passing the remote's keys to the kernel's
        own input device: they go to the player once, through its API."""
        fcntl.ioctl(self.fd, S_LOG_ADDRS, CecLogAddrs(), True)
        addrs = CecLogAddrs()
        addrs.num_log_addrs = 1
        addrs.cec_version = CEC_VERSION_1_4
        # The kernel answers Give Device Vendor ID with this; with none it
        # refuses it, and a TV kept asking (found 2026-10-30). The HDMI
        # placeholder, as cec-ctl uses.
        addrs.vendor_id = HDMI_VENDOR_ID
        addrs.osd_name = name.encode("ascii", "replace")[:14]
        addrs.primary_device_type[0] = PLAYBACK
        addrs.log_addr_type[0] = PLAYBACK_ADDRESS
        addrs.all_device_types[0] = ALL_PLAYBACK
        fcntl.ioctl(self.fd, S_LOG_ADDRS, addrs, True)
        fcntl.ioctl(self.fd, S_MODE, struct.pack("=I", MODE_INITIATOR_AND_FOLLOWER))

    def leave(self) -> None:
        fcntl.ioctl(self.fd, S_LOG_ADDRS, CecLogAddrs(), True)

    def answers(self, address: int) -> bool:
        """Whether a device at that logical address acknowledges a poll."""
        own = self.logical()
        return own is not None and self.transmit(bytes([(own << 4) | address]))

    def receive(self, timeout_ms: int = 1000) -> bytes | None:
        msg = CecMsg()
        msg.timeout = timeout_ms
        try:
            fcntl.ioctl(self.fd, RECEIVE, msg, True)
        except OSError as exc:
            if exc.errno in (errno.ETIMEDOUT, errno.EAGAIN):
                return None
            raise
        return bytes(msg.msg[:msg.len])

    def transmit(self, data: bytes) -> bool:
        msg = CecMsg()
        msg.len = len(data)
        for i, byte in enumerate(data):
            msg.msg[i] = byte
        try:
            fcntl.ioctl(self.fd, TRANSMIT, msg, True)
        except OSError as exc:
            log(f"not sent ({exc.strerror}): {data.hex()}")
            return False
        return bool(msg.tx_status & 0x01)


# ---- what the player does with the bus ------------------------------------

#: The remote's keys (CEC user control codes) for the player's screen.
KEYS = {0x01: "up", 0x02: "down", 0x03: "left", 0x04: "right", 0x00: "ok", 0x0D: "back"}
#: ...and for what plays. Stop pauses: the player has no stop of its own.
TRANSPORT = {0x44: "play", 0x60: "play", 0x46: "pause", 0x45: "pause", 0x64: "pause",
             0x4B: "next", 0x49: "next", 0x4C: "previous", 0x48: "previous"}
ROOT_MENU, PAUSE_PLAY = 0x09, 0x61
#: Answered by the kernel itself, or never answered: no Feature Abort from us.
NOT_OURS = {0x00, 0x83, 0x84, 0x87, 0x8C, 0x46, 0x47, 0x9E, 0x9F, 0xA5, 0xA6, 0x45, 0xFF, 0x90, 0x8E, 0x1B}


class Remote:
    """A playback device's answers, and the remote's keys to the player.

    `me` gives this player's (logical, physical) address; `send` puts bytes
    on the bus; `call` asks the player's API (a path and an optional JSON
    body)."""

    def __init__(self, me, send, call):
        self.me, self.send, self.call = me, send, call
        self.active = False
        self.playing = None
        #: Keys pressed that this plugin does not use, each said once: what
        #: a remote sends shows in the log (a Back key that did nothing,
        #: 2026-10-31).
        self.unused: set[int] = set()

    def _to(self, dest: int, *data: int) -> None:
        own, _ = self.me()
        if own is not None:
            self.send(bytes([(own << 4) | dest, *data]))

    def _active_source(self) -> None:
        _, phys = self.me()
        self._to(BROADCAST, 0x82, phys >> 8, phys & 0xFF)

    def handle(self, msg: bytes) -> None:
        if len(msg) < 2:
            return                          # a poll
        own, phys = self.me()
        initiator, dest, op, args = msg[0] >> 4, msg[0] & 0x0F, msg[1], msg[2:]
        if initiator == own:
            return
        address = (args[0] << 8 | args[1]) if len(args) >= 2 else None
        if op == 0x8F:                      # Give Device Power Status
            self._to(initiator, 0x90, 0x00)
        elif op == 0x8D:                    # Menu Request
            self._to(initiator, 0x8E, 0x00)
        elif op == 0x1A:                    # Give Deck Status
            self._to(initiator, 0x1B, 0x11 if self.playing else 0x1A)
        elif op == 0x86:                    # Set Stream Path
            self.active = address == phys
            if self.active:
                self._active_source()
        elif op == 0x85:                    # Request Active Source
            if self.active:
                self._active_source()
        elif op == 0x80 and len(args) >= 4:  # Routing Change
            self.active = (args[2] << 8 | args[3]) == phys
        elif op in (0x81, 0x82):            # Routing Information, Active Source
            self.active = address == phys
        elif op == 0x36:                    # Standby
            self.active = False
        elif op == 0x44 and args:           # User Control Pressed
            self.key(args[0])
        elif op == 0x41 and args:           # Play
            if args[0] == 0x24:
                self.transport("play")
            elif args[0] == 0x25:
                self.transport("pause")
        elif op == 0x42 and args:           # Deck Control
            self.transport({0x01: "next", 0x02: "previous", 0x03: "pause"}.get(args[0], ""))
        elif dest == own and op not in NOT_OURS:
            self._to(initiator, 0x00, op, 0x00)   # Feature Abort: not recognised

    def key(self, code: int) -> None:
        if code in KEYS:
            self.call("/panel/key", {"key": KEYS[code]})
        elif code == ROOT_MENU:
            self.call("/panel/go/home", None)
        elif code in TRANSPORT:
            self.transport(TRANSPORT[code])
        elif code == PAUSE_PLAY:
            self.transport("pause" if self.playing is not False else "play")
        elif code not in self.unused:
            self.unused.add(code)
            log(f"key 0x{code:02x} pressed; this plugin does not use it")

    def transport(self, command: str) -> None:
        if not command:
            return
        self.call(f"/transport/{command}", None)
        if command in ("play", "pause"):
            self.playing = command == "play"


def call(path: str, body: dict | None) -> None:
    data = json.dumps(body).encode() if body is not None else b""
    request = urllib.request.Request(API + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except (OSError, ValueError) as exc:
        log(f"{path}: {exc}")


# ---- the plugin ------------------------------------------------------------

def speak_to_the_player(path: str | None = None, every: float = 5.0, rounds: int | None = None) -> None:
    """Say hello on the plugin socket and answer what asks for an answer -
    again each time the player's core starts: at boot its socket may not be
    there yet (found 2026-10-31), and a restart closes it."""
    path = path or os.environ.get("GEXIS_PLUGIN_SOCKET", "/run/gexis/plugins.sock")
    while rounds is None or rounds > 0:
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.connect(path)
                stream = conn.makefile("rw")
                stream.write(json.dumps({"t": "hello", "contract": 1, "id": ID, "kind": "service",
                                         "name": NAME}) + "\n")
                stream.flush()
                for line in stream:
                    message = json.loads(line)
                    if message.get("id") is not None:
                        stream.write(json.dumps({"t": "ok", "id": message["id"], "result": True}) + "\n")
                        stream.flush()
        except (OSError, ValueError):
            pass
        if rounds is not None:
            rounds -= 1
        time.sleep(every)


#: Who must answer on the port: the TV, or a receiver (an audio system).
WANTED = (0x0, 0x5)


def find_bus(name: str, open_bus=Bus) -> Bus | None:
    """The port the TV is on. A screen that is not a TV may report an HDMI
    address too (found on a test player whose panel is on the other port),
    so each port with one is joined and polled for a TV or a receiver; the
    others are left again."""
    for path in sorted(glob.glob("/dev/cec[0-9]*")):
        try:
            bus = open_bus(path)
        except OSError as exc:
            log(f"{path}: {exc.strerror}")
            continue
        try:
            if bus.physical() != NO_ADDRESS:
                bus.join(name)
                if any(bus.answers(address) for address in WANTED):
                    return bus
                bus.leave()
        except OSError as exc:
            log(f"{path}: {exc.strerror}")
        bus.close()
    return None


def main() -> None:
    threading.Thread(target=speak_to_the_player, daemon=True).start()
    name = socket.gethostname()[:14] or "Gexis Player"
    bus, said = None, False
    while bus is None:
        bus = find_bus(name)
        if bus is None:
            if not said:
                log("no TV or receiver answers on any HDMI port yet; looking again every 10 s")
                said = True
            time.sleep(10)
    log(f"on {bus.path} as {name!r}, physical address {bus.physical():04x}, logical {bus.logical()}")
    remote = Remote(lambda: (bus.logical(), bus.physical()), bus.transmit, call)
    while True:
        msg = bus.receive()
        if msg:
            remote.handle(msg)


if __name__ == "__main__":
    main()
