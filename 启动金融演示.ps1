param([ValidateSet('start','stop','status','build','test')][string]$Action='start')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONPATH=Join-Path $PSScriptRoot 'src'
$env:PYTHONIOENCODING='utf-8'
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 或更高版本。' }
    & $python -m pip install -e '.[dev]' docker
    if ($LASTEXITCODE -ne 0) { throw '项目依赖安装失败。' }
}
if ($Action -eq 'test') {
    & $python -m pytest -q --basetemp=.pytest-tmp-launcher --junitxml=demo/finance/evidence/python-tests.xml
    if ($LASTEXITCODE -ne 0) { throw 'Python 测试失败。' }
    & $python demo/finance/acceptance.py --outage
} else {
    if ($Action -eq 'start') {
        & $python demo/finance/manage.py build
        if ($LASTEXITCODE -ne 0) { throw '镜像构建失败。' }
    }
    & $python demo/finance/manage.py $Action
    if ($LASTEXITCODE -ne 0) { throw 'Docker 操作失败，请检查 Docker Desktop 是否运行。' }
    if ($Action -eq 'start') { Start-Process 'http://127.0.0.1:18088/' }
}
if ($LASTEXITCODE -ne 0) { throw '操作未通过，请检查输出。' }
