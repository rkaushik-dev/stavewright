from __future__ import annotations

import pytest

from stavewright.config.toml import load_toml, parse_toml
from stavewright.errors import ConfigError


def test_reading_a_file(tmp_path):
    path = tmp_path / "thing.toml"
    path.write_text('name = "S-Jet 600"\ncount = 4\n', encoding="utf-8")
    assert load_toml(path, what="thing file") == {"name": "S-Jet 600", "count": 4}


def test_an_empty_file_is_an_empty_document(tmp_path):
    path = tmp_path / "thing.toml"
    path.write_text("", encoding="utf-8")
    assert load_toml(path, what="thing file") == {}


def test_a_missing_file_uses_the_caller_wording(tmp_path):
    with pytest.raises(ConfigError, match="cannot read roster file"):
        load_toml(tmp_path / "gone.toml", what="roster file")


def test_a_directory_is_refused_like_a_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read profile file"):
        load_toml(tmp_path, what="profile file")


def test_the_path_travels_with_a_read_failure(tmp_path):
    path = tmp_path / "gone.toml"
    with pytest.raises(ConfigError) as caught:
        load_toml(path, what="thing file")
    assert caught.value.context["path"] == str(path)
    assert caught.value.context["detail"]


def test_broken_toml_uses_the_caller_wording(tmp_path):
    path = tmp_path / "thing.toml"
    path.write_text("name = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="configuration file is not valid TOML"):
        load_toml(path, what="configuration file")


def test_the_path_travels_with_a_parse_failure(tmp_path):
    path = tmp_path / "thing.toml"
    path.write_text("[unclosed", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_toml(path, what="thing file")
    assert caught.value.context["path"] == str(path)


def test_bytes_that_are_not_utf8_are_refused(tmp_path):
    path = tmp_path / "thing.toml"
    path.write_bytes(b'name = "\xff\xfe"\n')
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_toml(path, what="thing file")


def test_a_utf8_bom_is_refused_rather_than_silently_eaten(tmp_path):
    # tomllib rejects it, and pretending otherwise would mean a file that
    # loads on the bench and not in CI.
    path = tmp_path / "thing.toml"
    path.write_bytes(b"\xef\xbb\xbfname = \"x\"\n")
    with pytest.raises(ConfigError):
        load_toml(path, what="thing file")


def test_parsing_bytes_in_memory():
    assert parse_toml(b"a = 1", what="bundle settings", where="bundle") == {"a": 1}


def test_parsing_bytes_names_where_they_came_from():
    with pytest.raises(ConfigError) as caught:
        parse_toml(b"a = ", what="bundle settings", where="bundle:/settings.toml")
    assert caught.value.context["path"] == "bundle:/settings.toml"


def test_nested_tables_survive():
    document = b"[line.export]\ncriticality = 'critical'\n"
    parsed = parse_toml(document, what="roster file", where="x")
    assert parsed["line"]["export"]["criticality"] == "critical"
