# Starts the setup wizard. Run from anywhere:  powershell -ExecutionPolicy Bypass -File setup.ps1
# Extra words pass through, e.g.  setup.ps1 status   or   setup.ps1 x-login
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Setup runs on uv (a Python tool manager). Install it with:'
    Write-Host '  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Write-Host 'then open a new terminal and run setup again.'
    exit 1
}
uv run --project (Join-Path $repo 'router') python -m jev_router.onboard @args
exit $LASTEXITCODE
