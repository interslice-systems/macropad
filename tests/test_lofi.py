"""The knob push as a radio power button for the lofi bar widget."""
import asyncio
import stat

from operatord import lofi


def test_push_notice_says_stopping_with_the_playing_title_or_loading():
    assert lofi.push_notice(('aaa', True, 'jazz lofi radio')) == {
        't': 'tune', 'title': 'jazz lofi radio', 'line': 'STOPPING', 'hold': 4}
    assert lofi.push_notice(('', False, '')) == {
        't': 'tune', 'title': 'LOFI GIRL', 'line': 'LOADING', 'hold': 10}


def test_notice_titles_are_squeezed_for_the_faceplate():
    msg = lofi.push_notice(('x', True, 'Study With Me 📚 Pomodoro'))
    assert msg['title'] == 'Study With Me Pomodoro'

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
        assert await client.status() == ('aaa', True, 'jazz')
        await client.toggle()
    asyncio.run(go())
    assert open(client.path + '.log').read().splitlines() == ['status', 'toggle']


def test_client_survives_a_missing_script(tmp_path):
    client = lofi.Client(str(tmp_path / 'nope'))
    async def go():
        assert await client.status() == ('', False, '')
        await client.toggle()
    asyncio.run(go())
