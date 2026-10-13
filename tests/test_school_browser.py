from pathlib import Path

import pytest

from test_connections import context


def test_moodle_login_uses_school_browser_instead_of_default_proxy(tmp_path, monkeypatch):
    from academic_os import api
    _, service, client, headers = context(tmp_path)
    monkeypatch.setattr(service, 'register_moodle_scheme', lambda: None)
    opened = []
    monkeypatch.setattr(api, 'open_school_login', lambda url, directory: opened.append((url, directory)))
    result = client.post('/api/v1/connections/moodle/authorize', json={'open_browser': True}, headers=headers)
    assert result.status_code == 200
    assert opened[0][0] == result.json()['url']
    assert opened[0][1] == service.core.settings.data_dir


def test_school_window_has_own_profile_and_direct_network(tmp_path, monkeypatch):
    from academic_os import school_browser
    browser = tmp_path / 'Program Files' / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'
    browser.parent.mkdir(parents=True)
    browser.touch()
    monkeypatch.setenv('PROGRAMFILES', str(tmp_path / 'Program Files'))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setattr(school_browser.sys, 'platform', 'win32')
    calls = []
    monkeypatch.setattr(school_browser.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)))
    url = 'https://buelearning.hkbu.edu.hk/admin/tool/mobile/launch.php?passport=one-time-flow'
    school_browser.open_school_login(url, tmp_path / 'data')
    args, kwargs = calls[0]
    assert args[0] == str(browser)
    assert '--no-proxy-server' in args
    assert '--new-window' in args
    profile = next(x.split('=', 1)[1] for x in args if x.startswith('--user-data-dir='))
    assert Path(profile).is_dir()
    assert Path(profile).is_relative_to(tmp_path / 'local' / 'AcademicOS')
    assert args[-1] == url
    assert not kwargs.get('shell', False)
    assert not any('ignore-certificate' in x or 'disable-web-security' in x for x in args)


@pytest.mark.parametrize('url', ['http://school.example/', 'https://user:secret@school.example/'])
def test_school_browser_rejects_unsafe_login_urls(tmp_path, url):
    from academic_os.school_browser import open_school_login
    with pytest.raises(ValueError):
        open_school_login(url, tmp_path)
