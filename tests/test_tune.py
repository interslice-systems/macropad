"""The faceplate previews the station under the knob."""
from km_stereo import Readout


def test_tune_overrides_the_readout_for_a_moment_then_the_player_returns():
    state = Readout(lambda a, b: a - b)
    state.receive({'title': 'synth ambient radio', 'artist': 'Lofi Girl'}, 0)
    assert state.frame(100) == ('SYNTH AMBIENT RADIO', 'LOFI GIRL')
    assert state.tune({'title': 'jazz lofi radio beats to chill', 'line': 'TUNING', 'hold': 3}, 500)
    assert state.frame(600) == ('JAZZ LOFI RADIO', 'TUNING')
    assert state.frame(3499) == ('JAZZ LOFI RADIO', 'TUNING')
    assert state.tuning(3499)
    state.receive({'title': 'synth ambient radio', 'artist': 'Lofi Girl'}, 3000)
    assert state.frame(3500) == ('SYNTH AMBIENT RADIO', 'LOFI GIRL')
    assert not state.tuning(3500)


def test_tune_rejects_junk_and_shows_only_the_first_page_of_long_names():
    state = Readout(lambda a, b: a - b)
    assert not state.tune({'title': 'x' * 97, 'line': 'TUNING', 'hold': 3}, 0)
    assert not state.tune({'title': 'ok', 'line': 5, 'hold': 3}, 0)
    assert not state.tune({'title': 'ok', 'line': 'T', 'hold': 'no'}, 0)
    assert state.tune({'title': 'lofi hip hop radio beats to relax/study to', 'line': 'PUSH TO PLAY', 'hold': 3}, 0)
    assert state.frame(0) == ('LOFI HIP HOP RADIO', 'PUSH TO PLAY')
    assert state.frame(2999) == ('LOFI HIP HOP RADIO', 'PUSH TO PLAY')   # no paging while tuning
    assert state.frame(3000) == ('SYSTEM AUDIO', 'OPERATOR')


def test_a_new_title_from_the_player_cancels_a_pending_tune_line():
    state = Readout(lambda a, b: a - b)
    state.receive({'title': 'synth ambient radio', 'artist': 'Lofi Girl'}, 0)
    state.tune({'title': 'jazz lofi radio', 'line': 'LOADING', 'hold': 10}, 100)
    state.receive({'title': 'synth ambient radio', 'artist': 'Lofi Girl'}, 2000)   # same title: still loading
    assert state.frame(2000) == ('JAZZ LOFI RADIO', 'LOADING')
    state.receive({'title': '', 'artist': ''}, 3000)                                # empty gap while resolving
    assert state.frame(3000) == ('JAZZ LOFI RADIO', 'LOADING')
    state.receive({'title': 'jazz lofi radio beats to chill', 'artist': 'Lofi Girl'}, 4000)
    assert state.frame(4000) == ('JAZZ LOFI RADIO', 'LOFI GIRL')
    assert not state.tuning(4000)
