"""Access control and session lifecycle checks, using an isolated database."""
from fastapi.testclient import TestClient
from app.main import app


def test_workspace_session_lifecycle(monkeypatch):
    monkeypatch.setenv('APP_USERNAME', 'reviewer')
    monkeypatch.setenv('APP_PASSWORD', 'a-long-test-password')
    with TestClient(app) as client:
        assert client.get('/api/health').status_code == 200
        for path in ['/api/rules', '/api/settings', '/api/agent', '/docs', '/openapi.json']:
            assert client.get(path).status_code == 401
        assert client.post('/api/auth/login', json={'username':'reviewer', 'password':'wrong'}).status_code == 401
        assert client.post('/api/auth/login', json={'username':'reviewer', 'password':'a-long-test-password'}, headers={'Origin':'https://untrusted.example'}).status_code == 403
        response = client.post('/api/auth/login', json={'username':'reviewer', 'password':'a-long-test-password'})
        assert response.status_code == 200
        assert 'HttpOnly' in response.headers['set-cookie']
        token = client.cookies.get('commute_session')
        assert client.get('/api/auth/session').json()['authenticated']
        assert client.get('/api/agent').status_code == 200
        assert client.post('/api/auth/logout').status_code == 200
        assert client.get('/api/rules', headers={'Cookie':f'commute_session={token}'}).status_code == 401


def test_unconfigured_workspace_fails_closed(monkeypatch):
    monkeypatch.delenv('APP_PASSWORD', raising=False)
    with TestClient(app) as client:
        assert client.get('/api/auth/session').json()['configured'] is False
        assert client.get('/api/rules').status_code == 503
        assert client.post('/api/auth/login', json={'username':'', 'password':''}).status_code == 503
