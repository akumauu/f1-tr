param(
    [string]$InstallDir = "D:\PostgreSQL\18",
    [string]$DataDir = "D:\PostgreSQL\18\data",
    [int]$Port = 5432,
    [string]$PostgresPassword = "f1pass2026",
    [string]$AppDatabase = "f1_analysis",
    [string]$AppUser = "f1user",
    [string]$AppPassword = "f1pass2026"
)

$ErrorActionPreference = "Stop"

$zipUrl = "https://get.enterprisedb.com/postgresql/postgresql-18.4-1-windows-x64-binaries.zip"
$downloadDir = "D:\installers"
$zipPath = Join-Path $downloadDir "postgresql-18.4-1-windows-x64-binaries.zip"

New-Item -ItemType Directory -Force $downloadDir | Out-Null
New-Item -ItemType Directory -Force $InstallDir | Out-Null

if (!(Test-Path $zipPath)) {
    Write-Host "Downloading PostgreSQL binaries..."
    curl.exe -L -C - $zipUrl -o $zipPath
}

if (!(Test-Path (Join-Path $InstallDir "bin\psql.exe"))) {
    Write-Host "Extracting PostgreSQL to $InstallDir..."
    $extractDir = Join-Path $downloadDir "postgresql-18-binaries"
    Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $extractDir | Out-Null
    tar.exe -xf $zipPath -C $extractDir

    $pgsqlDir = Get-ChildItem -Path $extractDir -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName "bin\psql.exe") } |
        Select-Object -First 1
    if (!$pgsqlDir) {
        throw "Cannot find PostgreSQL bin directory after extraction."
    }

    Copy-Item -Path (Join-Path $pgsqlDir.FullName "*") -Destination $InstallDir -Recurse -Force
}

$binDir = Join-Path $InstallDir "bin"
$initdb = Join-Path $binDir "initdb.exe"
$pgCtl = Join-Path $binDir "pg_ctl.exe"
$psql = Join-Path $binDir "psql.exe"
$createdb = Join-Path $binDir "createdb.exe"
$createuser = Join-Path $binDir "createuser.exe"

if (!(Test-Path $initdb)) {
    throw "PostgreSQL binaries are incomplete: $initdb not found."
}

$passwordFile = Join-Path $downloadDir "postgres-password.txt"
Set-Content -Path $passwordFile -Value $PostgresPassword -Encoding ascii

if (!(Test-Path (Join-Path $DataDir "PG_VERSION"))) {
    Write-Host "Initializing PostgreSQL data directory..."
    New-Item -ItemType Directory -Force $DataDir | Out-Null
    & $initdb -D $DataDir -U postgres --encoding=UTF8 --locale=C --pwfile=$passwordFile
}

$postgresqlConf = Join-Path $DataDir "postgresql.conf"
$confText = Get-Content -Raw $postgresqlConf
if ($confText -notmatch "port = $Port") {
    Add-Content -Path $postgresqlConf -Value "`n# Added by F1 TR local setup`nport = $Port`nlisten_addresses = 'localhost'`n"
}

Write-Host "Starting PostgreSQL..."
& $pgCtl -D $DataDir -l (Join-Path $DataDir "postgresql.log") start
Start-Sleep -Seconds 3

$env:PGPASSWORD = $PostgresPassword

Write-Host "Creating application user and database if needed..."
$userExists = (& $psql -h localhost -p $Port -U postgres -tAc "select 1 from pg_roles where rolname = '$AppUser'").Trim()
if ($userExists -ne "1") {
    & $createuser -h localhost -p $Port -U postgres $AppUser
    & $psql -h localhost -p $Port -U postgres -c "alter user $AppUser with password '$AppPassword';"
}

$dbExists = (& $psql -h localhost -p $Port -U postgres -tAc "select 1 from pg_database where datname = '$AppDatabase'").Trim()
if ($dbExists -ne "1") {
    & $createdb -h localhost -p $Port -U postgres -O $AppUser $AppDatabase
}

Write-Host "PostgreSQL is ready."
Write-Host "DATABASE_URL=postgres://$AppUser`:$AppPassword@localhost:$Port/$AppDatabase`?sslmode=disable"
