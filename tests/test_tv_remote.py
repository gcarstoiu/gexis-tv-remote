# SPDX-License-Identifier: GPL-3.0-or-later
"""The plugin's answers on the bus and the remote's keys, with no TV: a fake
bus and a fake player. Run: python3 -m pytest tests"""
import ctypes
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tv-remote"))
import tv_remote as tv  # noqa: E402

TV, RECEIVER, ME = 0x0, 0x5, 0x8
MY_ADDRESS = 0x2600


def remote():
    sent, called = [], []
    r = tv.Remote(lambda: (ME, MY_ADDRESS), sent.append, lambda path, body: called.append((path, body)))
    return r, sent, called


def msg(initiator, dest, *data):
    return bytes([(initiator << 4) | dest, *data])


def test_the_kernel_s_structures_and_requests():
    """As <linux/cec.h> on the player (aarch64): their sizes make the numbers."""
    assert ctypes.sizeof(tv.CecMsg) == 56 and ctypes.sizeof(tv.CecLogAddrs) == 92
    assert (tv.TRANSMIT, tv.RECEIVE, tv.S_LOG_ADDRS) == (0xC0386105, 0xC0386106, 0xC05C6104)
    assert (tv.G_PHYS_ADDR, tv.S_MODE, tv.G_LOG_ADDRS) == (0x80026101, 0x40046109, 0x805C6103)


def test_it_answers_as_a_playback_device():
    r, sent, _ = remote()
    r.handle(msg(TV, ME, 0x8F))                     # Give Device Power Status
    r.handle(msg(TV, ME, 0x8D, 0x02))               # Menu Request
    r.handle(msg(TV, ME, 0x1A, 0x01))               # Give Deck Status
    assert sent == [msg(ME, TV, 0x90, 0x00), msg(ME, TV, 0x8E, 0x00), msg(ME, TV, 0x1B, 0x1A)]


def test_it_takes_the_input_when_the_tv_routes_to_it_and_says_so_when_asked():
    r, sent, _ = remote()
    r.handle(msg(TV, 0xF, 0x86, 0x26, 0x00))        # Set Stream Path: us
    assert r.active and sent == [msg(ME, 0xF, 0x82, 0x26, 0x00)]
    r.handle(msg(TV, 0xF, 0x85))                    # Request Active Source
    assert sent[-1] == msg(ME, 0xF, 0x82, 0x26, 0x00)
    r.handle(msg(RECEIVER, 0xF, 0x80, 0x26, 0x00, 0x20, 0x00))  # Routing Change away
    assert not r.active
    r.handle(msg(TV, 0xF, 0x85))
    assert len(sent) == 2                           # not active: says nothing


def test_the_remote_s_keys_reach_the_player():
    r, _, called = remote()
    for code in (0x01, 0x02, 0x03, 0x04, 0x00, 0x0D, 0x09, 0x44, 0x46, 0x4B, 0x4C):
        r.handle(msg(TV, ME, 0x44, code))
    assert called == [("/panel/key", {"key": k}) for k in ("up", "down", "left", "right", "ok", "back")] + [
        ("/panel/go/home", None), ("/transport/play", None), ("/transport/pause", None),
        ("/transport/next", None), ("/transport/previous", None)]


def test_one_play_pause_key_toggles():
    r, _, called = remote()
    for _ in range(3):
        r.handle(msg(TV, ME, 0x44, 0x61))
    assert [c[0] for c in called] == ["/transport/pause", "/transport/play", "/transport/pause"]


def test_play_and_deck_control_messages():
    r, _, called = remote()
    r.handle(msg(TV, ME, 0x41, 0x24))
    r.handle(msg(TV, ME, 0x41, 0x25))
    r.handle(msg(TV, ME, 0x42, 0x01))
    r.handle(msg(TV, ME, 0x42, 0x03))
    assert [c[0] for c in called] == ["/transport/play", "/transport/pause", "/transport/next", "/transport/pause"]


def test_what_it_does_not_know_is_refused_only_when_meant_for_it():
    r, sent, _ = remote()
    r.handle(msg(TV, ME, 0x9A))                     # directed, unknown: Feature Abort
    r.handle(msg(TV, 0xF, 0x9A))                    # broadcast: nothing
    r.handle(msg(TV, ME, 0x83))                     # Give Physical Address: the kernel's
    r.handle(msg(TV, ME, 0x45))                     # User Control Released: nothing
    assert sent == [msg(ME, TV, 0x00, 0x9A, 0x00)]


def test_its_own_messages_and_polls_are_ignored():
    r, sent, called = remote()
    r.handle(bytes([(ME << 4) | TV]))
    r.handle(msg(ME, TV, 0x8F))
    assert sent == [] and called == []


class FakeBus:
    """A port: its HDMI address, and who answers a poll on it."""

    def __init__(self, path, physical, answering):
        self.path, self._physical, self.answering = path, physical, answering
        self.joined = self.left = self.closed = False

    def physical(self):
        return self._physical

    def join(self, name):
        self.joined = True

    def leave(self):
        self.left = True

    def answers(self, address):
        return address in self.answering

    def close(self):
        self.closed = True


def test_the_port_is_the_one_a_tv_or_receiver_answers_on(monkeypatch):
    """A panel on the other port reports an address too (sofapi, 2026-10-30)."""
    ports = {"/dev/cec0": FakeBus("/dev/cec0", 0x1000, set()),
             "/dev/cec1": FakeBus("/dev/cec1", 0x2600, {0x0, 0x5})}
    monkeypatch.setattr(tv.glob, "glob", lambda pattern: sorted(ports))
    found = tv.find_bus("sofapi", open_bus=lambda path: ports[path])
    assert found is ports["/dev/cec1"]
    assert ports["/dev/cec0"].joined and ports["/dev/cec0"].left and ports["/dev/cec0"].closed
    ports["/dev/cec1"].answering = set()
    assert tv.find_bus("sofapi", open_bus=lambda path: ports[path]) is None
