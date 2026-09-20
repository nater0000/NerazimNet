"""Tests for OS service registration artifacts (no real registration)."""
import importlib


def _svc():
    return importlib.import_module('utils.services')


def test_daemon_command_ends_with_daemon_flag():
    cmd = _svc().get_daemon_command()
    assert cmd[-1] == '--daemon'
    assert cmd[0]  # executable present


def test_windows_task_xml():
    xml = _svc()._win_task_xml(['/bin/x', '--daemon'])
    assert 'LogonTrigger' in xml
    assert 'RestartOnFailure' in xml
    assert '<Command>/bin/x</Command>' in xml
    assert '<Arguments>"--daemon"</Arguments>' in xml
    assert 'InteractiveToken' in xml  # own-user task, no admin needed


def test_linux_unit_text():
    unit = _svc()._linux_unit_text()
    assert 'Restart=on-failure' in unit
    assert 'WantedBy=default.target' in unit
    assert '--daemon' in unit


def test_macos_plist_dict():
    plist = _svc()._mac_plist_dict()
    assert plist['Label'] == 'com.nerazimnet.daemon'
    assert plist['RunAtLoad'] is True
    assert plist['KeepAlive'] == {'SuccessfulExit': False}
    assert plist['ProgramArguments'][-1] == '--daemon'
