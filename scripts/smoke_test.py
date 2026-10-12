"""Local read-only smoke test with FastAPI TestClient, no live Feishu access."""
import os
os.environ.setdefault('MOCK_CLI','1')
os.environ.setdefault('ALLOWED_BASE_TOKENS','basMock001')
os.environ.setdefault('ALLOWED_DOC_TOKENS','docMock001')
from fastapi.testclient import TestClient
from bridge.server import create_app
from bridge.settings import make_settings
from bridge.oauth import b64url
import hashlib

app=create_app(make_settings())
client=TestClient(app)
response=client.get('/health')
assert response.status_code==200
print('Health OK',response.json())
registration=client.post('/register',json={'redirect_uris':['https://chatgpt.com/oauth/test/callback']})
assert registration.status_code==201,registration.text
print('OAuth DCR OK')
metadata=client.get('/.well-known/oauth-authorization-server').json()
assert 'S256' in metadata['code_challenge_methods_supported']
print('OAuth discovery OK')
assert client.post('/mcp',json={'jsonrpc':'2.0','id':1,'method':'tools/list'}).status_code==401
print('Unauthorized request blocked OK')
