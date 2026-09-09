"""Read-only MPRIS metadata via systemd's busctl; no player plugin required."""
import asyncio
import json
import logging
import unicodedata

PREFIX = "org.mpris.MediaPlayer2."
POLL_S = 2


def clean(value):
    if not isinstance(value, str):
        return ""
    value = value.translate(str.maketrans({"’": "'", "‘": "'", "“": '"',
                                           "”": '"', "–": "-", "—": "-"}))
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join("".join(c if 32 <= ord(c) < 127 else " " for c in value).split())[:96]


def empty():
    return {"t": "media", "title": "", "artist": ""}


def playing(properties):
    """Accept only a playing source; paused browser tabs must not label music."""
    def data(obj, name, default=None):
        value = obj.get(name)
        return value.get("data", default) if isinstance(value, dict) else default
    if not isinstance(properties, dict) or data(properties, "PlaybackStatus") != "Playing":
        return None
    metadata = data(properties, "Metadata", {})
    if not isinstance(metadata, dict):
        return None
    title = clean(data(metadata, "xesam:title"))
    artists = data(metadata, "xesam:artist", [])
    artist = clean(" / ".join(a for a in artists if isinstance(a, str))) if isinstance(artists, list) else ""
    if not title:
        return None
    return {"t": "media", "title": title, "artist": artist}


async def query(*args):
    proc = await asyncio.create_subprocess_exec(
        "busctl", "--user", "--json=short", "--timeout=1", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 2)
        if proc.returncode:
            raise OSError("media bus unavailable")
        return json.loads(out)
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()


async def snapshot(preferred=None):
    names = sorted(row["name"] for row in await query("list")
                   if row.get("name", "").startswith(PREFIX) and row.get("pid"))
    # Keep the current source while it plays; deterministic if several start
    # together. MPRIS does not map titles to a particular PipeWire sink.
    if preferred in names:
        names.remove(preferred)
        names.insert(0, preferred)
    for name in names:
        try:
            result = await query("call", name, "/org/mpris/MediaPlayer2",
                                 "org.freedesktop.DBus.Properties", "GetAll",
                                 "s", "org.mpris.MediaPlayer2.Player")
            msg = playing(result["data"][0])
            if msg:
                return name, msg
        except (OSError, ValueError, KeyError, IndexError, TypeError, asyncio.TimeoutError):
            continue  # a player can disappear between enumeration and GetAll
    return None, empty()


async def watch(send, connected):
    preferred = None
    failed = False
    while True:
        if connected():
            try:
                preferred, msg = await snapshot(preferred)
                if failed:
                    logging.info("operatord: media metadata recovered")
                failed = False
            except (OSError, ValueError, KeyError, TypeError, asyncio.TimeoutError) as exc:
                preferred, msg = None, empty()
                if not failed:
                    logging.warning("operatord: media metadata unavailable: %s", exc)
                failed = True
            send(msg)  # heartbeat lets firmware expire a stuck metadata reader
        else:
            preferred = None
        await asyncio.sleep(POLL_S)
