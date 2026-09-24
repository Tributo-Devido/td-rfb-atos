# Conferência da manhã da rotina noturna do rfb_atos — alerta no Slack se a rodada não aconteceu.
# Registrar (uma vez, no PowerShell do usuário):
#   schtasks /Create /TN "td-rfb-atos conferencia" /SC DAILY /ST 09:00 /F `
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\td-rfb-atos\scripts\rotina_conferir.ps1"
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $PSScriptRoot
py rotina_noturna.py --conferir
exit $LASTEXITCODE
