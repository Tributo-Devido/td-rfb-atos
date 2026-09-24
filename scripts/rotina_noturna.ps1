# Rotina noturna do rfb_atos — chamada pelo Agendador de Tarefas do Windows.
# Registrar (uma vez, no PowerShell do usuário):
#   schtasks /Create /TN "td-rfb-atos rotina noturna" /SC DAILY /ST 02:00 /F `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\td-rfb-atos\scripts\rotina_noturna.ps1"
# Log e resumo de cada noite: C:\td-rfb-atos-dados\rotina\AAAA-MM-DD.log / .json
$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $PSScriptRoot
py rotina_noturna.py
exit $LASTEXITCODE
