"""Serial transport to the pad: pyserial + asyncio add_reader + reconnect."""
import asyncio
import logging

import serial

import km_proto


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

    @property
    def up(self):
        return self._ser is not None

    def send(self, msg):
        if self._ser is None:
            return False
        try:
            self._ser.write(km_proto.encode(msg))
            return True
        except (serial.SerialException, OSError):
            self._drop()
            return False

    def send_frame(self, msg):
        # Decorative frames are disposable. Never queue seconds of animation
        # behind a slow pad; state snapshots and key replies take priority.
        if self._ser is None:
            return False
        try:
            if self._ser.out_waiting > 256:
                return False
        except (serial.SerialException, OSError):
            self._drop()
            return False
        return self.send(msg)

    async def run(self):
        loop = asyncio.get_running_loop()
        while True:
            try:
                self._ser = serial.Serial(self.path, 115200, timeout=0, write_timeout=0.05)
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
                logging.exception("keymakerd: on_msg failed for %r", msg)

    def _drop(self):
        if self._ser is None:
            return
        try:
            asyncio.get_running_loop().remove_reader(self._ser.fileno())
        except (RuntimeError, OSError, ValueError):
            pass
        try:
            self._ser.close()
        except (serial.SerialException, OSError):
            pass
        self._ser = None
        if self.on_down is not None:
            try:
                self.on_down()
            except Exception:
                logging.exception("keymakerd: on_down failed")
        if self._lost is not None:
            self._lost.set()
