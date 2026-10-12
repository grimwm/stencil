"""The installed version against the newest release tag.

The hour is idle time, not a timer that fires while stencil is in use. A
missing stamp counts as idle: the first run of an install is the one that
looks. A checkout is never offered an install, and a failed lookup never
fails the command that happened to start stencil.
"""

from __future__ import annotations

import os

from stencil import update_check


TAGS = """
aaa\trefs/tags/v1.4.0
bbb\trefs/tags/v1.6.0
ccc\trefs/tags/v1.6.0^{}
ddd\trefs/tags/v1.5.0-rc1
eee\trefs/tags/v1.10.0
fff\trefs/tags/v0.45.0
"""


def test_the_highest_release_tag_wins_and_peels_once():
    assert update_check.select_latest(update_check.tag_names(TAGS)) == "1.10.0"


def test_a_release_tag_and_the_installed_version_parse_the_same_way():
    assert update_check.release_key("v1.6.0") == (1, 6, 0)
    assert update_check.release_key("1.6.0") == (1, 6, 0)
    assert update_check.release_key("1.6.0-rc1") is None
    assert update_check.release_key("1.6") is None


def test_missing_or_old_stamp_is_due(tmp_path):
    stamp = tmp_path / "last-used"
    now = 1_000_000.0
    assert update_check.due(stamp, now) is True

    update_check.record_use(stamp, now - update_check.IDLE_SECONDS)
    assert update_check.due(stamp, now) is True

    update_check.record_use(stamp, now - 60)
    assert update_check.due(stamp, now) is False


def _startup(tmp_path, *, now, installed, latest, error=None, tty=False, accept=False, idle_stamp=None):
    stamp = tmp_path / "last-used"
    if idle_stamp is not None:
        update_check.record_use(stamp, idle_stamp)
    fetches = []
    installed_versions = []
    execs = []

    def fetch():
        fetches.append(True)
        return latest, error

    def install(version):
        installed_versions.append(version)
        return True

    update_check.on_startup(
        disabled=False,
        checkout=False,
        stamp=stamp,
        now=now,
        fetch=fetch,
        tty=tty,
        ask=lambda _prompt: "y" if accept else "n",
        install=install,
        reexec=lambda: execs.append(True),
        installed=installed,
    )
    return fetches, installed_versions, execs, stamp


def test_a_recent_use_does_not_look(tmp_path, capsys):
    fetches, installed_versions, execs, _stamp = _startup(
        tmp_path,
        now=1_000_000.0,
        installed="1.6.0",
        latest="9.0.0",
        idle_stamp=1_000_000.0 - 60,
    )

    assert fetches == []
    assert installed_versions == []
    assert execs == []
    assert capsys.readouterr().err == ""


def test_an_idle_hour_with_no_tty_prints_the_notice_and_does_not_install(tmp_path, capsys):
    fetches, installed_versions, execs, stamp = _startup(
        tmp_path,
        now=1_000_000.0,
        installed="1.6.0",
        latest="1.7.0",
    )

    err = capsys.readouterr().err
    assert fetches == [True]
    assert installed_versions == []
    assert execs == []
    assert "1.7.0 is the latest tag" in err
    assert "stencil update" in err
    assert stamp.read_text().strip() == "1.6.0"


def test_an_idle_hour_on_a_tty_installs_only_when_accepted(tmp_path, capsys):
    _fetches, installed_versions, execs, _stamp = _startup(
        tmp_path,
        now=1_000_000.0,
        installed="1.6.0",
        latest="1.7.0",
        tty=True,
        accept=True,
    )

    assert installed_versions == ["1.7.0"]
    assert execs == [True]
    assert "Installed stencil 1.7.0." in capsys.readouterr().err


def test_declining_the_offer_leaves_the_install_alone(tmp_path, capsys):
    _fetches, installed_versions, execs, _stamp = _startup(
        tmp_path,
        now=1_000_000.0,
        installed="1.6.0",
        latest="1.7.0",
        tty=True,
        accept=False,
    )

    assert installed_versions == []
    assert execs == []
    assert capsys.readouterr().err.endswith("[y/N] ")


def test_the_current_tag_and_a_failed_lookup_say_nothing(tmp_path, capsys):
    _startup(tmp_path, now=1_000_000.0, installed="1.6.0", latest="1.6.0")
    assert capsys.readouterr().err == ""

    _startup(
        tmp_path,
        now=2_000_000.0,
        installed="1.6.0",
        latest=None,
        error="could not list stencil tags: timed out",
    )
    assert capsys.readouterr().err == ""


def test_a_checkout_and_the_disable_switch_do_not_look(tmp_path):
    fetches = []

    def fetch():
        fetches.append(True)
        return "9.0.0", None

    update_check.on_startup(
        disabled=False,
        checkout=True,
        stamp=tmp_path / "a",
        now=1.0,
        fetch=fetch,
        installed="1.0.0",
    )
    update_check.on_startup(
        disabled=True,
        checkout=False,
        stamp=tmp_path / "b",
        now=1.0,
        fetch=fetch,
        installed="1.0.0",
    )

    assert fetches == []


def test_update_installs_when_told_yes(tmp_path, capsys):
    installed_versions = []
    code = update_check.command(
        yes=True,
        checkout=False,
        stamp=tmp_path / "last-used",
        now=1.0,
        fetch=lambda: ("1.7.0", None),
        install=lambda version: installed_versions.append(version) or True,
        installed="1.6.0",
    )

    assert code == 0
    assert installed_versions == ["1.7.0"]
    assert "Installed stencil 1.7.0." in capsys.readouterr().err


def test_update_reports_current_ahead_and_unreachable(tmp_path, capsys):
    assert (
        update_check.command(
            checkout=False,
            stamp=tmp_path / "a",
            now=1.0,
            fetch=lambda: ("1.6.0", None),
            installed="1.6.0",
        )
        == 0
    )
    assert "is the latest" in capsys.readouterr().err

    assert (
        update_check.command(
            checkout=False,
            stamp=tmp_path / "b",
            now=1.0,
            fetch=lambda: ("1.6.0", None),
            installed="1.8.0",
        )
        == 0
    )
    assert "the latest tag is 1.6.0" in capsys.readouterr().err

    assert (
        update_check.command(
            checkout=False,
            stamp=tmp_path / "c",
            now=1.0,
            fetch=lambda: (None, "could not list stencil tags: down"),
            installed="1.6.0",
        )
        == 1
    )
    assert "could not list stencil tags: down" in capsys.readouterr().err


def test_update_refuses_a_checkout(tmp_path, capsys):
    code = update_check.command(
        yes=True,
        checkout=True,
        stamp=tmp_path / "last-used",
        now=1.0,
        fetch=lambda: (_ for _ in ()).throw(AssertionError("should not fetch")),
        installed="1.6.0",
    )

    assert code == 0
    assert "source checkout" in capsys.readouterr().err


def test_fetch_latest_reads_ls_remote_output():
    class Proc:
        returncode = 0
        stdout = TAGS
        stderr = ""

    version, error = update_check.fetch_latest(run=lambda _args, timeout: Proc())

    assert error is None
    assert version == "1.10.0"


def test_cli_update_is_a_command(tmp_path):
    """`--help` exits in argparse, before the lookup, so this stays offline."""
    from tests.test_cli import run_cli

    result = run_cli("update", "--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "--yes" in result.stdout
    assert os.environ.get("STENCIL_NO_UPDATE_CHECK") == "1"
