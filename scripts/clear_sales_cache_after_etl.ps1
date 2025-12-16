# Usage: .\clear_sales_cache_after_etl.ps1 -VenvPath C:\path\to\venv -ProjectPath C:\path\to\project
param(
    [Parameter(Mandatory=$true)] [string]$VenvPath,
    [Parameter(Mandatory=$true)] [string]$ProjectPath
)

# Activate virtualenv (Windows)
$activate = Join-Path $VenvPath 'Scripts\Activate.ps1'
if (Test-Path $activate) { . $activate } else { Write-Error "Virtualenv activate script not found: $activate"; exit 1 }

Push-Location $ProjectPath
python manage.py clear_sales_cache
Pop-Location

# Deactivate is automatic when restoring environment in many setups
