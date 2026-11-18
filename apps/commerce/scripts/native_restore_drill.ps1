#requires -Version 7.0
param(
    [Parameter(Mandatory=$true)][string]$PgBin,
    [Parameter(Mandatory=$true)][string]$WeedBinary,
    [switch]$WorkersPaused
)
$ErrorActionPreference='Stop'
if (-not $WorkersPaused) {throw 'Pause this local project publisher/consumer before controlled fixtures'}
$taskRepo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython=Join-Path $taskRepo '.venv/Scripts/python.exe'
if (-not $env:MIGRATION_DATABASE_URL) {throw 'Explicit source MIGRATION_DATABASE_URL required'}
$taskPorts=@(55434,19334,18081,18889,18334,29334,28081,28889,28334)
$taskOccupied=Get-NetTCPConnection -LocalPort $taskPorts -State Listen -ErrorAction SilentlyContinue
if ($taskOccupied) {throw ("Temporary restore ports are occupied: "+(($taskOccupied.LocalPort | Sort-Object -Unique)-join ","))}
$taskId=[guid]::NewGuid().ToString('N').Substring(0,12)
$taskDir=Join-Path $taskRepo ('work/native-restore-'+$taskId)
$taskCluster=Join-Path $taskDir 'postgres'
$taskS3Dir=Join-Path $taskDir 's3'
$taskProfile=Join-Path $taskDir 'profile'
$taskBackup=Join-Path $taskDir 'backup'
$taskFake=Join-Path $taskDir 'fake-provider'
$taskState=Join-Path $taskDir 'source-state.json'
$taskSource=Get-Content -Raw (Join-Path $taskRepo '.local/runtime.json') | ConvertFrom-Json
$taskSourceUri=[uri]$taskSource.API_DATABASE_URL
$taskOwnerUri=[uri]($env:MIGRATION_DATABASE_URL.Replace('postgresql+psycopg://','postgresql://'))
if ($taskSourceUri.Host -ne $taskOwnerUri.Host -or $taskSourceUri.Port -ne $taskOwnerUri.Port -or $taskSourceUri.AbsolutePath -ne $taskOwnerUri.AbsolutePath) {throw 'Source DDL database must match the local API profile'}
New-Item -ItemType Directory -Path $taskCluster,$taskS3Dir | Out-Null
function Invoke-TaskPython([string[]]$TaskArguments,[string]$TaskLog) {
    & $taskPython @TaskArguments *> $TaskLog
    if ($LASTEXITCODE -ne 0) {throw "Drill command failed; private diagnostics: $TaskLog"}
}
function New-TaskSecret {
    $taskBytes=New-Object byte[] 24
    $taskRng=[Security.Cryptography.RandomNumberGenerator]::Create()
    $taskRng.GetBytes($taskBytes);$taskRng.Dispose()
    return [BitConverter]::ToString($taskBytes).Replace('-','')
}
Set-Location $taskRepo
Invoke-TaskPython -TaskArguments @('scripts/verify_restore_drill.py','prepare','--state',$taskState) -TaskLog (Join-Path $taskDir 'prepare.log')
Invoke-TaskPython -TaskArguments @('scripts/backup.py','create','--output',$taskBackup,'--pg-bin',$PgBin) -TaskLog (Join-Path $taskDir 'backup.log')
Invoke-TaskPython -TaskArguments @('scripts/verify_restore_drill.py','marker','--state',$taskState) -TaskLog (Join-Path $taskDir 'marker.log')
$taskRecoveryStarted=[DateTimeOffset]::UtcNow
$taskRecoveryClock=[Diagnostics.Stopwatch]::StartNew()
$taskPassword=New-TaskSecret
$taskPasswordFile=Join-Path $taskCluster 'password'
[IO.File]::WriteAllText($taskPasswordFile,$taskPassword)
$taskData=Join-Path $taskCluster 'data'
& "$PgBin/initdb.exe" -D $taskData -U visual_restore_owner --auth=scram-sha-256 --pwfile=$taskPasswordFile --encoding=UTF8 --no-locale *> (Join-Path $taskDir 'initdb.log')
if ($LASTEXITCODE -ne 0) {throw 'Temporary restore initdb failed'}
$taskAccess=New-TaskSecret;$taskSecret=New-TaskSecret
$taskS3Config=Join-Path $taskS3Dir 's3.json'
$taskConfig=@{identities=@(@{name='restore_drill';credentials=@(@{accessKey=$taskAccess;secretKey=$taskSecret});actions=@('Admin','Read','Write','List','Tagging')})} | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText($taskS3Config,$taskConfig)
$taskPgServer=$null;$taskS3Server=$null;$taskSourceCleaned=$false
try {
    $taskPgArgs=@('-D',$taskData,'-h','127.0.0.1','-p','55434') | ForEach-Object {'"'+$_+'"'}
    $taskPgServer=Start-Process -FilePath "$PgBin/postgres.exe" -ArgumentList $taskPgArgs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskDir 'pg.stdout.log') -RedirectStandardError (Join-Path $taskDir 'pg.stderr.log') -PassThru
    $taskS3Args=@('server','-filer','-s3',('-dir='+$taskS3Dir),('-master.dir='+$taskS3Dir),'-ip=127.0.0.1','-ip.bind=127.0.0.1','-master.port=19334','-volume.port=18081','-filer.port=18889','-s3.port=18334','-s3.port.lance=0','-s3.port.iceberg=0','-volume.max=16','-master.volumeSizeLimitMB=64',('-s3.config='+$taskS3Config)) | ForEach-Object {'"'+$_+'"'}
    $taskS3Server=Start-Process -FilePath $WeedBinary -ArgumentList $taskS3Args -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskDir 's3.stdout.log') -RedirectStandardError (Join-Path $taskDir 's3.stderr.log') -PassThru
    $taskReady=$false
    for ($taskAttempt=0;$taskAttempt -lt 100;$taskAttempt++) {
        & "$PgBin/pg_isready.exe" -h 127.0.0.1 -p 55434 -U visual_restore_owner *> $null
        $taskPgReady=$LASTEXITCODE -eq 0
        $taskObjectReady=$false
        try {$null=Invoke-WebRequest -Uri 'http://127.0.0.1:18334/' -TimeoutSec 1;$taskObjectReady=$true}
        catch {if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 403) {$taskObjectReady=$true}}
        if ($taskPgReady -and $taskObjectReady) {$taskReady=$true;break}
        Start-Sleep -Milliseconds 100
    }
    if (-not $taskReady) {throw 'Temporary restore services did not become ready'}
    $env:RESTORE_DATABASE_URL="postgresql://visual_restore_owner:${taskPassword}@127.0.0.1:55434/postgres"
    $env:S3_ENDPOINT='http://127.0.0.1:18334';$env:S3_ACCESS_KEY=$taskAccess;$env:S3_SECRET_KEY=$taskSecret;$env:S3_BUCKET=$taskSource.S3_BUCKET;$env:S3_REGION=$taskSource.S3_REGION
    $taskRestoreLog=Join-Path $taskDir 'restore-result.json'
    Invoke-TaskPython -TaskArguments @('scripts/backup.py','restore','--input',$taskBackup,'--fake-output',$taskFake,'--config-output',(Join-Path $taskDir 'restored-configuration'),'--pg-bin',$PgBin) -TaskLog $taskRestoreLog
    $env:MIGRATION_DATABASE_URL="postgresql+psycopg://visual_restore_owner:${taskPassword}@127.0.0.1:55434/postgres"
    $env:AMQP_URL=$taskSource.AMQP_URL
    Invoke-TaskPython -TaskArguments @('scripts/setup_local.py','--profile-dir',$taskProfile) -TaskLog (Join-Path $taskDir 'profile.log')
    $taskProfileFile=Join-Path $taskProfile 'runtime.json'
    $taskValues=Get-Content -Raw $taskProfileFile | ConvertFrom-Json
    $taskValues.FAKE_PROVIDER_DIR=$taskFake
    [IO.File]::WriteAllText($taskProfileFile,($taskValues | ConvertTo-Json -Depth 8))
    Invoke-TaskPython -TaskArguments @('scripts/verify_restore_drill.py','verify','--profile-dir',$taskProfile,'--restore-result',$taskRestoreLog,'--state',$taskState) -TaskLog (Join-Path $taskDir 'verify.log')
    $taskRecoveryFinished=[DateTimeOffset]::UtcNow
    $taskRecoveryClock.Stop()
    $taskManifest=Get-Content -Raw (Join-Path $taskBackup 'manifest.json') | ConvertFrom-Json
    $taskEvidenceFile=Join-Path $taskRepo 'docs/evidence/backup-restore-drill.json'
    $taskEvidence=Get-Content -Raw $taskEvidenceFile | ConvertFrom-Json
    $taskTiming=@{rto_seconds=$taskRecoveryClock.Elapsed.TotalSeconds;snapshot_age_seconds_at_recovery_start=($taskRecoveryStarted-[DateTimeOffset]$taskManifest.snapshot_at).TotalSeconds;backup_seconds=([DateTimeOffset]$taskManifest.finished_at-[DateTimeOffset]$taskManifest.started_at).TotalSeconds;scope='native cold destination initialization through verified API/artifact/safe known-id recovery; not a production SLA'}
    if ($taskTiming.snapshot_age_seconds_at_recovery_start -lt 0 -or $taskTiming.rto_seconds -lt 0) {throw 'Invalid recovery timing; clock normalization required'}
    $taskRto=$taskTiming.rto_seconds.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $taskAge=$taskTiming.snapshot_age_seconds_at_recovery_start.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $taskAssessment=Join-Path $taskDir 'objectives.json'
    Invoke-TaskPython -TaskArguments @('scripts/assess_recovery.py','--rto-seconds',$taskRto,'--snapshot-age-seconds',$taskAge) -TaskLog $taskAssessment
    $taskTiming.objectives=Get-Content -Raw $taskAssessment | ConvertFrom-Json
    $taskEvidence | Add-Member -NotePropertyName timing -NotePropertyValue $taskTiming
    $taskCommit=(& git -C $taskRepo rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {throw 'Cannot bind restore evidence to current commit'}
    $taskEvidence | Add-Member -NotePropertyName base_commit -NotePropertyValue $taskCommit
    [IO.File]::WriteAllText($taskEvidenceFile,($taskEvidence | ConvertTo-Json -Depth 20))
    Invoke-TaskPython -TaskArguments @('scripts/verify_restore_drill.py','cleanup','--state',$taskState) -TaskLog (Join-Path $taskDir 'cleanup.log')
    $taskSourceCleaned=$true
    Write-Output ('Verified independent PG/S3 restore. RTO seconds: '+[Math]::Round($taskTiming.rto_seconds,2))
} finally {
    if ($taskPgServer -and -not $taskPgServer.HasExited) {& "$PgBin/pg_ctl.exe" -D $taskData stop -m fast *> (Join-Path $taskDir 'stop.log')}
    if ($taskS3Server -and -not $taskS3Server.HasExited) {Stop-Process -Id $taskS3Server.Id}
    if (-not $taskSourceCleaned -and (Test-Path $taskState)) {
        try {Invoke-TaskPython -TaskArguments @('scripts/verify_restore_drill.py','cleanup','--state',$taskState) -TaskLog (Join-Path $taskDir 'cleanup-recovery.log')}
        catch {Write-Warning 'Source fixture cleanup needs review; use the retained private state to retry.'}
    }
}
