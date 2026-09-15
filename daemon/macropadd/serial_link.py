"""Serial transport to the pad: pyserial + asyncio add_reader + reconnect."""
import asyncio
import logging
import os
from collections import deque

import serial

import km_proto

MAX_TX_BYTES = 16384


class SerialLink:
    def __init__(self, path, on_msg, on_up, on_down=None, reconnect_s=2.0):
        self.path = path
        self.on_msg = on_msg
        self.on_up = on_up
        self.on_down = on_down
        self.reconnect_s = reconnect_s
        self._ser = None
        self._codec = km_proto.LineCodec()
        self._lost = None   # asyncio.Event while connected
        self._tx = deque()
        self._tx_bytes = 0
        self._writer = False

    @property
    def up(self):
        return self._ser is not None

    def send(self, msg):
        if self._ser is None:
            return False
        packet = km_proto.encode(msg)
        if self._tx_bytes + len(packet) > MAX_TX_BYTES:
            # A persistently stuck device gets a fresh snapshot on reconnect;
            # never let reliable state accumulate without a bound.
            self._drop()
            return False
        self._tx.append(packet)
        self._tx_bytes += len(packet)
        if not self._writer:
            self._writable()
        return self.up

    def _writable(self):
        """Resume partial packets without blocking key/input callbacks.

        pyserial's timed write waits for *another* writable slot even after
        os.write accepted the entire packet. CircuitPython's USB scheduling
        can take >50 ms to offer that slot; this used to tear down a healthy
        connection. Its fd is O_NONBLOCK, so write directly and let asyncio
        wait for readiness only when bytes actually remain.
        """
        if self._ser is None:
            return
        fd = self._ser.fileno()
        try:
            for _ in range(16):  # bounded work per event-loop turn
                if not self._tx:
                    break
                data = self._tx[0]
                try:
                    n = os.write(fd, data)
                except (BlockingIOError, InterruptedError):
                    break
                if not n:
                    raise OSError('serial write returned zero')
                self._tx_bytes -= n
                if n == len(data):
                    self._tx.popleft()
                else:
                    self._tx[0] = data[n:]
        except OSError:
            self._drop()
            return
        loop = asyncio.get_running_loop()
        if self._tx and not self._writer:
            loop.add_writer(fd, self._writable)
            self._writer = True
        elif not self._tx and self._writer:
            loop.remove_writer(fd)
            self._writer = False

    def send_frame(self, msg):
        # Decorative frames are disposable. Never queue seconds of animation
        # behind a slow pad; state snapshots and key replies take priority.
        if self._ser is None:
            return False
        try:
            if self._tx or self._ser.out_waiting > 256:
                return False
        except (serial.SerialException, OSError):
            self._drop()
            return False
        return self.send(msg)

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._ser = serial.Serial(self.path, 115200, timeout=0, write_timeout=0)
            except (serial.SerialException, OSError):
                self._ser = None
                await asyncio.sleep(self.reconnect_s)
                continue
            self._codec = km_proto.LineCodec()
            self._lost = asyncio.Event()
            loop.add_reader(self._ser.fileno(), self._readable)
            try:
                await self.on_up()
                await self._lost.wait()
            finally:
                self._drop()
            await asyncio.sleep(self.reconnect_s)

    def _readable(self):
        try:
            data = self._ser.read(4096)
        except (serial.SerialException, OSError, TypeError):
            self._drop()
            return
        if data == b"":
            # pty EOF shows as readable-with-empty; real ttyACM raises instead
            self._drop()
            return
        for msg in self._codec.feed(data):
            try:
                self.on_msg(msg)
            except Exception:
                logging.exception("macropadd: on_msg failed for %r", msg)

    def _drop(self):
        if self._ser is None:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.remove_reader(self._ser.fileno())
            if self._writer:
                loop.remove_writer(self._ser.fileno())
        except (RuntimeError, OSError, ValueError):
            pass
        try:
            self._ser.close()
        except (serial.SerialException, OSError):
            pass
        self._ser = None
        self._writer = False
        self._tx.clear()
        self._tx_bytes = 0
        if self.on_down is not None:
            try:
                self.on_down()
            except Exception:
                logging.exception("macropadd: on_down failed")
        if self._lost is not None:
            self._lost.set()
