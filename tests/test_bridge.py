import base64
import asyncio
import json
import hashlib
import threading
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
import httpx
from bridge.settings import Settings
from bridge.server import create_app

VERIFIER='v'*50
CHALLENGE=base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip('=')


def client_pair(tmp_path, permission_mode='restricted'):
    cfg=Settings(url='http://127.0.0.1:8765',host='127.0.0.1',port=8765,
                 password='local-test-private-password',mock=True,cli_path='',
                 bases=frozenset({'basMock001'}),docs=frozenset({'docMock001'}),
                 db_path=tmp_path/'test.sqlite3',timeout=10,
                 permission_mode=permission_mode)
    return TestClient(create_app(cfg)),cfg


def grant(client, scope='lark:read lark:write', permission_profile='restricted'):
    callback='https://chatgpt.com/oauth/test/callback'
    r=client.post('/register',json={'redirect_uris':[callback]})
    assert r.status_code==201,r.text
    clientid=r.json()['client_id']
    assert client.post('/register',json={'redirect_uris':['https://evil.example/cb']}).status_code==400
    r=client.get('/authorize',params={
        'client_id':clientid,'redirect_uri':callback,'response_type':'code',
        'code_challenge':CHALLENGE,'code_challenge_method':'S256','state':'random-state-value',
        'resource':'http://127.0.0.1:8765/mcp','scope':scope})
    assert r.status_code==200,r.text
    import re
    pending=re.search(r'name="pending" value="([^"]+)"',r.text).group(1)
    assert client.post('/authorize',data={'pending':pending,'password':'wrong'}).status_code==401
    r=client.post('/authorize',data={'pending':pending,'password':'local-test-private-password',
                                     'permission_profile':permission_profile},follow_redirects=False)
    assert r.status_code==303
    url=urlparse(r.headers['location'])
    assert url.hostname=='chatgpt.com'
    assert parse_qs(url.query)['state']==['random-state-value']
    code=parse_qs(url.query)['code'][0]
    t=client.post('/token',data={'grant_type':'authorization_code','code':code,'client_id':clientid,
                                 'redirect_uri':callback,'code_verifier':VERIFIER,
                                 'resource':'http://127.0.0.1:8765/mcp'})
    assert t.status_code==200,t.text
    assert client.post('/token',data={'grant_type':'authorization_code','code':code,'client_id':clientid,
                                 'redirect_uri':callback,'code_verifier':VERIFIER,
                                 'resource':'http://127.0.0.1:8765/mcp'}).status_code==400
    return clientid,t.json()


def rpc(client,token,method,params=None):
    return client.post('/mcp',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                                      'Accept':'application/json, text/event-stream'},
                       json={'jsonrpc':'2.0','id':42,'method':method,'params':params or {}})


def test_full_oauth_mcp_and_mock_cli(tmp_path):
    client,cfg=client_pair(tmp_path)
    assert client.get('/health').json()['ok']
    assert client.get('/.well-known/oauth-protected-resource/mcp').json()['resource']==cfg.resource
    assert client.get('/.well-known/oauth-authorization-server').json()['code_challenge_methods_supported']==['S256']
    assert client.post('/mcp',json={'jsonrpc':'2.0','id':42,'method':'tools/list'}).status_code==401
    cid,tokens=grant(client)
    token=tokens['access_token']
    assert rpc(client,token,'initialize').json()['result']['capabilities']['tools']
    listing=rpc(client,token,'tools/list')
    assert listing.status_code==200
    tools=listing.json()['result']['tools']
    assert len(tools)==9
    assert all(t['securitySchemes'] for t in tools)
    result=rpc(client,token,'tools/call',{'name':'list_wiki_spaces','arguments':{}}).json()['result']
    assert result['isError'] is False
    assert 'mock-space-1' in result['content'][0]['text']
    personal=rpc(client,token,'tools/call',{'name':'list_personal_documents','arguments':{}}).json()['result']
    assert 'mock-personal-doc' in personal['content'][0]['text']
    assert 'run_lark_cli' not in {tool['name'] for tool in tools}
    denied=rpc(client,token,'tools/call',{'name':'create_base_record','arguments':{'base_token':'nope','table_id':'tblx','fields':{'Name':'Test'}}}).json()
    assert denied['result']['isError'] is True
    created=rpc(client,token,'tools/call',{'name':'create_base_record','arguments':{'base_token':'basMock001','table_id':'tblMockKeywords','fields':{'关键词':'AI Sprite Generator'}}}).json()
    assert created['result']['isError'] is False
    assert 'AI Sprite Generator' in created['result']['content'][0]['text']
    doc=rpc(client,token,'tools/call',{'name':'append_document','arguments':{'doc':'docMock001','content':'今天的灵感'}}).json()
    assert doc['result']['isError'] is False
    literal=rpc(client,token,'tools/call',{'name':'append_document','arguments':{'doc':'docMock001','content':'@.env'}}).json()['result']
    assert literal['isError'] is False
    assert json.loads(literal['content'][0]['text'])['data']['appended']=='@.env'
    made=rpc(client,token,'tools/call',{'name':'create_document',
        'arguments':{'title':'分镜脚本','content':'@.env'}}).json()['result']
    assert made['isError'] is False
    created_doc=json.loads(made['content'][0]['text'])['data']['document']
    assert created_doc['content']=='@.env'
    assert created_doc['document_id']=='docMockNew001'
    followup=rpc(client,token,'tools/call',{'name':'append_document',
        'arguments':{'doc':'docMockNew001','content':'后续内容'}}).json()['result']
    assert followup['isError'] is False
    restarted=TestClient(create_app(cfg))
    reopened=rpc(restarted,token,'tools/call',{'name':'read_document',
        'arguments':{'doc':'docMockNew001'}}).json()['result']
    assert reopened['isError'] is False
    clientid,new_tokens=cid,tokens
    r=client.post('/token',data={'grant_type':'refresh_token','refresh_token':new_tokens['refresh_token'],
                                 'client_id':clientid,'resource':cfg.resource})
    assert r.status_code==200
    assert client.post('/token',data={'grant_type':'refresh_token','refresh_token':new_tokens['refresh_token'],
                                 'client_id':clientid,'resource':cfg.resource}).status_code==400


