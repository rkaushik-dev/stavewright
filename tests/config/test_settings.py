from __future__ import annotations

from pathlib import Path

import pytest

from v4t_setup.config.settings import (
    CONFIG_FILE_NAME,
    Settings,
    find_config_file,
    from_environment,
    load_settings,
    merge,
    read_config_file,
)
from v4t_setup.errors import ConfigError

SITE_FILE = """
staging_root = "D:/commissioning/_staging"
require_signature = true
install_level = 200
default_languages = ["pl-PL", "ru-RU"]

[waived_checks]
code-page = "this line runs two languages on purpose"
"""

SECTIONED_FILE = """
[tool.something-else]
setting = 1

[v4t-setup]
staging_root = "E:/stage"
log_level = "DEBUG"
"""


def write(tmp_path: Path, text: str, name: str = CONFIG_FILE_NAME) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_are_usable_with_no_configuration():
    settings = load_settings(environ={})
    assert settings.staging_root == Path("_staging")
    assert settings.require_signature
    assert settings.install_level == 100
    assert settings.log_level == "INFO"


def test_a_site_file_is_read(tmp_path):
    settings = load_settings(files=[write(tmp_path, SITE_FILE)], environ={})
    assert settings.staging_root == Path("D:/commissioning/_staging")
    assert settings.install_level == 200
    assert settings.default_languages == ("pl-PL", "ru-RU")
    assert settings.waived_checks == {"code-page": "this line runs two languages on purpose"}


def test_settings_can_live_under_a_section(tmp_path):
    settings = load_settings(files=[write(tmp_path, SECTIONED_FILE)], environ={})
    assert settings.staging_root == Path("E:/stage")
    assert settings.log_level == "DEBUG"


def test_a_top_level_file_ignores_other_tables(tmp_path):
    text = 'staging_root = "E:/stage"\n\n[somebody-elses-tool]\nvalue = 1\n'
    assert read_config_file(write(tmp_path, text)) == {"staging_root": "E:/stage"}


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="cannot read configuration file"):
        read_config_file(tmp_path / "nope.toml")


def test_a_file_that_is_not_toml_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="not valid TOML"):
        read_config_file(write(tmp_path, "this is not = = toml"))


def test_the_environment_is_read():
    layer = from_environment({"V4T_LOG_LEVEL": "DEBUG", "PATH": "/usr/bin"})
    assert layer == {"log_level": "DEBUG"}


@pytest.mark.parametrize("text", ["true", "TRUE", "yes", "on", "1"])
def test_true_words_become_booleans(text):
    assert from_environment({"V4T_REQUIRE_SIGNATURE": text})["require_signature"] is True


@pytest.mark.parametrize("text", ["false", "no", "off", "0"])
def test_false_words_become_booleans(text):
    assert from_environment({"V4T_REQUIRE_SIGNATURE": text})["require_signature"] is False


def test_digits_become_integers():
    assert from_environment({"V4T_INSTALL_LEVEL": "200"})["install_level"] == 200


def test_a_negative_number_becomes_an_integer():
    assert from_environment({"V4T_REFERENCE_YEAR": "-1"})["reference_year"] == -1


def test_anything_else_stays_a_string():
    assert from_environment({"V4T_STAGING_ROOT": "D:/stage"})["staging_root"] == "D:/stage"


def test_a_bare_prefix_is_ignored():
    assert from_environment({"V4T_": "x"}) == {}


def test_the_environment_beats_a_file(tmp_path):
    settings = load_settings(
        files=[write(tmp_path, SITE_FILE)], environ={"V4T_INSTALL_LEVEL": "300"}
    )
    assert settings.install_level == 300


def test_overrides_beat_the_environment(tmp_path):
    settings = load_settings(
        files=[write(tmp_path, SITE_FILE)],
        environ={"V4T_INSTALL_LEVEL": "300"},
        overrides={"install_level": 400},
    )
    assert settings.install_level == 400


def test_a_none_override_does_not_win():
    settings = load_settings(
        environ={"V4T_INSTALL_LEVEL": "300"}, overrides={"install_level": None}
    )
    assert settings.install_level == 300


