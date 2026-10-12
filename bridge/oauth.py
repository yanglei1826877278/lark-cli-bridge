from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .settings import Settings

BASE_SCOPES = ('lark:read', 'lark:write')

def sha(s: str):
    return hashlib.sha256(s.encode()).hexdigest()

def b64url(d: bytes):
    import base64
    return base64.urlsafe_b64encode(d).decode().rstrip('=')

class OAuthStorage:
    def __init__(self, cfg: Settings):
        self.cfg = cfg
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY, redirect_uris TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (
                id TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
                state TEXT NOT NULL, challenge TEXT NOT NULL, resource TEXT NOT NULL,
                scope TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS codes (
                hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL,
                challenge TEXT NOT NULL, resource TEXT NOT NULL, scope TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS tokens (
                hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, resource TEXT NOT NULL,
                scope TEXT NOT NULL, kind TEXT NOT NULL, expires REAL NOT NULL);
            ''')

    def db(self):
        c = sqlite3.connect(str(self.cfg.db_path), timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def is_redirect_safe(self, uri: str):
        try:
            p = urlparse(uri)
            return (p.scheme=='https' and p.hostname in ('chatgpt.com','chat.openai.com')
                    and not p.username and not p.password and not p.fragment and len(uri)<1000)
        except ValueError:
            return False

    def new_client(self, redirect_uris):
        if (not isinstance(redirect_uris,list) or not 1<=len(redirect_uris)<=5
                or any(not isinstance(u,str) or not self.is_redirect_safe(u) for u in redirect_uris)):
            raise ValueError('仅允许 ChatGPT HTTPS 回调地址注册')
        client_id = 'lark-bridge-' + secrets.token_urlsafe(24)
        with self.db() as db:
            db.execute('INSERT INTO clients VALUES (?,?,?)',
                       (client_id,json.dumps(redirect_uris),time.time()))
        return {'client_id':client_id,'client_id_issued_at':int(time.time()),
                'redirect_uris':redirect_uris,'grant_types':['authorization_code','refresh_token'],
                'response_types':['code'],'token_endpoint_auth_method':'none'}

    def get_client(self, client_id, redirect_uri):
        with self.db() as db:
            row = db.execute('SELECT redirect_uris FROM clients WHERE client_id=?',(client_id,)).fetchone()
        return bool(row and redirect_uri in json.loads(row['redirect_uris']))

    def create_pending(self, client_id, redirect_uri, state, challenge, resource, scope):
        token = secrets.token_urlsafe(32)
        with self.db() as db:
            db.execute('INSERT INTO pending VALUES (?,?,?,?,?,?,?,?)',
                       (sha(token),client_id,redirect_uri,state,challenge,resource,scope,time.time()+600))
        return token

    def consume_pending(self, token):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM pending WHERE id=?',(sha(token),)).fetchone()
            db.execute('DELETE FROM pending WHERE id=?',(sha(token),))
        return dict(row) if row and row['expires']>time.time() else None

    def create_code(self, row):
        code = secrets.token_urlsafe(48)
        with self.db() as db:
            db.execute('INSERT INTO codes VALUES (?,?,?,?,?,?,?)',
                       (sha(code),row['client_id'],row['redirect_uri'],row['challenge'],row['resource'],row['scope'],time.time()+300))
        return code

    def exchange_code(self, code, client_id, redirect_uri, verifier, resource):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM codes WHERE hash=?',(sha(code),)).fetchone()
            db.execute('DELETE FROM codes WHERE hash=?',(sha(code),))
        if not row or row['expires']<=time.time() or row['client_id']!=client_id or row['redirect_uri']!=redirect_uri or row['resource']!=resource:
            return None
        if not (43<=len(verifier)<=128) or not hmac.compare_digest(b64url(hashlib.sha256(verifier.encode()).digest()),row['challenge']):
            return None
        return self.issue_tokens(row['client_id'],row['resource'],row['scope'])

    def issue_tokens(self, client_id, resource, scope):
        access, refresh = secrets.token_urlsafe(48),secrets.token_urlsafe(48)
        with self.db() as db:
            db.executemany('INSERT INTO tokens VALUES (?,?,?,?,?,?)',[
                (sha(access),client_id,resource,scope,'access',time.time()+3600),
                (sha(refresh),client_id,resource,scope,'refresh',time.time()+30*86400)])
        return {'access_token':access,'token_type':'Bearer','expires_in':3600,
                'refresh_token':refresh,'scope':scope}

    def refresh(self, refresh_token, client_id, resource):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM tokens WHERE hash=? AND kind=?',(sha(refresh_token),'refresh')).fetchone()
            db.execute('DELETE FROM tokens WHERE hash=?',(sha(refresh_token),))
        if not row or row['expires']<=time.time() or row['client_id']!=client_id or row['resource']!=resource:
            return None
        return self.issue_tokens(row['client_id'],row['resource'],row['scope'])

    def verify(self, token: str, needed: str | None = None):
        with self.db() as db:
            row=db.execute('SELECT * FROM tokens WHERE hash=? AND kind=?',(sha(token),'access')).fetchone()
        if not row or row['expires']<=time.time() or row['resource']!=self.cfg.resource:
            return False
        return needed is None or needed in row['scope'].split()

def oauth_router(cfg: Settings, store: OAuthStorage):
    router = APIRouter()
    scopes = BASE_SCOPES + (('lark:full',) if cfg.permission_mode == 'full' else ())
    metadata = {
        'issuer':cfg.url,'authorization_endpoint':cfg.url+'/authorize',
        'token_endpoint':cfg.url+'/token','registration_endpoint':cfg.url+'/register',
        'grant_types_supported':['authorization_code','refresh_token'],
        'response_types_supported':['code'],'code_challenge_methods_supported':['S256'],
        'token_endpoint_auth_methods_supported':['none'],
        'scopes_supported':list(scopes),'client_id_metadata_document_supported':False,
        'authorization_response_iss_parameter_supported':False,
    }
    resource = {'resource':cfg.resource,'authorization_servers':[cfg.url],
                'scopes_supported':list(scopes),'resource_name':'Personal Lark CLI Bridge'}

    @router.get('/.well-known/oauth-authorization-server')
    async def discovery():
        return JSONResponse(metadata,headers={'Cache-Control':'no-store'})

    @router.get('/.well-known/oauth-protected-resource')
    @router.get('/.well-known/oauth-protected-resource/mcp')
    async def resource_meta():
        return JSONResponse(resource,headers={'Cache-Control':'no-store'})

    @router.post('/register')
    async def register(req: Request):
        data = await req.json()
        if data.get('token_endpoint_auth_method','none')!='none' or data.get('response_types',['code'])!=['code']:
            return JSONResponse({'error':'invalid_client_metadata'},status_code=400)
        try:
            client=store.new_client(data.get('redirect_uris'))
        except ValueError as e:
            return JSONResponse({'error':'invalid_redirect_uri','error_description':str(e)},status_code=400)
        return JSONResponse(client,status_code=201,headers={'Cache-Control':'no-store'})

    @router.get('/authorize',response_class=HTMLResponse)
    async def authorize_get(request: Request):
        q=request.query_params
        cid=q.get('client_id',''); redirect=q.get('redirect_uri','')
        challenge=q.get('code_challenge',''); scope=q.get('scope','lark:read lark:write')
        resource_q=q.get('resource',''); state=q.get('state','')
        if (q.get('response_type')!='code' or q.get('code_challenge_method')!='S256'
            or not 40<=len(challenge)<=100 or not 8<=len(state)<=200
            or resource_q!=cfg.resource or not store.get_client(cid,redirect)
            or not set(scope.split()).issubset(scopes) or not scope.strip()):
            return HTMLResponse('OAuth 参数不合法或客户端未注册',status_code=400)
        pending=store.create_pending(cid,redirect,state,challenge,resource_q,scope)
        from html import escape
        permission_options = ''
        if cfg.permission_mode == 'full':
            permission_options = '''
        <fieldset style="margin:20px 0;padding:14px 16px;border:1px solid #ccc;border-radius:8px">
        <legend><strong>本次连接权限</strong></legend>
        <label style="display:block;margin:8px 0"><input type="radio" name="permission_profile" value="restricted" checked>
        <strong>受限权限</strong> — 仅使用受控的文档、知识库和多维表格工具</label>
        <label style="display:block;margin:8px 0"><input type="radio" name="permission_profile" value="full">
        <strong>完全权限</strong> — 可调用本机 lark-cli 的全部非交互功能，并继承 Bridge 进程的本地文件权限</label>
        </fieldset>'''
        html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>授权飞书 CLI Bridge</title>
        <body style="font:16px system-ui;max-width:480px;margin:12vh auto;padding:20px;line-height:1.6">
        <h2>连接 ChatGPT 与飞书 CLI</h2><p>将允许这个 ChatGPT 插件通过本机 CLI 读取/写入你预先允许的飞书资源。</p>
        <p>请求权限：<code>{escape(scope)}</code></p><form method="post" action="/authorize">
        <input type="hidden" name="pending" value="{escape(pending)}">{permission_options}<label>OWNER_PASSWORD<br>
        <input style="width:100%;padding:8px;box-sizing:border-box" name="password" type="password" required autocomplete="off"></label>
        <p><button style="padding:10px 20px" type="submit">允许连接</button></p></form></body></html>'''
        return HTMLResponse(html,headers={'Cache-Control':'no-store','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer'})

    # In-process limiter for wrong password. For production also use tunnel/reverse-proxy rate limiting.
    failures = {}
    @router.post('/authorize')
    async def authorize_post(request: Request, pending: str=Form(...), password: str=Form(...),
                             permission_profile: str=Form('restricted')):
        ip=request.client.host if request.client else 'unknown'
        now=time.time()
        failed=[t for t in failures.get(ip,[]) if t>now-600]
        if len(failed)>=5:
            return HTMLResponse('尝试次数过多，请十分钟后重试',status_code=429)
        if not hmac.compare_digest(password.encode(),cfg.password.encode()):
            failed.append(now)
            failures[ip]=failed
            return HTMLResponse('密码错误，请返回授权页面重试',status_code=401)
        if permission_profile not in ('restricted','full'):
            return HTMLResponse('权限选项无效，请返回授权页面重试',status_code=400)
        if permission_profile == 'full' and cfg.permission_mode != 'full':
            return HTMLResponse('服务器未开启完全权限模式',status_code=400)
        row=store.consume_pending(pending)
        if not row:
            return HTMLResponse('授权请求已过期，请返回 ChatGPT 重试',status_code=400)
        if permission_profile == 'full':
            # The owner explicitly elevates this connection on the consent page.
            # Include the base scopes so existing fixed tools and the generic CLI
            # tool can all be used with the same access token.
            row['scope']='lark:read lark:write lark:full'
        code=store.create_code(row)
        from urllib.parse import urlencode
        url=row['redirect_uri'] + ('&' if '?' in row['redirect_uri'] else '?') + urlencode({'code':code,'state':row['state']})
        return RedirectResponse(url,status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @router.post('/token')
    async def token(req: Request):
        form=await req.form()
        grant=str(form.get('grant_type',''))
        cid=str(form.get('client_id',''))
        resource_req=str(form.get('resource',''))
        if resource_req!=cfg.resource:
            return JSONResponse({'error':'invalid_target'},status_code=400)
        if grant=='authorization_code':
            tokens=store.exchange_code(str(form.get('code','')),cid,str(form.get('redirect_uri','')),
                                       str(form.get('code_verifier','')),resource_req)
        elif grant=='refresh_token':
            tokens=store.refresh(str(form.get('refresh_token','')),cid,resource_req)
        else:
            return JSONResponse({'error':'unsupported_grant_type'},status_code=400)
        if not tokens:
            return JSONResponse({'error':'invalid_grant'},status_code=400)
        return JSONResponse(tokens,headers={'Cache-Control':'no-store','Pragma':'no-cache'})

    return router
