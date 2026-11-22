from investiq.platform.api import settings

def test_local_http_auth_requires_explicit_opt_in_and_exact_loopback(monkeypatch):
    monkeypatch.setenv('SUPABASE_ANON_KEY','test-key')
    monkeypatch.setenv('SUPABASE_URL','http://127.0.0.1:54321')
    monkeypatch.delenv('INVESTIQ_ALLOW_LOCAL_AUTH',raising=False)
    assert not settings()['auth']
    monkeypatch.setenv('INVESTIQ_ALLOW_LOCAL_AUTH','1')
    assert settings()['auth']
    for url in ('http://example.com:54321', 'http://127.0.0.1:54322', 'http://127.0.0.1:54321.evil.example', 'http://localhost:54321'):
        monkeypatch.setenv('SUPABASE_URL',url)
        assert not settings()['auth']
    monkeypatch.setenv('SUPABASE_URL','https://example.supabase.co')
    assert settings()['auth']