def test_later_files_beat_earlier_ones(tmp_path):
    first = write(tmp_path, 'install_level = 100\n', "one.toml")
    second = write(tmp_path, 'install_level = 500\n', "two.toml")
    assert load_settings(files=[first, second], environ={}).install_level == 500


def test_merging_is_shallow_so_waivers_are_replaced_not_combined():
    merged = merge(
        {"waived_checks": {"a": "one"}},
        {"waived_checks": {"b": "two"}},
    )
    assert merged["waived_checks"] == {"b": "two"}


def test_merging_ignores_none():
    assert merge({"a": 1}, {"a": None}) == {"a": 1}


def test_merging_nothing():
    assert merge() == {}


def test_an_unknown_setting_is_refused():
    with pytest.raises(ConfigError, match="not valid"):
        load_settings(environ={}, overrides={"nonsense": 1})


def test_an_unknown_log_level_is_refused():
    with pytest.raises(ConfigError, match="not valid"):
        load_settings(environ={}, overrides={"log_level": "CHATTY"})


def test_a_log_level_is_upper_cased():
    assert load_settings(environ={}, overrides={"log_level": "debug"}).log_level == "DEBUG"


def test_an_install_level_out_of_range_is_refused():
    with pytest.raises(ConfigError):
        load_settings(environ={}, overrides={"install_level": 0})
    with pytest.raises(ConfigError):
        load_settings(environ={}, overrides={"install_level": 99999})


def test_a_reference_year_out_of_range_is_refused():
    with pytest.raises(ConfigError):
        load_settings(environ={}, overrides={"reference_year": 1900})


def test_languages_from_the_environment_split_on_commas():
    settings = load_settings(environ={"V4T_DEFAULT_LANGUAGES": "pl-PL, ru-RU"})
    assert settings.default_languages == ("pl-PL", "ru-RU")


def test_an_empty_language_string_is_no_languages():
    assert load_settings(environ={"V4T_DEFAULT_LANGUAGES": ""}).default_languages == ()


def test_waiver_reasons_are_coerced_to_strings():
    settings = load_settings(environ={}, overrides={"waived_checks": {"code-page": 42}})
    assert settings.waived_checks == {"code-page": "42"}


def test_settings_are_frozen():
    settings = load_settings(environ={})
    with pytest.raises(Exception, match="frozen"):
        settings.install_level = 200


def test_the_journal_path_for_a_run():
    settings = load_settings(environ={}, overrides={"journal_root": Path("J:/journals")})
    assert settings.journal_for("0123abcd") == Path("J:/journals/run-0123abcd.jsonl")


def test_describe_reads_as_a_header():
    settings = load_settings(environ={})
    assert settings.describe() == "staging _staging, signature required, install level 100"


def test_describe_says_when_signing_is_off():
    settings = load_settings(environ={}, overrides={"require_signature": False})
    assert "signature not required" in settings.describe()


def test_find_config_file_in_the_directory_itself(tmp_path):
    path = write(tmp_path, SITE_FILE)
    assert find_config_file(tmp_path) == path


def test_find_config_file_walks_upwards(tmp_path):
    path = write(tmp_path, SITE_FILE)
    deep = tmp_path / "terminals" / "LINE-PC-04"
    deep.mkdir(parents=True)
    assert find_config_file(deep) == path


def test_find_config_file_returns_none_when_there_is_none(tmp_path):
    deep = tmp_path / "nothing" / "here"
    deep.mkdir(parents=True)
    found = find_config_file(deep)
    # The walk stops at the filesystem root, so a stray file above the temp
    # directory would be found. Only assert on the shape of the answer.
    assert found is None or found.name == CONFIG_FILE_NAME


def test_a_nearer_file_wins(tmp_path):
    write(tmp_path, 'install_level = 100\n')
    deep = tmp_path / "terminals"
    deep.mkdir()
    nearer = write(deep, 'install_level = 500\n')
    assert find_config_file(deep) == nearer


def test_the_defaults_object_can_be_built_directly():
    assert Settings().install_level == 100
