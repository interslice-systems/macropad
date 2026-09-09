import asyncio
import os

import pytest

import km_proto
from operatord.serial_link import SerialLink


@pytest.fixture
def pty_pair():
    master, slave = os.openpty()
    os.set_blocking(master, False)
    yield master, os.ttyname(slave)
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


async def _drain(master):
    await asyncio.sleep(0.15)
    try:
        return os.read(master, 4096)
    except BlockingIOError:
        return b""


def test_receives_messages_and_calls_on_up(pty_pair):
    master, slave_path = pty_pair
    got, ups = [], []

    async def scenario():
        link = SerialLink(slave_path, on_msg=got.append,
                         on_up=lambda: ups.append(1) or asyncio.sleep(0))
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.15)
        os.write(master, km_proto.encode({"t": "hello", "fw": "0.1.0"}))
        await asyncio.sleep(0.15)
        assert link.send({"t": "ping"}) is True
        out = await _drain(master)
        task.cancel()
        return out

    out = asyncio.run(scenario())
    assert got == [{"t": "hello", "fw": "0.1.0"}]
    assert ups == [1]
    assert b'{"t":"ping"}\n' in out


def test_send_while_down_returns_false():
    link = SerialLink("/dev/does-not-exist", on_msg=lambda m: None,
                     on_up=lambda: asyncio.sleep(0))
    assert link.send({"t": "ping"}) is False


def test_audio_frames_drop_when_serial_is_backed_up():
    class BackedUp:
        out_waiting = 512

        def write(self, data):
            raise AssertionError("must not queue decorative frames")

    link = SerialLink("unused", on_msg=lambda m: None,
                      on_up=lambda: asyncio.sleep(0))
    link._ser = BackedUp()
    assert link.send_frame({"t": "spectrum"}) is False


def test_on_down_fires_when_the_link_drops(pty_pair):
    master, slave_path = pty_pair
    downs = []

    async def scenario():
        link = SerialLink(slave_path, on_msg=lambda m: None,
                         on_up=lambda: asyncio.sleep(0),
                         on_down=lambda: downs.append(1))
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.15)
        assert downs == []
        os.close(master)          # slave reads EOF -> _drop()
        await asyncio.sleep(0.15)
        task.cancel()

    asyncio.run(scenario())
    assert downs == [1]


def test_bad_message_does_not_drop_rest_of_batch(pty_pair):
    master, slave_path = pty_pair
    got, calls = [], {"n": 0}

    def on_msg(m):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("boom")
        got.append(m)

    async def scenario():
        link = SerialLink(slave_path, on_msg=on_msg,
                         on_up=lambda: asyncio.sleep(0))
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.15)
        chunk = km_proto.encode({"t": "a"}) + km_proto.encode({"t": "b"})
        os.write(master, chunk)
        await asyncio.sleep(0.15)
        task.cancel()

    asyncio.run(scenario())
    assert got == [{"t": "b"}]


def test_missing_device_keeps_retrying():
    async def scenario():
        link = SerialLink("/dev/does-not-exist", on_msg=lambda m: None,
                         on_up=lambda: asyncio.sleep(0), reconnect_s=0.05)
        task = asyncio.create_task(link.run())
        await asyncio.sleep(0.3)          # several failed attempts; no crash
        alive = not task.done()
        task.cancel()
        return alive

    assert asyncio.run(scenario()) is True
