import asyncio
import json

import pytest

from operatord import ledtest


def test_to_pixels_matches_the_app_path_with_no_gamma_decode():
    """The bridge must store the byte the app would store, unmodified.

    #808080 used to become (56,56,56) here via a 2.2 decode while Cockpit sent
    (128,128,128), so the same hex rendered two different brightnesses and the
    eyeball test measured a renderer that does not ship. Decoding is also not
    recoverable: a cell at the measured comfort floor decodes to PWM 0.
    """
    assert ledtest.to_pixels("#000000") == (0, 0, 0)
    assert ledtest.to_pixels("#ffffff") == (255, 255, 255)
    assert ledtest.to_pixels("#808080") == (128, 128, 128)
    assert ledtest.linearize is ledtest.to_pixels   # old name still resolves


def test_parse_spool_happy():
    doc = json.dumps({"colors": ["#ff0000"] * 12, "hold": 30})
    rgb, hold = ledtest.parse_spool(doc)
    assert len(rgb) == 12 and rgb[0] == (255, 0, 0) and hold == 30


@pytest.mark.parametrize("doc", [
    '{"colors": ["#ff0000"], "hold": 30}',                       # wrong count
    'not json',                                                  # invalid json
    '[]',                                                        # valid json, not a dict
    json.dumps({"colors": [255] * 12, "hold": 30}),              # ints, not hex strings
    json.dumps({"colors": ["#ff0000"] * 12, "hold": None}),      # null hold
    json.dumps({"colors": ["#ff0000"] * 12, "hold": 0}),         # hold out of range
    json.dumps({"colors": ["#ff0000"] * 12}),                    # missing hold
    json.dumps({"colors": ["#ff0000"] * 12, "hold": True}),      # bool is not an int here
    json.dumps({"colors": ["#gg0000"] * 12, "hold": 30}),        # non-hex digits
    json.dumps({"colors": ["ff0000"] * 12, "hold": 30}),         # missing leading '#'
    # int(x, 16) accepts signs and whitespace, so these once got PAST the hex
    # guard: "#-10000" reached negative-base gamma math -> complex -> round()
    # TypeError -> escaped watch() -> killed the daemon. The other two rendered
    # a silently wrong color. All three must be ValueError at the front door.
    json.dumps({"colors": ["#-10000"] * 12, "hold": 30}),        # signed: was a TypeError
    json.dumps({"colors": ["#+10000"] * 12, "hold": 30}),        # signed: rendered black
    json.dumps({"colors": ["# f0000"] * 12, "hold": 30}),        # space: rendered near-black
])
def test_parse_spool_rejects(doc):
    with pytest.raises(ValueError):
        ledtest.parse_spool(doc)


def test_watch_sends_once_and_survives_send_false(tmp_path):
    # send is SYNCHRONOUS and returns bool, like SerialLink.send.
    spool = tmp_path / "ledtest.json"
    sent, ok = [], [False]           # first send fails (link down), then succeeds

    def send(msg):
        sent.append(msg)
        r = ok[0]
        ok[0] = True
        return r

    async def run():
        task = asyncio.ensure_future(ledtest.watch(str(spool), send, interval=0.01))
        await asyncio.sleep(0.03)                       # no spool yet → no sends
        spool.write_text(json.dumps({"colors": ["#ffffff"] * 12, "hold": 5}))
        await asyncio.sleep(0.08)                       # first send False → retried → True
        task.cancel()
    asyncio.run(run())
    assert len(sent) >= 2 and sent[-1]["t"] == "ledtest" and sent[-1]["rgb"][0] == [255, 255, 255]


def test_watch_logs_only_successful_sends(tmp_path, capsys):
    # The pad is the only other observable, so a journal line per delivered
    # frame is how "did it leave the host?" gets answered. One line per frame,
    # and none for the send that failed.
    spool = tmp_path / "ledtest.json"
    ok = [False]

    def send(msg):
        r = ok[0]
        ok[0] = True
        return r

    async def run():
        task = asyncio.ensure_future(ledtest.watch(str(spool), send, interval=0.01))
        await asyncio.sleep(0.02)     # let watch() take its baseline stat first
        spool.write_text(json.dumps({"colors": ["#ffffff"] * 12, "hold": 5}))
        await asyncio.sleep(0.08)
        task.cancel()
    asyncio.run(run())
    assert capsys.readouterr().out.count("ledtest frame sent") == 1


def test_watch_ignores_preexisting_spool(tmp_path):
    spool = tmp_path / "ledtest.json"
    spool.write_text(json.dumps({"colors": ["#ffffff"] * 12, "hold": 5}))
    sent = []

    async def run():
        task = asyncio.ensure_future(
            ledtest.watch(str(spool), lambda m: sent.append(m) or True, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(run())
    assert sent == []        # a spool that predates the daemon is already consumed


def test_watch_survives_bad_spool_and_recovers(tmp_path, capsys):
    # A malformed spool must not kill the watcher (it shares the supervisor's
    # gather -- a raise there takes the whole daemon down), and it must not be
    # re-logged every tick. A later good write still lands.
    spool = tmp_path / "ledtest.json"
    sent = []

    async def run():
        task = asyncio.ensure_future(
            ledtest.watch(str(spool), lambda m: sent.append(m) or True, interval=0.01))
        await asyncio.sleep(0.02)
        spool.write_text("not json")
        await asyncio.sleep(0.06)
        spool.write_text(json.dumps({"colors": ["#00ff00"] * 12, "hold": 7}))
        await asyncio.sleep(0.06)
        alive = not task.done()
        task.cancel()
        return alive
    alive = asyncio.run(run())
    assert alive
    assert len(sent) == 1 and sent[0]["hold"] == 7
    assert capsys.readouterr().out.count("bad spool") == 1


def test_watch_survives_missing_spool_dir(tmp_path):
    # state_dir may not exist yet (nothing creates it eagerly).
    spool = tmp_path / "nope" / "ledtest.json"

    async def run():
        task = asyncio.ensure_future(
            ledtest.watch(str(spool), lambda m: True, interval=0.01))
        await asyncio.sleep(0.05)
        alive = not task.done()
        task.cancel()
        return alive
    assert asyncio.run(run())
