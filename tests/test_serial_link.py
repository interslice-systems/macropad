import asyncio
import os

import pytest

import km_proto
from operatord.serial_link import SerialLink
from operatord import serial_link


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


@pytest.fixture
def stalled_writer(monkeypatch):
    class Loop:
        writer = None

        def add_writer(self, fd, callback):
            self.writer = callback

        def remove_writer(self, fd):
            self.writer = None

        def remove_reader(self, fd):
            pass

    class Port:
        out_waiting = 0
        closed = False

        def fileno(self):
            return 42

        def close(self):
            self.closed = True

        def write(self, data):
            raise AssertionError('must not use pyserial post-write select')

    loop, port, downs = Loop(), Port(), []
    monkeypatch.setattr(asyncio, 'get_running_loop', lambda: loop)
    link = SerialLink('unused', lambda m: None, lambda: None,
                      on_down=lambda: downs.append(1))
    link._ser = port
    return link, loop, port, downs


def test_partial_write_waits_without_disconnect_and_preserves_packet_boundaries(stalled_writer, monkeypatch):
    link, loop, port, downs = stalled_writer
    written = bytearray()
    busy = True

    def write(fd, data):
        if busy and written:
            raise BlockingIOError()
        n = 5 if busy else len(data)
        written.extend(data[:n])
        return n

    monkeypatch.setattr(serial_link.os, 'write', write)
    first = {'t':'spectrum','active':True,'bars':[4]*16}
    state = {'t':'ws','active':3}
    assert link.send_frame(first)
    assert loop.writer is not None
    assert link.send(state)  # reliable state waits behind the packet fragment
    assert not link.send_frame({'t':'spectrum','active':True,'bars':[8]*16})
    for _ in range(4):
        loop.writer()  # a busy device must not be treated as a lost link
    assert not downs and not port.closed
    busy = False
    loop.writer()
    assert km_proto.LineCodec().feed(written) == [first,state]
    assert loop.writer is None and link._tx_bytes == 0
    assert link.send_frame({'t':'media','title':'Live','artist':''})
    assert len(km_proto.LineCodec().feed(written)) == 3


def test_io_failure_clears_partial_packet_and_writer(stalled_writer, monkeypatch):
    link, loop, port, downs = stalled_writer
    def busy(fd,data):
        raise BlockingIOError()
    monkeypatch.setattr(serial_link.os, 'write', busy)
    assert link.send({'t':'ping'})
    def disconnected(fd,data):
        raise OSError('USB removed')
    monkeypatch.setattr(serial_link.os, 'write', disconnected)
    loop.writer()
    assert downs == [1] and port.closed
    assert loop.writer is None and not link._tx and link._tx_bytes == 0


def test_reliable_queue_is_bounded(stalled_writer, monkeypatch):
    link, loop, port, downs = stalled_writer
    def busy(fd,data):
        raise BlockingIOError()
    monkeypatch.setattr(serial_link.os, 'write', busy)
    monkeypatch.setattr(serial_link, 'MAX_TX_BYTES', 32)
    assert link.send({'t':'ping'})
    assert link.send({'t':'ping'})
    assert not link.send({'t':'ping'})
    assert downs == [1] and port.closed and loop.writer is None
