from pathlib import Path

from macropadd.theme import load_palette, resolve_theme_dir

FIXTURES = Path(__file__).parent / "fixtures"


def _theme(tmp_path, name, toml_text):
    d = tmp_path / name
    d.mkdir()
    (d / "colors.toml").write_text(toml_text)
    return d


def test_resolve_prefers_state_path_over_config(tmp_path):
    for rel in (".local/state/omarchy/current", ".config/omarchy/current"):
        target = tmp_path / rel / "themes" / rel.split("/")[1]
        target.mkdir(parents=True)
        (tmp_path / rel / "theme").symlink_to(target)
    assert "state" in str(resolve_theme_dir(tmp_path))


def test_resolve_falls_back_to_config_then_none(tmp_path):
    assert resolve_theme_dir(tmp_path) is None
    target = tmp_path / "t"; target.mkdir()
    cfg = tmp_path / ".config/omarchy/current"; cfg.mkdir(parents=True)
    (cfg / "theme").symlink_to(target)
    assert resolve_theme_dir(tmp_path) == target


def test_load_palette_from_real_omarchy_384_theme(tmp_path):
    d = tmp_path / "fantasy"; d.mkdir()
    (d / "colors.toml").write_text((FIXTURES / "colors-fantasy.toml").read_text())
    pal = load_palette(d)
    assert pal["t"] == "palette" and pal["name"] == "fantasy"
    assert pal["accent"] == "faa968" and pal["bg"] == "05182e"
    assert pal["red"] == "f85525"    # no 'red' key on 3.8.4 → color1 fallback
    assert pal["muted"] == "134e5a"  # no 'muted' key → color8 fallback


def test_load_palette_quattro_style_semantic_keys(tmp_path):
    d = _theme(tmp_path, "q", 'accent = "#112233"\nbackground = "#000000"\n'
               'foreground = "#ffffff"\nred = "#ff0000"\nmuted = "#333344"\n')
    pal = load_palette(d)
    assert pal["red"] == "ff0000" and pal["muted"] == "333344"


def test_load_palette_bad_or_missing_file(tmp_path):
    d = tmp_path / "x"; d.mkdir()
    assert load_palette(d) is None
    (d / "colors.toml").write_text("not [ valid toml")
    assert load_palette(d) is None


def test_load_palette_invalid_accent_with_no_fallback_is_none(tmp_path):
    d = _theme(tmp_path, "bad-accent", 'accent = "light blue"\nred = "#f85525"\n')
    assert load_palette(d) is None


def test_load_palette_invalid_value_skipped_fallback_chain_continues(tmp_path):
    d = _theme(tmp_path, "fallback",
               'accent = "#112233"\nred = "nope"\ncolor1 = "#aa0000"\n')
    pal = load_palette(d)
    assert pal["red"] == "aa0000"


def test_load_palette_invalid_utf8_returns_none(tmp_path):
    d = tmp_path / "badutf8"; d.mkdir()
    (d / "colors.toml").write_bytes(b'accent = "#112233"\n\xff\xfe invalid utf8')
    assert load_palette(d) is None
