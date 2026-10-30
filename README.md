# TV remote (HDMI-CEC) for Gexis Player

A plugin for [Gexis Player](https://github.com/gcarstoiu/gexis-player): the
TV's own remote drives the player over HDMI-CEC.

| On the TV's remote | On the player |
|---|---|
| Up, Down, Left, Right, OK, Back | Moves on the player's screen, as its D-pad does |
| Home (the root menu key) | The player's home screen |
| Play, Pause, the play/pause key | Plays or pauses what plays |
| Next, Previous (and fast forward, rewind) | The next or previous track |

The player joins the TV's CEC bus as a playback device on the HDMI port the TV
(or a receiver in between) is on, and answers it as one: its power, its menu,
its deck, and which input is active. It never switches the TV on or takes its
input by itself. With a receiver in between, the remote's volume keys stay
with the receiver.

## What it asks for

**`cec`**: the player's HDMI-CEC devices (`/dev/cec*`), and nothing else. Its
keys reach the player through the player's HTTP API, open to anything on the
network. Standard library Python only.

## Building

`./package.sh` builds `dist/tv-remote-<version>.tar.gz` from the files git
tracks, in a fixed order, owner and date, so the same tag gives the same bytes.
Tests: `python3 -m pytest tests`.

## Licence

GNU General Public License, version 3 or later ([LICENSE](LICENSE)).
