from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from .settings import Settings, ROOT

class CommandError(Exception):
    pass

class CliRunner:
    def __init__(self, config: Settings):
        self.config = config

    def binary(self):
        if self.config.mock:
            return [sys.executable, str(ROOT / 'scripts' / 'mock_lark_cli.py')]
        exe = self.config.cli_path or shutil.which('lark-cli.exe' if os.name == 'nt' else 'lark-cli')
        if not exe and os.name == 'nt':
            # npm places the native binary inside the package, but only exposes
            # a PowerShell/CMD shim on PATH. Resolve the binary beside that shim.
            for shim_name in ('lark-cli.ps1', 'lark-cli.cmd'):
                shim = shutil.which(shim_name)
                if shim:
                    candidate = Path(shim).parent / 'node_modules' / '@larksuite' / 'cli' / 'bin' / 'lark-cli.exe'
                    if candidate.is_file():
                        exe = str(candidate)
                        break
        if not exe:
            raise CommandError('未找到 lark-cli，请先安装并执行 lark-cli auth status')
        # .cmd/.bat normally execute via cmd.exe; refuse because user text could be shell-expanded.
        if os.name == 'nt' and Path(exe).suffix.lower() in ('.cmd', '.bat'):
            raise CommandError('发现 Windows CMD 包装器。请将 LARK_CLI_PATH 指向官方 lark-cli.exe，而不是 .cmd/.bat')
        return [str(exe)]

    def _execute(self, args: list[str], *, stdin: str | None = None,
                 timeout: int | None = None):
        if any(not isinstance(x, str) or '\x00' in x for x in args):
            raise CommandError('CLI 参数非法')
        env = os.environ.copy()
        env['LARKSUITE_CLI_NO_UPDATE_NOTIFIER'] = '1'
        env['LARKSUITE_CLI_NO_SKILLS_NOTIFIER'] = '1'
        try:
            return subprocess.run(self.binary() + args, shell=False, capture_output=True, input=stdin,
                                  text=True, encoding='utf-8', errors='replace',
                                  timeout=timeout or self.config.timeout, env=env,
                                  cwd=str(self.config.cli_workdir))
        except subprocess.TimeoutExpired as e:
            raise CommandError('飞书 CLI 超时，结果不确定。请先查询记录，确认是否写入后再试，避免重复。') from e
        except OSError as e:
            raise CommandError(f'无法启动飞书 CLI：{e.strerror or type(e).__name__}') from e

    def run(self, args: list[str], *, stdin: str | None = None):
        proc = self._execute(args, stdin=stdin)
        out = proc.stdout.strip()
        if len(out) > 350_000:
            raise CommandError('CLI 输出过长，请缩小读取范围')
        if proc.returncode and not out:
            # The official CLI reports errors as JSON on stderr. Never return raw stderr.
            try:
                diag = json.loads(proc.stderr.strip())
                err = diag.get('error', {})
                msg = err.get('message', 'CLI 调用失败') if isinstance(err,dict) else 'CLI 调用失败'
                raise CommandError(str(msg)[:300])
            except json.JSONDecodeError:
                raise CommandError(f'CLI 执行失败，退出码 {proc.returncode}；请在本机检查 CLI 授权与权限')
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            # Don't return stderr: it can include local paths / sensitive cli diagnostics.
            raise CommandError(f'飞书 CLI 返回非 JSON 内容（退出码 {proc.returncode}）。请在本地终端直接检查命令。')
        if proc.returncode or (isinstance(result, dict) and result.get('ok') is False):
            err = result.get('error', {}) if isinstance(result, dict) else {}
            msg = err.get('message', 'CLI 执行失败') if isinstance(err, dict) else 'CLI 执行失败'
            raise CommandError(str(msg)[:300])
        return result

    def run_generic(self, args: list[str], *, stdin: str | None = None,
                    timeout: int | None = None):
        proc = self._execute(args, stdin=stdin, timeout=timeout)
        out = proc.stdout.strip()
        if len(out) > 350_000:
            raise CommandError('CLI 输出过长，请缩小读取范围')
        try:
            output = json.loads(out)
        except json.JSONDecodeError:
            output = out
        result = {'exit_code':proc.returncode,'output':output}
        if proc.returncode:
            try:
                diag = json.loads(proc.stderr.strip())
                error = diag.get('error',{})
                result['error'] = str(error.get('message','CLI 调用失败'))[:300] if isinstance(error,dict) else 'CLI 调用失败'
            except json.JSONDecodeError:
                result['error'] = f'CLI 执行失败，退出码 {proc.returncode}'
        return result

def token_from_document(doc: str):
    if not isinstance(doc, str) or len(doc) > 500 or not doc:
        raise ValueError('文档 ID 或链接无效')
    if doc.startswith('https://'):
        p = urlparse(doc)
        if not p.hostname or not (p.hostname.endswith('.feishu.cn') or p.hostname.endswith('.larksuite.com')):
            raise ValueError('仅支持飞书官方文档链接')
        parts = [s for s in p.path.split('/') if s]
        return parts[-1] if parts else ''
    if '/' in doc or '?' in doc or '#' in doc:
        raise ValueError('文档 ID 无效')
    return doc
