"""Offline mock of a small CLI surface for local protocol tests. Never use publicly."""
from __future__ import annotations
import json
import sys
from pathlib import Path

args=sys.argv[1:]
if args == ['--help']:
    print('Mock lark-cli help: wiki, docs, base')
    sys.exit(0)

def arg(name, default=None):
    try:
        return args[args.index(name)+1]
    except (ValueError,IndexError):
        return default

if args[:2]==['wiki','+space-list']:
    result={'ok':True,'data':{'spaces':[{'space_id':'mock-space-1','name':'测试知识空间'}],'has_more':False}}
elif args[:2]==['base','+table-list']:
    result={'ok':True,'data':{'tables':[{'table_id':'tblMockKeywords','name':'SEO关键词库'}]}}
elif args[:2]==['base','+field-list']:
    result={'ok':True,'data':{'fields':[{'field_name':n,'type':'text'} for n in ('关键词','备注','来源')]}}
elif args[:2]==['base','+record-list']:
    result={'ok':True,'data':{'items':[{'record_id':'recMock001','fields':{'关键词':'sprite sheet maker','备注':'模拟数据'}}], 'has_more':False}}
elif args[:2]==['base','+record-upsert']:
    try:
        data=json.loads(arg('--json','{}'))
        result={'ok':True,'data':{'record_id':'recMockCreated','fields':data},'mock':True}
    except ValueError:
        result={'ok':False,'error':{'message':'fields JSON 无效'}}
elif args[:2]==['docs','+fetch']:
    result={'ok':True,'data':{'document_id':arg('--doc'), 'content':'# 模拟飞书文档\n用于离线测试。'}}
elif args[:2]==['docs','+create']:
    content = sys.stdin.read() if arg('--content') == '-' else arg('--content','')
    result={'ok':True,'data':{'document':{'document_id':'docMockNew001',
        'url':'https://example.feishu.cn/docx/docMockNew001',
        'title':arg('--title'),'content':content}}}
elif args[:2]==['docs','+update']:
    content = sys.stdin.read() if arg('--content') == '-' else arg('--content')
    result={'ok':True,'data':{'document_id':arg('--doc'), 'appended':content, 'mock':True}}
elif args[:2]==['wiki','+node-list']:
    result={'ok':True,'data':{'nodes':[{'node_token':'mock-personal-doc','title':'个人文档'}], 'has_more':False}}
else:
    result={'ok':False,'error':{'message':'mock 不支持此命令'}}
print(json.dumps(result,ensure_ascii=False))
sys.exit(0 if result['ok'] else 1)
