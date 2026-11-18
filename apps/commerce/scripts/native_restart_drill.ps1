#requires -Version 7.0
param([Parameter(Mandatory=$true)][string]$RuntimeRoot)
$ErrorActionPreference='Stop'
$taskRepo=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskRoot=(Resolve-Path -LiteralPath $RuntimeRoot).Path
$taskPython=Join-Path $taskRepo '.venv/Scripts/python.exe'
$taskWeed=(Resolve-Path (Join-Path $taskRoot 'seaweed-runtime/weed.exe')).Path
$taskData=(Resolve-Path (Join-Path $taskRoot 'seaweed-data')).Path
$taskCtl=(Resolve-Path (Join-Path $taskRoot 'rabbit-runtime/rabbitmq_server-4.4.0/sbin/rabbitmqctl.bat')).Path
$taskPorts=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 18333 -State Listen
if (@($taskPorts).Count -ne 1) {throw 'Expected one isolated S3 listener'}
$taskProcess=Get-CimInstance Win32_Process -Filter ('ProcessId='+$taskPorts.OwningProcess)
if ($taskProcess.ExecutablePath -ne $taskWeed -or -not $taskProcess.CommandLine.Contains('-dir='+$taskData) -or -not $taskProcess.CommandLine.Contains('-s3.port=18333')) {throw 'S3 process identity mismatch; refusing stop'}
$taskRabbitListener=Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 5679 -State Listen
if (-not $taskRabbitListener) {throw 'Expected isolated RabbitMQ listener'}
$taskDir=Join-Path $taskRepo ('work/native-restart-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskDir | Out-Null
$taskState=Join-Path $taskDir 'state.json'
$taskSavedEnv=@{}
foreach ($taskName in @('ERLANG_HOME','RABBITMQ_BASE','RABBITMQ_MNESIA_BASE','RABBITMQ_LOG_BASE','RABBITMQ_CONFIG_FILE','RABBITMQ_NODENAME','ERL_EPMD_ADDRESS','ERL_EPMD_PORT','ERL_FLAGS')) {$taskSavedEnv[$taskName]=[Environment]::GetEnvironmentVariable($taskName,'Process')}
$env:ERLANG_HOME=Join-Path $taskRoot 'erlang-runtime'
$env:RABBITMQ_BASE=Join-Path $taskRoot 'rabbit-data'
$env:RABBITMQ_MNESIA_BASE=Join-Path $env:RABBITMQ_BASE 'db'
$env:RABBITMQ_LOG_BASE=Join-Path $env:RABBITMQ_BASE 'log'
$env:RABBITMQ_CONFIG_FILE=Join-Path $env:RABBITMQ_BASE 'rabbitmq'
$env:RABBITMQ_NODENAME='visual_test@localhost'
$env:ERL_EPMD_ADDRESS='127.0.0.1';$env:ERL_EPMD_PORT='14369'
$env:ERL_FLAGS='-setcookie '+([IO.File]::ReadAllText((Join-Path $taskRoot 'rabbit-cookie')).Trim())+' +S 2:2 +A 4'
Set-Location $taskRepo
function Invoke-TaskProbe([string]$Phase) {
    $taskOutput=& $taskPython scripts/restart_probe.py $Phase --state $taskState 2> (Join-Path $taskDir ($Phase+'.stderr'))
    if ($LASTEXITCODE -ne 0) {throw "Fixture $Phase failed; diagnostics retained privately"}
    return ($taskOutput | ConvertFrom-Json)
}
function Read-TaskMonitor {
    $taskOutput=& $taskPython scripts/run_local.py monitor 2> (Join-Path $taskDir 'monitor.stderr')
    return ($taskOutput | ConvertFrom-Json)
}
function Invoke-TaskRabbit([string]$Action) {
    & $taskCtl -n visual_test@localhost $Action *> (Join-Path $taskDir ('rabbit-'+$Action+'.log'))
    if ($LASTEXITCODE -ne 0) {throw "Isolated RabbitMQ $Action failed"}
}
function Start-TaskS3 {
    $taskArgs=@('server','-filer','-s3',('-dir='+$taskData),('-master.dir='+$taskData),'-ip=127.0.0.1','-ip.bind=127.0.0.1','-master.port=19333','-volume.port=18080','-filer.port=18888','-s3.port=18333','-s3.port.lance=0','-s3.port.iceberg=0','-volume.max=16','-master.volumeSizeLimitMB=64',('-s3.config='+(Join-Path $taskRoot 's3-config.json'))) | ForEach-Object {'"'+$_+'"'}
    $taskNew=Start-Process -FilePath $taskWeed -ArgumentList $taskArgs -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskDir 's3.stdout.log') -RedirectStandardError (Join-Path $taskDir 's3.stderr.log') -PassThru
    [IO.File]::WriteAllText((Join-Path $taskRoot 'seaweed-pid'),[string]$taskNew.Id)
    return $taskNew
}
$taskRabbitStopped=$false;$taskS3Stopped=$false;$taskFixtureCleaned=$false
try {
    $taskBefore=Read-TaskMonitor
    if ($taskBefore.alerts.Count -ne 0) {throw 'Resolve existing monitor alerts before restart drill'}
    $taskPrepared=Invoke-TaskProbe prepare
    Invoke-TaskRabbit stop_app;$taskRabbitStopped=$true
    $taskMqDown=Read-TaskMonitor
    if ($taskMqDown.dependencies.mq -ne 'unavailable' -or 'mq_unavailable' -notin $taskMqDown.alerts.code) {throw 'MQ outage did not alert'}
    Invoke-TaskRabbit start_app;$taskRabbitStopped=$false
    $taskMqReady=$false
    for ($taskAttempt=0;$taskAttempt -lt 30;$taskAttempt++) {
        $taskCheck=Read-TaskMonitor
        if ($taskCheck.dependencies.mq -eq 'ready') {$taskMqReady=$true;break}
        Start-Sleep -Seconds 1
    }
    if (-not $taskMqReady) {throw 'MQ did not recover'}
    # Identity was checked against executable, data directory and listener above.
    $taskStill=Get-CimInstance Win32_Process -Filter ('ProcessId='+$taskProcess.ProcessId)
    if ($taskStill.CreationDate -ne $taskProcess.CreationDate -or $taskStill.ExecutablePath -ne $taskWeed) {throw 'S3 process changed; refusing stop'}
    Stop-Process -Id $taskProcess.ProcessId
    $taskS3Stopped=$true
    $taskS3Down=Read-TaskMonitor
    if ($taskS3Down.dependencies.storage -ne 'unavailable' -or 'storage_unavailable' -notin $taskS3Down.alerts.code) {throw 'S3 outage did not alert'}
    $taskNewS3=Start-TaskS3;$taskS3Stopped=$false
    $taskReady=$false
    for ($taskAttempt=0;$taskAttempt -lt 45;$taskAttempt++) {
        $taskAfter=Read-TaskMonitor
        if ($taskAfter.dependencies.storage -eq 'ready' -and $taskAfter.alerts.Count -eq 0) {$taskReady=$true;break}
        if ($taskNewS3.HasExited) {throw 'Restarted S3 exited; see private log'}
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) {throw 'Services did not recover cleanly'}
    $taskVerified=Invoke-TaskProbe verify
    $taskCleanup=Invoke-TaskProbe cleanup
    if (-not $taskCleanup.fixture_cleanup) {throw 'Restart fixture cleanup incomplete'}
    $taskFixtureCleaned=$true
    foreach ($taskInvariant in @('wallet_equation_errors','ledger_total_errors','reservation_link_errors','task_credit_state_errors','duplicate_terminal_settlements')) {
        if ($taskBefore.snapshot.$taskInvariant -ne 0 -or $taskAfter.snapshot.$taskInvariant -ne 0) {throw 'Financial invariant changed during infrastructure drill'}
    }
    if (($taskBefore.snapshot.tasks | ConvertTo-Json -Compress) -ne ($taskAfter.snapshot.tasks | ConvertTo-Json -Compress)) {throw 'Business task status changed during fixture-only drill'}
    if (($taskBefore.snapshot.business_metrics.provider_activity | ConvertTo-Json -Compress) -ne ($taskAfter.snapshot.business_metrics.provider_activity | ConvertTo-Json -Compress)) {throw 'Provider activity changed during fixture-only drill'}
    $taskResult=@{observed_at=[DateTimeOffset]::UtcNow.ToString('o');environment=@{postgres='17.11';rabbitmq='4.4.0';seaweedfs='4.53';scope='isolated Windows native loopback';mq_restart='stop_app/start_app; Erlang VM retained';s3_restart='process stop/new process; same data directory'};prepared=$taskPrepared;mq_down=$taskMqDown;s3_down=$taskS3Down;recovered=$taskAfter;verification=$taskVerified;cleanup=$taskCleanup;paid_calls=0}
    $taskResult.base_commit=(& git -C $taskRepo rev-parse HEAD).Trim()
    $taskResult.result='PASS';$taskResult.fixture_cleanup=$true;$taskResult.financial_invariants_unchanged=$true;$taskResult.provider_attempts_unchanged=$true
    if ($LASTEXITCODE -ne 0) {throw 'Cannot bind restart evidence to current commit'}
    $taskResult | ConvertTo-Json -Depth 15 | Set-Content -Encoding utf8 docs/evidence/native-restart-drill.json
    Write-Output 'PASS: native MQ application and S3 process restart, alerts, durable redelivery, private immutable object persistence'
} finally {
    try {
        if ($taskRabbitStopped) {Invoke-TaskRabbit start_app}
        if ($taskS3Stopped) {$null=Start-TaskS3}
        if (-not $taskFixtureCleaned -and (Test-Path -LiteralPath $taskState)) {$null=Invoke-TaskProbe cleanup}
    } finally {
        foreach ($taskName in $taskSavedEnv.Keys) {[Environment]::SetEnvironmentVariable($taskName,$taskSavedEnv[$taskName],'Process')}
    }
}
