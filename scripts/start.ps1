$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (!(Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host '已生成 .env。请编辑 OWNER_PASSWORD、ALLOWED_BASE_TOKENS 和 ALLOWED_DOC_TOKENS。' -ForegroundColor Yellow
}
if (!(Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& .\.venv\Scripts\python.exe -m scripts.run
