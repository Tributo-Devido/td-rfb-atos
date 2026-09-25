# Segunda tentativa da rotina noturna do rfb_atos (04:00): só roda se a das 02:00 não terminou ok.
# Registrar (uma vez, no PowerShell do usuário):
#   schtasks /Create /TN "td-rfb-atos rotina reforco" /SC DAILY /ST 04:00 /F `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\td-rfb-atos\scripts\rotina_reforco.ps1"
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $PSScriptRoot
py rotina_noturna.py --reforco
exit $LASTEXITCODE
