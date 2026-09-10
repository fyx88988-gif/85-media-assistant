param(
    [Parameter(Mandatory)][string]$ModulePath,
    [Parameter(Mandatory)][string]$Url,
    [Parameter(Mandatory)][string]$SessionRoot,
    [Parameter(Mandatory)][string]$CookieOutput,
    [Parameter(Mandatory)][string]$MediaOutput
)

$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force

$profileDir = Join-Path $SessionRoot 'app-browser-profile'
$session = $null
try {
    $session = Start-DouyinSession `
        -Url $Url `
        -TempRoot $SessionRoot `
        -ProfileDir $profileDir `
        -Persistent `
        -Headless
    $media = Wait-DouyinSessionMedia -Session $session -TimeoutSeconds 25
    $sessionCookie = Get-DouyinSessionCookies -Session $session
    Copy-Item -LiteralPath $sessionCookie -Destination $CookieOutput -Force
    $mediaJson = $media | ConvertTo-Json -Depth 8 -Compress
    [System.IO.File]::WriteAllText($MediaOutput, $mediaJson, [System.Text.UTF8Encoding]::new($false))
}
finally {
    if ($null -ne $session) {
        [void](Stop-DouyinSession -Session $session)
    }
}
