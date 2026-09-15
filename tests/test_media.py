import asyncio
import sys

import km_proto
from km_stereo import Readout, pages, backdrop, tile, FONT
from operatord import media


def props(title='Kind of Blue', artist=None, status='Playing'):
    return {'PlaybackStatus': {'data': status}, 'Metadata': {'data': {
        'xesam:title': {'data': title},
        'xesam:artist': {'data': ['Miles Davis'] if artist is None else artist}}}}


def test_metadata_is_bounded_printable_and_ignores_paused_or_untitled_players():
    msg = media.playing(props('Beyoncé — “été”\n☀️', ['A', 'B']))
    assert msg == {'t': 'media', 'title': 'Beyonce - "ete"', 'artist': 'A / B'}
    assert media.playing(props(status='Paused')) is None
    assert media.playing(props(title='')) is None
    assert media.playing(props(title=12)) is None
    assert media.playing(props(artist=4))['artist'] == ''
    long = media.playing(props('"' * 1000, ['\\' * 1000]))
    assert len(km_proto.encode(long)) < 1024


def test_readout_pages_on_words_holds_heartbeat_and_expires():
    state = Readout(lambda a,b: a-b)
    msg = {'title': 'Summer lofi radio music to put you in a better mood', 'artist': 'Lofi Girl'}
    assert state.frame(0) == ('SYSTEM AUDIO', '')
    assert state.receive(msg,0)
    assert state.frame(2999) == ('SUMMER LOFI RADIO', 'LOFI GIRL')
    state.receive(msg,2000)
    assert state.frame(3000) == ('MUSIC TO PUT YOU IN', 'LOFI GIRL')
    assert state.frame(6000) == ('A BETTER MOOD', 'LOFI GIRL')
    assert not state.receive({'title': 'x'*97, 'artist': ''},8000)
    assert not state.receive({'title': 'x\n', 'artist': ''},8000)
    assert state.frame(8500) == ('SYSTEM AUDIO', '')
    state.receive(msg,9000)
    assert state.frame(9000)[0] == 'SUMMER LOFI RADIO'
    state.receive(media.empty(),10000)
    assert state.frame(10000) == ('SYSTEM AUDIO', '')
    assert pages('x'*45) == ['X'*20,'X'*20,'X'*5]


def test_readout_clock_wrap_and_pixel_bounds():
    state = Readout(lambda a,b: (a-b+32768)%65536-32768)
    state.receive({'title':'Before midnight', 'artist':'Artist'},65000)
    assert state.frame(1000)[0] == 'BEFORE MIDNIGHT'
    assert state.frame(5964) == ('SYSTEM AUDIO', '')
    assert all(0 <= x < 128 and 0 <= y < 64 for x,y in backdrop())
    assert all(len(rows) == 7 and all(0 <= r < 32 for r in rows) for rows in FONT.values())
    assert [tile(4,8,r) for r in range(8)] == [0,0,0,0,2,0,1,1]
    assert tile(16,16,0) == 3
    assert tile(1,1,7) == 3


def test_source_selection_retains_playing_source_and_handles_player_exit(monkeypatch):
    statuses = {'a':'Paused','b':'Playing','c':'Playing'}
    async def query(*args):
        if args == ('list',):
            return [{'name':media.PREFIX+n,'pid':123} for n in statuses]
        name = args[1].removeprefix(media.PREFIX)
        if statuses[name] == 'Gone':
            raise OSError('closed')
        return {'data':[props(title=name,status=statuses[name])]}
    monkeypatch.setattr(media,'query',query)
    async def scenario():
        assert (await media.snapshot())[0] == media.PREFIX+'b'
        assert (await media.snapshot(media.PREFIX+'c'))[0] == media.PREFIX+'c'
        statuses['c'] = 'Gone'
        assert (await media.snapshot(media.PREFIX+'c'))[0] == media.PREFIX+'b'
        statuses['b'] = 'Paused'
        assert await media.snapshot() == (None,media.empty())
    asyncio.run(scenario())


def test_watcher_recovers_and_disconnected_reader_does_not_query(monkeypatch):
    calls = []
    connected = False
    async def snapshot(preferred):
        calls.append(preferred)
        if len(calls) == 1:
            raise OSError('bus restarting')
        return 'source', media.playing(props())
    monkeypatch.setattr(media,'snapshot',snapshot)
    monkeypatch.setattr(media,'POLL_S',.01)
    async def scenario():
        nonlocal connected
        sent = []
        task = asyncio.create_task(media.watch(sent.append,lambda: connected))
        try:
            await asyncio.sleep(.025)
            assert not calls
            connected = True
            async with asyncio.timeout(2):
                while len(sent) < 2:
                    await asyncio.sleep(.01)
            assert sent[0] == media.empty()
            assert sent[1]['title'] == 'Kind of Blue'
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(scenario())


def test_query_reaps_child_when_cancelled(monkeypatch):
    create = asyncio.create_subprocess_exec
    children = []
    async def substitute(*args,**kwargs):
        proc = await create(sys.executable,'-c','import time; time.sleep(60)',**kwargs)
        children.append(proc)
        return proc
    monkeypatch.setattr(asyncio,'create_subprocess_exec',substitute)
    async def scenario():
        task = asyncio.create_task(media.query('list'))
        async with asyncio.timeout(2):
            while not children:
                await asyncio.sleep(.01)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert children[0].returncode is not None
    asyncio.run(scenario())
