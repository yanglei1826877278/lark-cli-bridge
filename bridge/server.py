from __future__ import annotations
import asyncio
import json
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from .settings import Settings, make_settings
from .oauth import OAuthStorage, oauth_router
from .tools import ToolRegistry

SUPPORTED_PROTOCOL='2025-06-18'


def create_app(settings: Settings | None = None):
    cfg=settings or make_settings()
    app=FastAPI(title='Personal Lark CLI MCP Bridge',docs_url=None,redoc_url=None,openapi_url=None)
    store=OAuthStorage(cfg)
    tools=ToolRegistry(cfg)
    cli_slots=asyncio.Semaphore(4)
    app.include_router(oauth_router(cfg,store))
    app.state.settings=cfg
    app.state.oauth=store
    app.state.tools=tools

    @app.get('/health')
    async def health():
        return {'ok':True,'service':'lark-cli-bridge','mock':cfg.mock,
                'permission_mode':cfg.permission_mode}

    def challenge(status=401, scope='lark:read lark:write'):
        error = 'insufficient_scope' if status == 403 else 'invalid_token'
        return JSONResponse({'error':error},status_code=status,headers={
            'WWW-Authenticate':f'Bearer resource_metadata="{cfg.url}/.well-known/oauth-protected-resource/mcp", error="{error}", error_description="Authorize Lark CLI Bridge in ChatGPT", scope="{scope}"',
            'Cache-Control':'no-store'})

    @app.api_route('/mcp',methods=['POST','GET','DELETE'])
    async def mcp(req: Request):
        origin=req.headers.get('origin')
        if origin and origin not in (cfg.url, 'https://chatgpt.com', 'https://chat.openai.com'):
            return JSONResponse({'error':'invalid_origin'},status_code=403)
        auth=req.headers.get('authorization','')
        if not auth.startswith('Bearer ') or not store.verify(auth[7:]):
            return challenge()
        if req.method!='POST':
            return Response(status_code=405,headers={'Allow':'POST','Cache-Control':'no-store'})
        if 'application/json' not in req.headers.get('content-type',''):
            return JSONResponse({'error':'expected JSON'},status_code=415)
        try:
            data=await req.json()
        except Exception:
            return JSONResponse({'error':'malformed JSON'},status_code=400)
        if not isinstance(data,dict) or data.get('jsonrpc')!='2.0':
            return JSONResponse({'error':'invalid JSON-RPC message'},status_code=400)
        method=data.get('method'); rid=data.get('id',None)
        if rid is None:  # JSON-RPC notification, no response
            return Response(status_code=202)
        def ok(result):
            return JSONResponse({'jsonrpc':'2.0','id':rid,'result':result},headers={'Cache-Control':'no-store','Mcp-Protocol-Version':SUPPORTED_PROTOCOL})
        def error(code,message):
            return JSONResponse({'jsonrpc':'2.0','id':rid,'error':{'code':code,'message':message}},headers={'Cache-Control':'no-store'})
        if method=='initialize':
            return ok({'protocolVersion':SUPPORTED_PROTOCOL,'capabilities':{'tools':{'listChanged':False}},
                       'serverInfo':{'name':'lark-cli-bridge','version':'0.1.0'}})
        if method=='ping':
            return ok({})
        if method=='tools/list':
            return ok({'tools':tools.definitions()})
        if method=='tools/call':
            params=data.get('params') or {}
            name=params.get('name') if isinstance(params,dict) else None
            args=params.get('arguments',{}) if isinstance(params,dict) else {}
            descriptor=tools.by_name.get(name)
            if not descriptor:
                return error(-32602,'未知工具')
            needed_scope = descriptor.get('scope') or ('lark:write' if descriptor['write'] else 'lark:read')
            if not store.verify(auth[7:],needed_scope):
                return challenge(403,needed_scope)
            try:
                async with cli_slots:
                    result=await run_in_threadpool(tools.invoke,name,args)
                failed=isinstance(result,dict) and result.get('exit_code',0)!=0
                return ok({'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False)}], 'isError':failed})
            except Exception as ex:
                # Return MCP tool error rather than crash, but don't expose sensitive tracebacks.
                from .cli import CommandError
                if isinstance(ex,(ValueError,CommandError)):
                    msg=str(ex)
                else:
                    msg='内部错误，请检查服务端日志'
                return ok({'content':[{'type':'text','text':msg}], 'isError':True})
        return error(-32601,'未知 JSON-RPC 方法')

    return app

app=create_app()
