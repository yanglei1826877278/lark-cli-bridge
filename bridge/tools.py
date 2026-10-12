from __future__ import annotations
import json
import sqlite3
from typing import Any
from .settings import Settings
from .cli import CliRunner, CommandError, token_from_document

TOOLS = [
    {'name':'list_wiki_spaces', 'description':'查看当前已授权飞书账号可访问的 Wiki 知识空间。', 'properties':{'page_size':{'type':'integer','minimum':1,'maximum':50,'default':10}}, 'required':[], 'write':False},
    {'name':'list_personal_documents', 'description':'列出个人文档库根目录的文档、表格和多维表格；返回分页信息。', 'properties':{'page_size':{'type':'integer','minimum':1,'maximum':50,'default':50},'page_token':{'type':'string'}}, 'required':[], 'write':False},
    {'name':'list_base_tables', 'description':'查看指定飞书多维表格下的数据表；受限权限连接只能访问预先允许的 Base。', 'properties':{'base_token':{'type':'string'}},'required':['base_token'],'write':False},
    {'name':'list_base_fields', 'description':'获取指定多维表格的数据表字段信息，写入前建议先调用。', 'properties':{'base_token':{'type':'string'},'table_id':{'type':'string'}},'required':['base_token','table_id'],'write':False},
    {'name':'list_base_records', 'description':'读取指定飞书多维表格中最新一页记录。', 'properties':{'base_token':{'type':'string'},'table_id':{'type':'string'},'limit':{'type':'integer','minimum':1,'maximum':100,'default':20}},'required':['base_token','table_id'],'write':False},
    {'name':'create_base_record', 'description':'向指定飞书多维表格新建一条记录。fields 对象的键必须与该表字段名一致；绝不猜测 SEO 指标。调用前核对目标与字段。','properties':{'base_token':{'type':'string'},'table_id':{'type':'string'},'fields':{'type':'object','additionalProperties':True}},'required':['base_token','table_id','fields'],'write':True},
    {'name':'read_document', 'description':'读取飞书云文档；受限权限连接只能读取允许列表或本 Bridge 创建的文档。', 'properties':{'doc':{'type':'string'}},'required':['doc'],'write':False},
    {'name':'append_document', 'description':'向飞书云文档末尾追加 Markdown；受限权限连接只能操作允许列表或本 Bridge 创建的文档。仅在明确要求保存时调用。','properties':{'doc':{'type':'string'},'content':{'type':'string','maxLength':8000}},'required':['doc','content'],'write':True},
    {'name':'create_document', 'description':'在当前用户的个人文档库中新建飞书云文档；可同时写入完整 Markdown 正文。新文档自动允许后续读取和追加。', 'properties':{'title':{'type':'string','maxLength':200},'content':{'type':'string','maxLength':50000}},'required':['title'],'write':True},
]

FULL_TOOL = {
    'name':'run_lark_cli',
    'description':'完全权限模式：调用本机 lark-cli 的非交互命令。用 argv 数组传入 CLI 参数，不包含程序名；先用 ["--help"]、["<领域>","--help"]、["schema","<接口>"] 或 ["skills","read","<技能>"] 发现用法。需要 stdin 时传 stdin 字段。CLI 的本地文件访问和写操作权限与运行 Bridge 的 Windows 用户相同。',
    'properties':{
        'argv':{'type':'array','items':{'type':'string'},'minItems':1,'maxItems':60},
        'stdin':{'type':'string','maxLength':100000},
        'timeout_seconds':{'type':'integer','minimum':1,'maximum':120},
    },
    'required':['argv'],'write':True,'scope':'lark:full',
}

