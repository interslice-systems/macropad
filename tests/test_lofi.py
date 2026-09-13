"""The knob as a station dial for the lofi bar widget."""
import asyncio
import os
import stat

from operatord import lofi
from operatord.lofi import Tuner

STATIONS = [{'id': 'aaa', 'title': 'jazz lofi radio'},
            {'id': 'bbb', 'title': 'sleep lofi radio'},
            {'id': 'ccc', 'title': 'synth ambient radio'}]


def test_dial_steps_the_cursor_from_the_playing_station_and_wraps():
    t = Tuner()
    t.sync(STATIONS, current_id='bbb', running=True)
    assert t.dial(1, now=0.0) == {'t': 'tune', 'title': 'synth ambient radio',
                                  'line': 'TUNING', 'hold': 3}
    assert t.dial(1, now=0.1)['title'] == 'jazz lofi radio'       # wraps
    assert t.dial(-2, now=0.2)['title'] == 'sleep lofi radio'


def test_settle_plays_the_cursor_only_after_the_knob_rests_and_only_if_moved():
    t = Tuner(settle_s=0.6)
    t.sync(STATIONS, current_id='aaa', running=True)
    t.dial(1, now=0.0)
    assert t.settle(now=0.5) is None            # still turning
    t.dial(1, now=0.5)
    assert t.settle(now=1.0) is None            # deadline moved
    assert t.settle(now=1.2) == 'ccc'
    assert t.settle(now=2.0) is None            # already committed
    t.dial(1, now=3.0)
    t.dial(-1, now=3.1)                         # back where it started
    assert t.settle(now=4.0) is None


def test_while_stopped_dial_previews_and_push_starts_the_cursor():
    t = Tuner()
    t.sync(STATIONS, current_id='ccc', running=False)
    assert t.dial(1, now=0.0)['line'] == 'PUSH TO PLAY'
    assert t.settle(now=5.0) is None            # never starts on its own
    assert t.push() == ('play', 'aaa')
    t.sync(STATIONS, current_id='aaa', running=True)
    assert t.push() == ('stop', None)


def test_push_with_nothing_browsed_plays_the_last_station_or_the_first():
    t = Tuner()
    t.sync(STATIONS, current_id='bbb', running=False)
    assert t.push() == ('play', 'bbb')
    t.sync(STATIONS, current_id='', running=False)
    assert t.push() == ('play', 'aaa')
    t.sync([], current_id='', running=False)
    assert t.push() == ('play', None)
    assert t.dial(1, now=0.0) is None


def test_sync_is_wanted_at_the_start_of_each_browse_not_every_detent():
    t = Tuner(settle_s=0.6, resync_s=5.0)
    assert t.needs_sync(now=0.0)
    t.sync(STATIONS, current_id='aaa', running=True)
    t.dial(1, now=0.0)
    assert not t.needs_sync(now=0.3)
    assert t.needs_sync(now=6.0)


def test_settle_is_quick_by_default_and_notices_name_the_cursor_station():
    t = Tuner()
    assert t.settle_s == 0.4
    t.sync(STATIONS, current_id='bbb', running=True)
    assert t.notice('LOADING') == {'t': 'tune', 'title': 'sleep lofi radio', 'line': 'LOADING', 'hold': 10}
    assert t.notice('STOPPING', hold=4)['hold'] == 4
    t.sync([], current_id='', running=False)
    assert t.notice('LOADING') is None


def test_titles_are_squeezed_for_the_faceplate():
    t = Tuner()
    t.sync([{'id': 'x', 'title': 'lofi hip hop radio 📚 beats to relax/study to'},
            {'id': 'y', 'title': 'Study With Me 📚 Pomodoro'}],
           current_id='x', running=True)
    assert t.dial(1, now=0.0)['title'] == 'Study With Me Pomodoro'
    assert t.dial(1, now=0.0)['title'] == 'lofi hip hop radio beats to relax/study to'


def fake_lofi(tmp_path):
    script = tmp_path / 'lofi'
    script.write_text('#!/bin/sh\necho "$@" >> "$0.log"\ncase "$1" in\n'
                      '  list) echo \'[{"id":"aaa","title":"jazz"}]\' ;;\n'
                      '  status) echo \'{"running": true, "id": "aaa", "title": "jazz"}\' ;;\n'
                      'esac\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_client_shells_out_to_the_lofi_script(tmp_path):
    client = lofi.Client(fake_lofi(tmp_path))
    async def go():
        assert await client.stations() == [{'id': 'aaa', 'title': 'jazz'}]
        assert await client.status() == ('aaa', True)
        await client.play('aaa')
        await client.stop()
    asyncio.run(go())
    assert open(client.path + '.log').read().splitlines() == ['list', 'status', 'play aaa', 'stop']


def test_client_survives_a_missing_script(tmp_path):
    client = lofi.Client(str(tmp_path / 'nope'))
    async def go():
        assert await client.stations() == []
        assert await client.status() == ('', False)
    asyncio.run(go())
