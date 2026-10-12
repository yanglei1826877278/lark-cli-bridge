from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent

def load_dotenv(path: Path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip('"').strip("'")

@dataclass(frozen=True)
class Settings:
    url: str
    host: str
    port: int
    password: str
    mock: bool
    cli_path: str
    bases: frozenset[str]
    docs: frozenset[str]
    db_path: Path
    timeout: int
    cli_workdir: Path = ROOT
    permission_mode: str = 'restricted'

    @property
    def resource(self):
        return self.url + '/mcp'

    @property
    def remote(self):
        p = urlparse(self.url)
        return p.hostname not in ('127.0.0.1', 'localhost', '::1')

def make_settings():
    load_dotenv(ROOT / '.env')
    url = os.getenv('PUBLIC_URL', 'http://127.0.0.1:8765').rstrip('/')
    p = urlparse(url)
    if p.scheme not in ('https', 'http') or not p.netloc or p.path or p.query or p.fragment:
        raise ValueError('PUBLIC_URL 必须是无路径的 URL，例如 https://bridge.example.com')
    remote = p.hostname not in ('127.0.0.1', 'localhost', '::1')
    if remote and p.scheme != 'https':
        raise ValueError('公网 PUBLIC_URL 必须使用 HTTPS')
    mock = os.getenv('MOCK_CLI', '0').strip() == '1'
    if remote and mock:
        raise ValueError('禁止将 MOCK_CLI=1 暴露到公网')
    pwd = os.getenv('OWNER_PASSWORD', '')
    if remote and (len(pwd) < 16 or pwd == 'replace-with-a-strong-random-password'):
        raise ValueError('公网部署时请设置强 OWNER_PASSWORD（至少 16 位）')
    if not pwd:
        pwd = 'local-dev-password-only' if not remote else ''
    db_path = Path(os.getenv('DB_PATH', 'data/bridge.sqlite3'))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    permission_mode = os.getenv('BRIDGE_PERMISSION_MODE', 'restricted').strip().lower()
    if permission_mode not in ('restricted', 'full'):
        raise ValueError('BRIDGE_PERMISSION_MODE 只能是 restricted 或 full')
    cli_workdir = Path(os.getenv('CLI_WORKDIR', str(ROOT)))
    if not cli_workdir.is_absolute():
        cli_workdir = ROOT / cli_workdir
    cli_workdir.mkdir(parents=True, exist_ok=True)
    return Settings(
        url=url, host=os.getenv('HOST', '127.0.0.1'),
        port=int(os.getenv('PORT', '8765')), password=pwd,
        mock=mock, cli_path=os.getenv('LARK_CLI_PATH', ''),
        bases=frozenset(s.strip() for s in os.getenv('ALLOWED_BASE_TOKENS', '').split(',') if s.strip()),
        docs=frozenset(s.strip() for s in os.getenv('ALLOWED_DOC_TOKENS', '').split(',') if s.strip()),
        db_path=db_path, timeout=int(os.getenv('CLI_TIMEOUT', '25')),
        cli_workdir=cli_workdir,
        permission_mode=permission_mode,
    )
