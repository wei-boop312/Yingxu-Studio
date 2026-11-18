<#
.SYNOPSIS
Store one provider key in this Windows user's environment using masked input.
.DESCRIPTION
Run this script in your own PowerShell window. Never pass a key on the command line.
The script prints only the variable name and result, and never calls a provider API.
#>
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('bfl','hubway','gemini','bailian')]
    [string]$Provider,
    [switch]$Replace,
    [switch]$ExplainOnly
)
$ErrorActionPreference = 'Stop'
$providerVariables = @{bfl='BFL_API_KEY';hubway='HUBWAY_API_KEY';gemini='GEMINI_API_KEY';bailian='DASHSCOPE_API_KEY'}
$providerVariable = $providerVariables[$Provider]
if ($ExplainOnly) {
    @{result='INSTRUCTIONS_ONLY';variable=$providerVariable;storage='Windows user and current PowerShell environment';masked_input=$true;paid_calls=0} | ConvertTo-Json -Compress
    return
}
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This helper requires Windows. Configure the private environment on your server instead.'
}
$existingProviderKey = [Environment]::GetEnvironmentVariable($providerVariable,'User')
if ($existingProviderKey -and -not $Replace) {
    [Environment]::SetEnvironmentVariable($providerVariable,$existingProviderKey,'Process')
    $existingProviderKey = $null
    @{result='EXISTING_USER_KEY_LOADED';variable=$providerVariable;paid_calls=0} | ConvertTo-Json -Compress
    return
}
$existingProviderKey = $null
$secureProviderKey = Read-Host -Prompt "Paste $providerVariable locally (input is hidden)" -AsSecureString
$plainProviderKey = $null
try {
    $plainProviderKey = [Net.NetworkCredential]::new('', $secureProviderKey).Password
    if ($plainProviderKey -notmatch '^[\x21-\x7e]{1,4096}$') {
        throw 'Invalid key format; no environment value was changed.'
    }
    [Environment]::SetEnvironmentVariable($providerVariable,$plainProviderKey,'User')
    [Environment]::SetEnvironmentVariable($providerVariable,$plainProviderKey,'Process')
    @{result='USER_KEY_CONFIGURED';variable=$providerVariable;paid_calls=0} | ConvertTo-Json -Compress
}
finally {
    $plainProviderKey = $null
    if ($secureProviderKey) { $secureProviderKey.Dispose() }
}