class ToolRegistry:
    def __init__(self, config: Settings):
        self.cfg = config
        self.runner = CliRunner(config)
        self.available_tools = TOOLS + ([FULL_TOOL] if config.permission_mode == 'full' else [])
        self.by_name = {t['name']:t for t in self.available_tools}
        with sqlite3.connect(str(config.db_path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS bridge_created_docs (document_id TEXT PRIMARY KEY)')

    def document_allowed(self, document_id: str):
        if document_id in self.cfg.docs:
            return True
        with sqlite3.connect(str(self.cfg.db_path)) as db:
            return db.execute('SELECT 1 FROM bridge_created_docs WHERE document_id=?',
                              (document_id,)).fetchone() is not None

    def definitions(self):
        output = []
        for t in self.available_tools:
            scope = t.get('scope') or ('lark:write' if t['write'] else 'lark:read')
            output.append({
                'name': t['name'], 'description': t['description'],
                'inputSchema':{'type':'object','properties':t['properties'],'required':t['required'], 'additionalProperties':False},
                'annotations':{'readOnlyHint':not t['write'],
                               'destructiveHint':t['name']=='run_lark_cli',
                               'openWorldHint':True},
                'securitySchemes':[{'type':'oauth2','scopes':[scope]}],
                '_meta':{'securitySchemes':[{'type':'oauth2','scopes':[scope]}]},
            })
        return output

    def invoke(self, name: str, params: Any, *, unrestricted: bool = False):
        if name not in self.by_name:
            raise ValueError('未知工具')
        if not isinstance(params, dict):
            raise ValueError('参数必须为对象')
        tool = self.by_name[name]
        if set(params) - set(tool['properties']):
            raise ValueError('存在不允许的参数')
        if set(tool['required']) - set(params):
            raise ValueError('缺少必填参数')
        for k,v in params.items():
            spec = tool['properties'][k]
            if spec['type']=='string' and (not isinstance(v,str) or len(v)>10000):
                raise ValueError(f'{k} 必须是字符串')
            if spec['type']=='integer' and (type(v) is not int or not spec['minimum'] <= v <= spec['maximum']):
                raise ValueError(f'{k} 超出范围')
            if spec['type']=='object' and not isinstance(v,dict):
                raise ValueError(f'{k} 必须为 JSON 对象')
            if spec['type']=='array' and not isinstance(v,list):
                raise ValueError(f'{k} 必须为数组')
        if name == 'run_lark_cli':
            argv=params['argv']
            if not 1<=len(argv)<=60 or any(not isinstance(a,str) or not a or len(a)>100000 or '\x00' in a for a in argv):
                raise ValueError('argv 必须是 1 到 60 个非空字符串')
            stdin=params.get('stdin')
            if stdin is not None and (not isinstance(stdin,str) or len(stdin)>100000):
                raise ValueError('stdin 内容过长')
            return self.runner.run_generic(argv,stdin=stdin,
                                           timeout=params.get('timeout_seconds'))
        if name == 'list_wiki_spaces':
            return self.runner.run(['wiki','+space-list','--page-size',str(params.get('page_size',10)),'--as','user','--format','json'])
        if name == 'list_personal_documents':
            args=['wiki','+node-list','--space-id','my_library','--page-size',str(params.get('page_size',50))]
            page_token=params.get('page_token')
            if page_token:
                if len(page_token)>500 or any(c in page_token for c in '\r\n\x00'):
                    raise ValueError('page_token 无效')
                args+=['--page-token',page_token]
            return self.runner.run(args+['--as','user','--format','json'])
        if name in ('list_base_tables','list_base_fields','list_base_records','create_base_record'):
            base = params['base_token']
            if not unrestricted and base not in self.cfg.bases:
                raise ValueError('此 Base 未加入 ALLOWED_BASE_TOKENS 白名单')
            if name == 'list_base_tables':
                return self.runner.run(['base','+table-list','--base-token',base,'--as','user','--format','json'])
            tid = params['table_id']
            if not isinstance(tid,str) or not 1<=len(tid)<=120 or any(x in tid for x in '\n\r\x00'):
                raise ValueError('table_id 无效')
            basic = ['--base-token',base,'--table-id',tid]
            if name == 'list_base_fields':
                return self.runner.run(['base','+field-list']+basic+['--as','user','--format','json'])
            if name == 'list_base_records':
                return self.runner.run(['base','+record-list']+basic+['--limit',str(params.get('limit',20)),'--as','user','--format','json'])
            fields = params['fields']
            if not fields or len(fields)>30 or any(not isinstance(k,str) or not k or len(k)>100 for k in fields):
                raise ValueError('fields 格式不合法')
            packed = json.dumps(fields,ensure_ascii=False,separators=(',',':'))
            if len(packed)>16_000:
                raise ValueError('记录内容太长')
            return self.runner.run(['base','+record-upsert']+basic+['--json',packed,'--as','user','--format','json'])
        if name == 'create_document':
            title=params['title']
            if not title.strip() or len(title)>200 or any(c in title for c in '\r\n\x00'):
                raise ValueError('标题不能为空，且不能超过 200 字符或包含换行')
            content=params.get('content')
            if content is not None and len(content)>50000:
                raise ValueError('正文不能超过 50000 字符')
            args=['docs','+create','--title',title,'--parent-position','my_library',
                  '--as','user']
            if content is not None:
                args+=['--doc-format','markdown','--content','-']
            result=self.runner.run(args,stdin=content)
            document=result.get('data',{}).get('document',{})
            document_id=document.get('document_id') if isinstance(document,dict) else None
            if isinstance(document_id,str) and document_id:
                with sqlite3.connect(str(self.cfg.db_path)) as db:
                    db.execute('INSERT OR IGNORE INTO bridge_created_docs VALUES (?)',(document_id,))
            else:
                result['_bridge_warning']='文档已创建，但未返回 document_id；请先确认结果，再配置后续读取权限。'
            return result
        if name in ('read_document','append_document'):
            doc = token_from_document(params['doc'])
            if not unrestricted and not self.document_allowed(doc):
                raise ValueError('当前连接为受限权限；此文档未加入允许列表，且不是本 Bridge 创建的文档')
            if name=='read_document':
                return self.runner.run(['docs','+fetch','--doc',doc,'--doc-format','markdown','--as','user','--format','json'])
            content = params['content']
            if not content.strip() or len(content)>8000:
                raise ValueError('追加内容不能为空，且不得超过 8000 字符')
            # The CLI interprets @path in --content as a local file reference.
            # Stdin keeps user text literal, including values beginning with @.
            return self.runner.run(['docs','+update','--doc',doc,'--command','append','--doc-format','markdown','--content','-','--as','user','--format','json'],stdin=content)
        raise ValueError('工具未实现')