def test_command_rejection(tmp_path):
    client,cfg=client_pair(tmp_path)
    _,t=grant(client)
    r=rpc(client,t['access_token'],'tools/call',{'name':'list_base_records','arguments':{'base_token':'basMock001','table_id':'tblOK','limit':1000}}).json()
    assert r['result']['isError']
    r=rpc(client,t['access_token'],'tools/call',{'name':'append_document','arguments':{'doc':'https://evil.example/doc','content':'x'}}).json()
    assert r['result']['isError']
    r=rpc(client,t['access_token'],'tools/call',{'name':'list_wiki_spaces','arguments':{'unapproved':'whoami'}}).json()
    assert r['result']['isError']
    assert client.get('/mcp').status_code==401


def test_read_only_token_cannot_write(tmp_path):
    client,cfg=client_pair(tmp_path)
    _,tokens=grant(client)
    # Issue a separate user-authorized token restricted to read scope.
    read_token=client.app.state.oauth.issue_tokens('read-only-client',cfg.resource,'lark:read')['access_token']
    assert rpc(client,read_token,'tools/list').status_code==200
    denied=rpc(client,read_token,'tools/call',{'name':'create_base_record','arguments':{'base_token':'basMock001','table_id':'tblMockKeywords','fields':{'关键词':'test'}}})
    assert denied.status_code==403
    assert 'insufficient_scope' in denied.headers['www-authenticate']


def test_mcp_origin_validation(tmp_path):
    client,cfg=client_pair(tmp_path)
    _,tokens=grant(client)
    headers={'Authorization':'Bearer '+tokens['access_token']}
    blocked=client.post('/mcp',headers={**headers,'Origin':'https://evil.example'},
                        json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
    assert blocked.status_code==403
    accepted=client.post('/mcp',headers={**headers,'Origin':'https://chatgpt.com'},
                         json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
    assert accepted.status_code==200


def test_slow_cli_does_not_block_other_requests(tmp_path):
    client,cfg=client_pair(tmp_path)
    app=client.app
    token=app.state.oauth.issue_tokens('test-client',cfg.resource,'lark:read')['access_token']
    started=threading.Event()
    release=threading.Event()

    def slow_invoke(name,args,**kwargs):
        started.set()
        assert release.wait(2)
        return {'ok':True}

    app.state.tools.invoke=slow_invoke

    async def check():
        transport=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,base_url=cfg.url) as async_client:
            call=asyncio.create_task(async_client.post('/mcp',
                headers={'Authorization':'Bearer '+token},
                json={'jsonrpc':'2.0','id':1,'method':'tools/call',
                      'params':{'name':'list_wiki_spaces','arguments':{}}}))
            try:
                assert await asyncio.to_thread(started.wait,1)
                health=await asyncio.wait_for(async_client.get('/health'),timeout=0.5)
                assert health.status_code==200
            finally:
                release.set()
                await call

    asyncio.run(check())


def test_full_mode_requires_separate_oauth_scope(tmp_path):
    client,cfg=client_pair(tmp_path,permission_mode='full')
    assert 'lark:full' in client.get('/.well-known/oauth-authorization-server').json()['scopes_supported']
    _,regular=grant(client)
    listing=rpc(client,regular['access_token'],'tools/list').json()['result']['tools']
    assert 'run_lark_cli' in {tool['name'] for tool in listing}
    assert next(tool for tool in listing if tool['name']=='run_lark_cli')['annotations']['destructiveHint'] is True
    denied=rpc(client,regular['access_token'],'tools/call',
               {'name':'run_lark_cli','arguments':{'argv':['--help']}})
    assert denied.status_code==403
    assert 'lark:full' in denied.headers['www-authenticate']
    _,full=grant(client,permission_profile='full')
    assert set(full['scope'].split()) == {'lark:read','lark:write','lark:full'}
    full_token=full['access_token']
    # Full OAuth permission also lifts the fixed tools' resource allowlists.
    unrestricted_doc=rpc(client,full_token,'tools/call',
        {'name':'read_document','arguments':{'doc':'docOutsideAllowlist'}}).json()['result']
    assert unrestricted_doc['isError'] is False
    unrestricted_base=rpc(client,full_token,'tools/call',
        {'name':'list_base_tables','arguments':{'base_token':'basOutsideAllowlist'}}).json()['result']
    assert unrestricted_base['isError'] is False
    help_result=rpc(client,full_token,'tools/call',
                    {'name':'run_lark_cli','arguments':{'argv':['--help']}}).json()['result']
    assert help_result['isError'] is False
    assert 'Mock lark-cli help' in help_result['content'][0]['text']
    command=rpc(client,full_token,'tools/call',
                {'name':'run_lark_cli','arguments':{'argv':['wiki','+space-list','--as','user','--format','json']}}).json()['result']
    assert command['isError'] is False
    assert 'mock-space-1' in command['content'][0]['text']
