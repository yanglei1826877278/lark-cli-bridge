"""Start Bridge with host and port from .env."""
import uvicorn
from bridge.settings import make_settings

if __name__ == '__main__':
    cfg = make_settings()
    uvicorn.run('bridge.server:app', host=cfg.host, port=cfg.port, reload=False, log_level='info')
