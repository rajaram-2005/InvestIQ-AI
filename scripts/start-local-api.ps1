# Local development only. Run from the repository root after `supabase start`.
# Status is captured in memory; keys are not printed or written to a file.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Python environment missing. Follow docs/windows-local-setup.md first.' }
$lines = & npx.cmd --yes supabase@3.2.0 status -o env
if ($LASTEXITCODE -ne 0) { throw 'Local Supabase status failed. Start Docker Desktop and run npx --yes supabase@3.2.0 start.' }
$vars = @{}
foreach ($line in $lines) {
    if ($line -match '^([A-Z_]+)="(.*)"$') { $vars[$Matches[1]] = $Matches[2] }
    elseif ($line -match '^([A-Z_]+)=([^\s]+)$') { $vars[$Matches[1]] = $Matches[2] }
}
if ($vars['API_URL'] -ne 'http://127.0.0.1:54321' -or -not $vars['ANON_KEY'] -or -not $vars['DB_URL']) {
    throw 'Unexpected local Supabase status format or URL; check the Windows setup guide. No credentials were applied.'
}
try { $dbUri = [System.Uri]$vars['DB_URL'] } catch { throw 'Invalid database URL from local Supabase status.' }
if ($dbUri.Scheme -notin @('postgres','postgresql') -or $dbUri.Host -ne '127.0.0.1' -or $dbUri.Port -ne 54322) {
    throw 'Refusing a database URL outside the expected local Supabase instance.'
}
$env:SUPABASE_URL = $vars['API_URL']
$env:SUPABASE_ANON_KEY = $vars['ANON_KEY']
$env:INVESTIQ_DATABASE_URL = $vars['DB_URL']
$env:INVESTIQ_ALLOW_LOCAL_AUTH = '1'
Write-Host 'Starting the development API on 127.0.0.1:8000. Do not expose it to the network.'
& $python -m uvicorn investiq.platform.api:app --host 127.0.0.1 --port 8000
if ($LASTEXITCODE -ne 0) { throw 'API process stopped with an error.' }
