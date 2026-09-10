Set-StrictMode -Version Latest

Add-Type -AssemblyName System.Net.Http

function Get-DouyinPropertyValue {
    param(
        [AllowNull()][object]$Object,
        [Parameter(Mandatory)][string]$Name
    )

    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Test-IsDouyinUrl {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Url)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$uri)) {
        return $false
    }
    $hostName = $uri.DnsSafeHost.ToLowerInvariant()
    return $hostName -eq 'douyin.com' -or
        $hostName.EndsWith('.douyin.com') -or
        $hostName -eq 'iesdouyin.com' -or
        $hostName.EndsWith('.iesdouyin.com')
}

function Get-DouyinPageKind {
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Url)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Url, [System.UriKind]::Absolute, [ref]$uri)) {
        return 'Unknown'
    }
    if ($uri.AbsolutePath -match '^/note/') { return 'Note' }
    if ($uri.AbsolutePath -match '^/video/') { return 'Video' }
    if ($uri.AbsolutePath -match '^/user/[^/]+/?$') { return 'Profile' }
    return 'Unknown'
}

function Wait-DouyinSessionPage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Session,
        [int]$TimeoutSeconds = 20,
        [scriptblock]$CommandInvoker = { param($webSocketUrl, $method, $params) Invoke-CdpCommand -WebSocketUrl $webSocketUrl -Method $method -Params $params }
    )
    $expression = @'
JSON.stringify({
  url: location.href,
  title: document.title,
  readyState: document.readyState,
  verificationRequired: /\/(?:login|passport)(?:\/|$)/i.test(location.pathname) ||
    Array.from(document.querySelectorAll('[role="dialog"],iframe[src*="captcha"],iframe[src*="verify"]')).some(function(element) {
      var rect = element.getBoundingClientRect();
      var text = (element.innerText || element.getAttribute('src') || '').replace(/\s+/g, '');
      return rect.width > 0 && rect.height > 0 && /扫码登录|安全验证|人机验证|完成验证|验证码/.test(text);
    })
})
'@
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastUrl = [string]$Session.Url
    do {
        $result = & $CommandInvoker ([string]$Session.WebSocketUrl) 'Runtime.evaluate' @{
            expression = $expression; returnByValue = $true; awaitPromise = $true
        }
        $json = [string]$result.result.value
        if (-not [string]::IsNullOrWhiteSpace($json)) {
            $snapshot = $json | ConvertFrom-Json
            $lastUrl = [string]$snapshot.url
            if ([bool]$snapshot.verificationRequired) { throw '抖音明确要求登录或人机验证。' }
            $kind = Get-DouyinPageKind -Url $lastUrl
            if ([string]$snapshot.readyState -eq 'complete' -and $kind -ne 'Unknown') {
                return [pscustomobject]@{
                    Url = $lastUrl; Kind = $kind; Title = [string]$snapshot.title
                    VerificationRequired = $false
                }
            }
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "浏览器中的抖音页面未在规定时间内完成跳转：$lastUrl"
}

function Get-DouyinSessionAction {
    [CmdletBinding()]
    param(
        [bool]$HasSession,
        [bool]$SessionAlive
    )

    if ($HasSession -and $SessionAlive) { return 'Reuse' }
    return 'Start'
}

function Get-DouyinFailureDisposition {
    [CmdletBinding()]
    param(
        [AllowEmptyString()][string]$Message,
        [int]$BackgroundAttempt = 0
    )

    if ($Message -like '*抖音明确要求登录或人机验证*') { return 'Verify' }
    if ($BackgroundAttempt -lt 1) { return 'RetryBackground' }
    return 'Fail'
}

function Resolve-DouyinBrowserPath {
    [CmdletBinding()]
    param([string[]]$CandidatePaths)

    if ($null -eq $CandidatePaths -or $CandidatePaths.Count -eq 0) {
        $CandidatePaths = @(
            (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
            'C:\Program Files\Google\Chrome\Application\chrome.exe',
            'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
        )
    }
    foreach ($path in $CandidatePaths) {
        if (-not [string]::IsNullOrWhiteSpace($path) -and (Test-Path -LiteralPath $path)) {
            return [System.IO.Path]::GetFullPath($path)
        }
    }
    throw '未找到 Google Chrome 或 Microsoft Edge。'
}

function New-DouyinSessionDescriptor {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$TempRoot,
        [Parameter(Mandatory)][int]$Port,
        [Alias('EdgePath')][Parameter(Mandatory)][string]$BrowserPath,
        [string]$ProfileDir,
        [switch]$Persistent,
        [switch]$Headless
    )

    if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
        $ProfileDir = Join-Path $TempRoot ('edge-session-' + [guid]::NewGuid().ToString('N'))
    }
    $cookieFile = Join-Path $profileDir 'douyin-cookies.txt'
    $arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in @(
        "--user-data-dir=`"$profileDir`"",
        "--remote-debugging-port=$Port",
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-sync',
        '--disable-background-mode',
        '--new-window'
    )) { [void]$arguments.Add($argument) }
    if ([System.IO.Path]::GetFileName($BrowserPath) -ieq 'msedge.exe') {
        [void]$arguments.Add('--disable-features=msEdgeFirstRunExperience,msImplicitSignin')
    }
    else {
        [void]$arguments.Add('--disable-features=SigninIntercept')
    }
    if ($Headless) { [void]$arguments.Add('--headless=new') }
    [void]$arguments.Add($Url)
    return @{
        Url = $Url
        TempRoot = [System.IO.Path]::GetFullPath($TempRoot)
        ProfileDir = [System.IO.Path]::GetFullPath($profileDir)
        CookieFile = [System.IO.Path]::GetFullPath($cookieFile)
        Port = $Port
        BrowserPath = [System.IO.Path]::GetFullPath($BrowserPath)
        EdgePath = [System.IO.Path]::GetFullPath($BrowserPath)
        Arguments = $arguments.ToArray()
        Persistent = [bool]$Persistent
        Headless = [bool]$Headless
    }
}

function ConvertFrom-DouyinPageSnapshot {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Json)

    $snapshot = $Json | ConvertFrom-Json
    $images = [System.Collections.Generic.List[string]]::new()
    $seenImages = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($image in @($snapshot.images)) {
        $source = [string]$image.src
        if ($source -notmatch 'biz_tag=aweme_images' -or $source -notmatch 'PackSourceEnum_AWEME_DETAIL') {
            continue
        }
        $uri = [uri]$source
        $assetKey = ($uri.AbsolutePath -split '~', 2)[0]
        if ($seenImages.Add($assetKey)) { [void]$images.Add($source) }
    }

    $kind = Get-DouyinPageKind ([string]$snapshot.url)
    $audio = [System.Collections.Generic.List[string]]::new()
    $videos = [System.Collections.Generic.List[string]]::new()
    $videoOptions = [System.Collections.Generic.List[object]]::new()
    $seenMedia = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($video in @($snapshot.videos)) {
        $source = [string]$video.src
        if ([string]::IsNullOrWhiteSpace($source) -or $source -match '/douyin-pc-web/uuu_') {
            continue
        }
        if (-not $seenMedia.Add($source)) { continue }
        if ($kind -eq 'Video') {
            [void]$videos.Add($source)
            [void]$videoOptions.Add([pscustomobject]@{
                Url = $source
                BackupUrls = @($source)
                Width = [int](Get-DouyinPropertyValue -Object $video -Name 'width')
                Height = [int](Get-DouyinPropertyValue -Object $video -Name 'height')
                Duration = [double](Get-DouyinPropertyValue -Object $video -Name 'duration')
                BitRate = 0
                IsH265 = $false
                GearName = '浏览器原始媒体'
            })
        }
        elseif ($kind -eq 'Note') { [void]$audio.Add($source) }
    }

    $caption = [string](Get-DouyinPropertyValue -Object $snapshot -Name 'embeddedDescription')
    if ([string]::IsNullOrWhiteSpace($caption)) {
        $caption = [string](Get-DouyinPropertyValue -Object $snapshot -Name 'description')
    }

    return @{
        Url = [string]$snapshot.url
        Title = [string]$snapshot.title
        Caption = $caption
        Author = [string](Get-DouyinPropertyValue -Object $snapshot -Name 'author')
        PublishedAt = [string](Get-DouyinPropertyValue -Object $snapshot -Name 'publishedAt')
        Kind = $kind
        Images = $images.ToArray()
        Audio = $audio.ToArray()
        Videos = $videos.ToArray()
        VideoOptions = $videoOptions.ToArray()
    }
}

function ConvertFrom-DouyinDetailResponse {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Json,
        [Parameter(Mandatory)][string]$PageUrl,
        [string]$Title
    )

    $response = $Json | ConvertFrom-Json
    if ($null -eq $response.aweme_detail -or $null -eq $response.aweme_detail.video) {
        throw '抖音返回的作品详情中没有视频信息。'
    }

    $options = [System.Collections.Generic.List[object]]::new()
    foreach ($rate in @($response.aweme_detail.video.bit_rate)) {
        if ($null -eq $rate.play_addr -or @($rate.play_addr.url_list).Count -eq 0) { continue }
        $primaryUrl = [string]@($rate.play_addr.url_list)[0]
        if ([string]::IsNullOrWhiteSpace($primaryUrl)) { continue }
        $options.Add([pscustomobject]@{
            Url = $primaryUrl
            BackupUrls = @($rate.play_addr.url_list)
            Width = [int]$rate.play_addr.width
            Height = [int]$rate.play_addr.height
            BitRate = [long]$rate.bit_rate
            IsH265 = ([int]$rate.is_h265 -eq 1)
            GearName = [string]$rate.gear_name
        })
    }
    if ($options.Count -eq 0) {
        throw '抖音返回的作品详情中没有可下载的视频地址。'
    }

    $images = [System.Collections.Generic.List[string]]::new()
    foreach ($coverName in @('origin_cover', 'cover')) {
        $cover = Get-DouyinPropertyValue -Object $response.aweme_detail.video -Name $coverName
        $urlList = Get-DouyinPropertyValue -Object $cover -Name 'url_list'
        foreach ($source in @($urlList)) {
            $sourceUrl = [string]$source
            if (-not [string]::IsNullOrWhiteSpace($sourceUrl)) {
                [void]$images.Add($sourceUrl)
                break
            }
        }
        if ($images.Count -gt 0) { break }
    }

    $sorted = @($options | Sort-Object `
        @{ Expression = { [long]$_.Width * [long]$_.Height }; Descending = $true }, `
        @{ Expression = { [bool]$_.IsH265 }; Descending = $false }, `
        @{ Expression = { [long]$_.BitRate }; Descending = $true })
    $videos = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($option in $sorted) {
        foreach ($source in @($option.BackupUrls)) {
            $sourceUrl = [string]$source
            if (-not [string]::IsNullOrWhiteSpace($sourceUrl) -and $seen.Add($sourceUrl)) {
                [void]$videos.Add($sourceUrl)
            }
        }
    }

    if ([string]::IsNullOrWhiteSpace($Title)) { $Title = [string]$response.aweme_detail.desc }
    $author = Get-DouyinPropertyValue -Object $response.aweme_detail -Name 'author'
    $authorName = [string](Get-DouyinPropertyValue -Object $author -Name 'nickname')
    $publishedAt = ''
    $createTime = Get-DouyinPropertyValue -Object $response.aweme_detail -Name 'create_time'
    if ($null -ne $createTime -and [long]$createTime -gt 0) {
        $publishedAt = [DateTimeOffset]::FromUnixTimeSeconds([long]$createTime).ToLocalTime().ToString('yyyy-MM-dd HH:mm:ss')
    }
    return @{
        Url = $PageUrl
        Title = $Title
        Caption = [string](Get-DouyinPropertyValue -Object $response.aweme_detail -Name 'desc')
        Author = $authorName
        PublishedAt = $publishedAt
        Kind = 'Video'
        Images = $images.ToArray()
        Audio = @()
        Videos = $videos.ToArray()
        VideoOptions = $sorted
    }
}

function Export-DouyinCookies {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object[]]$Cookies,
        [Parameter(Mandatory)][string]$Path
    )

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('# Netscape HTTP Cookie File')
    $lines.Add('# Generated by 视频下载助手 from its isolated Edge session.')
    foreach ($cookie in $Cookies) {
        $domain = [string]$cookie.domain
        $normalized = $domain.TrimStart('.').ToLowerInvariant()
        $allowed = $normalized -eq 'douyin.com' -or
            $normalized.EndsWith('.douyin.com') -or
            $normalized -eq 'iesdouyin.com' -or
            $normalized.EndsWith('.iesdouyin.com')
        if (-not $allowed) {
            continue
        }

        $includeSubdomains = if ($domain.StartsWith('.')) { 'TRUE' } else { 'FALSE' }
        $secure = if ([bool]$cookie.secure) { 'TRUE' } else { 'FALSE' }
        $expires = 0
        if ($null -ne $cookie.expires -and [double]$cookie.expires -gt 0) {
            $expires = [long][math]::Floor([double]$cookie.expires)
        }
        $cookiePath = if ([string]::IsNullOrWhiteSpace([string]$cookie.path)) { '/' } else { [string]$cookie.path }
        $lines.Add(($domain, $includeSubdomains, $cookiePath, $secure, $expires, [string]$cookie.name, [string]$cookie.value -join "`t"))
    }

    if ($lines.Count -le 2) {
        throw '独立浏览器会话中未找到可用的抖音 Cookie，请在验证窗口中打开视频并完成验证。'
    }
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, [string]::Join("`n", $lines) + "`n", $encoding)
    return $Path
}

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    try {
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    }
    finally {
        $listener.Stop()
    }
}

function Select-DouyinPageTarget {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowNull()][object]$Targets)

    if ($null -eq $Targets) { return $null }

    $flattened = [System.Collections.Generic.List[object]]::new()
    foreach ($target in @($Targets)) {
        if ($null -eq $target) { continue }
        if ($target -is [System.Array]) {
            foreach ($innerTarget in $target) {
                if ($null -eq $innerTarget) { continue }
                $flattened.Add($innerTarget)
            }
        }
        else {
            $flattened.Add($target)
        }
    }
    return $flattened |
        Where-Object { $_.type -eq 'page' -and -not [string]::IsNullOrWhiteSpace([string]$_.webSocketDebuggerUrl) } |
        Select-Object -First 1
}

function Start-DouyinSession {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)][string]$TempRoot,
        [int]$Port = 0,
        [Alias('EdgePath')][string]$BrowserPath,
        [string]$ProfileDir,
        [switch]$Persistent,
        [switch]$Headless
    )

    if (-not (Test-IsDouyinUrl $Url)) {
        throw '该链接不是有效的抖音网址。'
    }
    if ([string]::IsNullOrWhiteSpace($BrowserPath)) { $BrowserPath = Resolve-DouyinBrowserPath }
    if (-not (Test-Path -LiteralPath $BrowserPath)) { throw "未找到浏览器：$BrowserPath" }
    if ($Port -le 0) {
        $Port = Get-FreeTcpPort
    }
    $session = New-DouyinSessionDescriptor `
        -Url $Url `
        -TempRoot $TempRoot `
        -Port $Port `
        -BrowserPath $BrowserPath `
        -ProfileDir $ProfileDir `
        -Persistent:$Persistent `
        -Headless:$Headless
    New-Item -ItemType Directory -Path $session.ProfileDir -Force | Out-Null

    $process = Start-Process -FilePath $session.BrowserPath -ArgumentList $session.Arguments -PassThru
    $session.Process = $process
    $session.ProcessId = $process.Id

    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    $pages = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $pages = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/list" -TimeoutSec 2
            if ($null -ne $pages) { break }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    $page = Select-DouyinPageTarget -Targets $pages
    if ($null -eq $page) {
        Stop-DouyinSession -Session $session | Out-Null
        throw '独立 Edge 验证窗口未能建立调试连接。'
    }
    $session.WebSocketUrl = [string]($page.webSocketDebuggerUrl)
    return $session
}

function Invoke-CdpCommand {
    param(
        [Parameter(Mandatory)][string]$WebSocketUrl,
        [Parameter(Mandatory)][string]$Method,
        [hashtable]$Params = @{}
    )

    $socket = [System.Net.WebSockets.ClientWebSocket]::new()
    $socket.Options.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
    $token = [System.Threading.CancellationToken]::None
    try {
        [void]$socket.ConnectAsync([uri]$WebSocketUrl, $token).GetAwaiter().GetResult()
        $requestId = Get-Random -Minimum 1000 -Maximum 999999
        $json = @{ id = $requestId; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 8
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
        $segment = [System.ArraySegment[byte]]::new($bytes)
        [void]$socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $token).GetAwaiter().GetResult()

        do {
            $stream = [System.IO.MemoryStream]::new()
            do {
                $buffer = New-Object byte[] 65536
                $receiveSegment = [System.ArraySegment[byte]]::new($buffer)
                $received = $socket.ReceiveAsync($receiveSegment, $token).GetAwaiter().GetResult()
                if ($received.Count -gt 0) {
                    $stream.Write($buffer, 0, $received.Count)
                }
            } while (-not $received.EndOfMessage)
            $responseText = [System.Text.Encoding]::UTF8.GetString($stream.ToArray())
            $response = $responseText | ConvertFrom-Json
        } while ($response.id -ne $requestId)

        return Resolve-CdpResponse -Response $response
    }
    finally {
        if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
            [void]$socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, 'done', $token).GetAwaiter().GetResult()
        }
        $socket.Dispose()
    }
}

function Get-DouyinDetailResponseBody {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$WebSocketUrl,
        [int]$TimeoutSeconds = 20
    )

    $socket = [System.Net.WebSockets.ClientWebSocket]::new()
    $socket.Options.Proxy = [System.Net.GlobalProxySelection]::GetEmptyWebProxy()
    $cancellation = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds($TimeoutSeconds))
    $token = $cancellation.Token
    try {
        [void]$socket.ConnectAsync([uri]$WebSocketUrl, $token).GetAwaiter().GetResult()
        $send = {
            param([int]$Id, [string]$Method, [hashtable]$Params)
            $json = @{ id = $Id; method = $Method; params = $Params } | ConvertTo-Json -Compress -Depth 8
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
            $segment = [System.ArraySegment[byte]]::new($bytes)
            [void]$socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $token).GetAwaiter().GetResult()
        }
        $receive = {
            $stream = [System.IO.MemoryStream]::new()
            do {
                $buffer = New-Object byte[] 65536
                $segment = [System.ArraySegment[byte]]::new($buffer)
                $received = $socket.ReceiveAsync($segment, $token).GetAwaiter().GetResult()
                if ($received.Count -gt 0) { $stream.Write($buffer, 0, $received.Count) }
            } while (-not $received.EndOfMessage)
            return ([System.Text.Encoding]::UTF8.GetString($stream.ToArray()) | ConvertFrom-Json)
        }

        & $send 1 'Network.enable' @{}
        do {
            $message = & $receive
            $idProperty = $message.PSObject.Properties['id']
        } while ($null -eq $idProperty -or [int]$idProperty.Value -ne 1)
        if ($null -ne $message.PSObject.Properties['error']) { Resolve-CdpResponse -Response $message | Out-Null }

        & $send 2 'Page.reload' @{ ignoreCache = $true }
        $requestId = $null
        $loaded = $false
        while (-not $loaded) {
            $message = & $receive
            $methodProperty = $message.PSObject.Properties['method']
            if ($null -eq $methodProperty) { continue }
            $method = [string]$methodProperty.Value
            if ($method -eq 'Network.responseReceived' -and
                [string]$message.params.response.url -match '/aweme/v1/web/aweme/detail/' -and
                [string]$message.params.response.mimeType -eq 'application/json') {
                $requestId = [string]$message.params.requestId
            }
            if (-not [string]::IsNullOrWhiteSpace($requestId) -and
                $method -eq 'Network.loadingFinished' -and
                [string]$message.params.requestId -eq $requestId) {
                $loaded = $true
            }
        }

        & $send 3 'Network.getResponseBody' @{ requestId = $requestId }
        do {
            $message = & $receive
            $idProperty = $message.PSObject.Properties['id']
        } while ($null -eq $idProperty -or [int]$idProperty.Value -ne 3)
        $result = Resolve-CdpResponse -Response $message
        $body = [string]$result.body
        $base64Property = $result.PSObject.Properties['base64Encoded']
        if ($null -ne $base64Property -and [bool]$base64Property.Value) {
            $body = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($body))
        }
        if ([string]::IsNullOrWhiteSpace($body)) { throw '抖音作品详情响应为空。' }
        return $body
    }
    catch [System.OperationCanceledException] {
        throw '等待抖音高清资源清单超时。'
    }
    finally {
        $cancellation.Dispose()
        if ($socket.State -eq [System.Net.WebSockets.WebSocketState]::Open) {
            try {
                [void]$socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, 'done', [System.Threading.CancellationToken]::None).GetAwaiter().GetResult()
            }
            catch {}
        }
        $socket.Dispose()
    }
}

function Resolve-CdpResponse {
    [CmdletBinding()]
    param([Parameter(Mandatory)][object]$Response)

    $errorProperty = $Response.PSObject.Properties['error']
    if ($null -ne $errorProperty -and $null -ne $errorProperty.Value) {
        $messageProperty = $errorProperty.Value.PSObject.Properties['message']
        $message = if ($null -ne $messageProperty) { [string]$messageProperty.Value } else { [string]$errorProperty.Value }
        throw "Edge 调试接口错误：$message"
    }
    $resultProperty = $Response.PSObject.Properties['result']
    if ($null -eq $resultProperty) {
        throw 'Edge 调试接口响应缺少 result。'
    }
    return $resultProperty.Value
}

function Get-DouyinSessionCookies {
    [CmdletBinding()]
    param([Parameter(Mandatory)][hashtable]$Session)

    $result = Invoke-CdpCommand -WebSocketUrl $Session.WebSocketUrl -Method 'Storage.getCookies'
    if ($null -eq $result.cookies) {
        throw '无法从独立 Edge 会话读取抖音 Cookie。'
    }
    return Export-DouyinCookies -Cookies @($result.cookies) -Path $Session.CookieFile
}

function Test-DouyinSessionAlive {
    [CmdletBinding()]
    param([AllowNull()][hashtable]$Session)

    if ($null -eq $Session -or
        -not $Session.ContainsKey('Port') -or
        -not $Session.ContainsKey('WebSocketUrl') -or
        [string]::IsNullOrWhiteSpace([string]$Session.WebSocketUrl)) {
        return $false
    }
    if ($Session.ContainsKey('Process') -and $null -ne $Session.Process) {
        try {
            if ($Session.Process.HasExited) { return $false }
        }
        catch { return $false }
    }
    try {
        $targets = Invoke-RestMethod -Uri "http://127.0.0.1:$($Session.Port)/json/list" -TimeoutSec 2
        return $null -ne (Select-DouyinPageTarget -Targets $targets)
    }
    catch {
        return $false
    }
}

function Set-DouyinSessionUrl {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Session,
        [Parameter(Mandatory)][string]$Url
    )

    if (-not (Test-IsDouyinUrl $Url)) { throw '该链接不是有效的抖音网址。' }
    if (-not (Test-DouyinSessionAlive -Session $Session)) { throw '后台抖音会话已断开。' }
    [void](Invoke-CdpCommand `
        -WebSocketUrl $Session.WebSocketUrl `
        -Method 'Page.navigate' `
        -Params @{ url = $Url })
    $Session.Url = $Url
    return $Session
}

function Wait-DouyinSessionMedia {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Session,
        [int]$TimeoutSeconds = 20,
        [scriptblock]$CommandInvoker = { param($webSocketUrl, $method, $params) Invoke-CdpCommand -WebSocketUrl $webSocketUrl -Method $method -Params $params }
    )

    $expression = @'
JSON.stringify({
  url: location.href,
  title: document.title,
  description: (document.querySelector('meta[name="description"]') || document.querySelector('meta[property="og:description"]') || {}).content || '',
  embeddedDescription: (function () {
    var meta = (document.querySelector('meta[name="description"]') || document.querySelector('meta[property="og:description"]') || {}).content || '';
    var seed = meta.replace(/\s+/g, '').slice(0, 32);
    var pathMatch = location.pathname.match(/\/(?:video|note)\/(\d+)/);
    var currentId = pathMatch ? pathMatch[1] : '';
    var candidates = [];
    function addCandidate(value) {
      if (typeof value !== 'string' || !value.trim()) return;
      var comparable = value.replace(/\s+/g, '');
      if (seed && comparable.indexOf(seed) !== 0) return;
      candidates.push(value);
    }
    try {
      (self.__pace_f || []).forEach(function (entry) {
        var chunk = entry && entry[1];
        if (typeof chunk !== 'string') return;
        if (currentId && chunk.indexOf('"awemeId":"' + currentId + '"') < 0) return;
        var pattern = /"desc":("(?:\\.|[^"\\])*")/g;
        var match;
        while ((match = pattern.exec(chunk)) !== null) {
          try { addCandidate(JSON.parse(match[1])); } catch (error) {}
        }
      });
    } catch (error) {}
    if (candidates.length === 0) {
      var root = document.querySelector('[data-e2e="note-detail"]') || document.body;
      if (root) {
        Array.from(root.querySelectorAll('span')).forEach(function (element) {
          addCandidate(element.innerText || '');
        });
      }
    }
    candidates.sort(function (left, right) { return right.length - left.length; });
    return candidates[0] || '';
  })(),
  author: (document.querySelector('meta[name="author"]') || {}).content || '',
  verificationRequired: (function () {
    if (/\/(?:login|passport)(?:\/|$)/i.test(location.pathname)) return true;
    var elements = Array.from(document.querySelectorAll('[role="dialog"], iframe[src*="captcha"], iframe[src*="verify"]'));
    return elements.some(function (element) {
      var rect = element.getBoundingClientRect();
      var style = getComputedStyle(element);
      if (rect.width <= 0 || rect.height <= 0 || style.display === 'none' || style.visibility === 'hidden') return false;
      var text = (element.innerText || element.getAttribute('src') || '').replace(/\s+/g, '');
      return /扫码登录|安全验证|人机验证|完成验证|验证码/.test(text);
    });
  })(),
  readyState: document.readyState,
  images: Array.from(document.images).map(function (image) {
    return {
      src: image.currentSrc || image.src || '',
      naturalWidth: image.naturalWidth || 0,
      naturalHeight: image.naturalHeight || 0
    };
  }),
  videos: Array.from(document.querySelectorAll('video')).map(function (video) {
    var source = video.querySelector('source');
    return {
      src: video.currentSrc || video.src || (source ? source.src : ''),
      width: video.videoWidth || 0,
      height: video.videoHeight || 0,
      duration: Number.isFinite(video.duration) ? video.duration : 0
    };
  })
})
'@
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastUrl = [string]$Session.Url
    $detailAttempted = $false
    do {
        try {
            $result = & $CommandInvoker `
                ([string]$Session.WebSocketUrl) `
                'Runtime.evaluate' `
                @{ expression = $expression; returnByValue = $true; awaitPromise = $true }
            $json = [string]$result.result.value
            if (-not [string]::IsNullOrWhiteSpace($json)) {
                $snapshot = $json | ConvertFrom-Json
                $lastUrl = [string]$snapshot.url
                if ([bool](Get-DouyinPropertyValue -Object $snapshot -Name 'verificationRequired')) {
                    throw '抖音明确要求登录或人机验证。'
                }
                $media = ConvertFrom-DouyinPageSnapshot -Json $json
                if ($media.Kind -eq 'Profile') { throw '抖音链接指向作者主页。' }
                if ($snapshot.readyState -eq 'complete') {
                    if ($media.Kind -eq 'Video' -and -not $detailAttempted) {
                        $detailAttempted = $true
                        try {
                            $remainingSeconds = [Math]::Max(5, [int][Math]::Ceiling(($deadline - [DateTime]::UtcNow).TotalSeconds))
                            $detailJson = Get-DouyinDetailResponseBody -WebSocketUrl $Session.WebSocketUrl -TimeoutSeconds $remainingSeconds
                            return ConvertFrom-DouyinDetailResponse -Json $detailJson -PageUrl $media.Url -Title $media.Title
                        }
                        catch {
                            if ($media.Videos.Count -gt 0) { return $media }
                            throw
                        }
                    }
                    if ($media.Kind -eq 'Video' -and $media.Videos.Count -gt 0) { return $media }
                    if ($media.Kind -eq 'Note' -and $media.Images.Count -gt 0) { return $media }
                }
            }
        }
        catch {
            if ($_.Exception.Message -like '*抖音链接指向作者主页。*') { throw }
            if ($_.Exception.Message -like '*抖音明确要求登录或人机验证*') { throw }
            if ([DateTime]::UtcNow -ge $deadline) { throw }
        }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "浏览器中的抖音页面未在规定时间内加载完成：$lastUrl"
}

function Get-DouyinMediaExtension {
    param(
        [string]$Url,
        [string]$ContentType,
        [string]$Fallback
    )

    $mediaType = ([string]$ContentType).ToLowerInvariant()
    switch -Regex ($mediaType) {
        '^image/webp' { return '.webp' }
        '^image/jpeg' { return '.jpg' }
        '^image/png' { return '.png' }
        '^audio/mpeg' { return '.mp3' }
        '^audio/mp4' { return '.m4a' }
        '^video/mp4' { return '.mp4' }
    }
    try {
        $extension = [System.IO.Path]::GetExtension(([uri]$Url).AbsolutePath)
        if (-not [string]::IsNullOrWhiteSpace($extension) -and $extension.Length -le 6) {
            return $extension.ToLowerInvariant()
        }
    }
    catch {}
    return $Fallback
}

function Save-DouyinNoteMedia {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Media,
        [Parameter(Mandatory)][string]$OutputDirectory,
        [ValidateSet('best', 'mp4', 'audio')][string]$Mode = 'best',
        [switch]$IncludeImages,
        [switch]$IncludeAudio,
        [switch]$FlatOutput,
        [ValidateRange(0, 1000)][int]$ImageLimit = 0,
        [string]$ImageBaseName
    )

    $uri = [uri]$Media.Url
    $noteId = ($uri.AbsolutePath.Trim('/') -split '/')[-1]
    if ([string]::IsNullOrWhiteSpace($noteId)) { $noteId = [guid]::NewGuid().ToString('N') }
    $folder = if ($FlatOutput) { $OutputDirectory } else { Join-Path $OutputDirectory ("抖音图文_$noteId") }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null

    $hasExplicitSelection = $PSBoundParameters.ContainsKey('IncludeImages') -or $PSBoundParameters.ContainsKey('IncludeAudio')
    $saveImages = if ($hasExplicitSelection) { [bool]$IncludeImages } else { $Mode -ne 'audio' }
    $saveAudio = if ($hasExplicitSelection) { [bool]$IncludeAudio } else { $true }

    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AutomaticDecompression = [System.Net.DecompressionMethods]::GZip -bor [System.Net.DecompressionMethods]::Deflate
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(60)
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36')
    $client.DefaultRequestHeaders.Referrer = [uri]'https://www.douyin.com/'
    $outputFiles = [System.Collections.Generic.List[string]]::new()
    try {
        if ($saveImages) {
            $index = 1
            foreach ($source in @($Media.Images)) {
                $response = $client.GetAsync([string]$source).GetAwaiter().GetResult()
                [void]$response.EnsureSuccessStatusCode()
                $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
                $extension = Get-DouyinMediaExtension -Url ([string]$source) -ContentType ([string]$response.Content.Headers.ContentType) -Fallback '.jpg'
                $fileName = if ([string]::IsNullOrWhiteSpace($ImageBaseName)) {
                    '{0:D2}{1}' -f $index, $extension
                }
                else {
                    "$ImageBaseName$extension"
                }
                $path = Join-Path $folder $fileName
                [System.IO.File]::WriteAllBytes($path, $bytes)
                [void]$outputFiles.Add($path)
                $index++
                if ($ImageLimit -gt 0 -and $index -gt $ImageLimit) { break }
            }
        }

        if ($saveAudio) {
            $audioIndex = 1
            foreach ($source in @($Media.Audio)) {
                $response = $client.GetAsync([string]$source).GetAwaiter().GetResult()
                [void]$response.EnsureSuccessStatusCode()
                $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
                $extension = Get-DouyinMediaExtension -Url ([string]$source) -ContentType ([string]$response.Content.Headers.ContentType) -Fallback '.mp4'
                $suffix = if ($Media.Audio.Count -gt 1) { "_$audioIndex" } else { '' }
                $path = Join-Path $folder ("配乐$suffix$extension")
                [System.IO.File]::WriteAllBytes($path, $bytes)
                [void]$outputFiles.Add($path)
                $audioIndex++
            }
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
    }

    if ($outputFiles.Count -eq 0) {
        throw '该抖音图文作品没有可下载的原图或配乐。'
    }
    return $outputFiles.ToArray()
}

function Get-DouyinVideoSources {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Media,
        [ValidateSet('best', 'mp4', 'audio')][string]$Mode = 'best',
        [ValidateSet('best', '480', '720', '1080', '1440', '2160', '4320')][string]$MaxResolution = 'best'
    )

    $sources = [System.Collections.Generic.List[string]]::new()
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    if ($Media.ContainsKey('VideoOptions')) {
        $options = @($Media.VideoOptions)
        if ($Mode -eq 'mp4') { $options = @($options | Where-Object { -not [bool]$_.IsH265 }) }
        if ($MaxResolution -ne 'best') {
            $heightLimit = [int]$MaxResolution
            $limited = @($options | Where-Object { [int]$_.Height -le $heightLimit })
            if ($limited.Count -gt 0) { $options = $limited }
            elseif ($options.Count -gt 0) {
                $options = @($options | Sort-Object @{ Expression = { [int]$_.Height }; Descending = $false } | Select-Object -First 1)
            }
        }
        $options = @($options | Sort-Object `
            @{ Expression = { [long]$_.Width * [long]$_.Height }; Descending = $true }, `
            @{ Expression = { [long]$_.BitRate }; Descending = $true })
        foreach ($option in $options) {
            foreach ($source in @($option.BackupUrls)) {
                $sourceUrl = [string]$source
                if (-not [string]::IsNullOrWhiteSpace($sourceUrl) -and $seen.Add($sourceUrl)) {
                    [void]$sources.Add($sourceUrl)
                }
            }
        }
    }
    if ($sources.Count -eq 0) {
        foreach ($source in @($Media.Videos)) {
            $sourceUrl = [string]$source
            if (-not [string]::IsNullOrWhiteSpace($sourceUrl) -and $seen.Add($sourceUrl)) {
                [void]$sources.Add($sourceUrl)
            }
        }
    }
    return $sources.ToArray()
}

function ConvertTo-DouyinCompatibleMp4 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$FfmpegPath
    )

    $ffprobePath = Join-Path (Split-Path -Parent $FfmpegPath) 'ffprobe.exe'
    if (-not (Test-Path -LiteralPath $FfmpegPath) -or -not (Test-Path -LiteralPath $ffprobePath)) {
        throw '兼容 MP4 需要 FFmpeg 和 ffprobe。'
    }
    $codec = [string](& $ffprobePath -v error -select_streams 'v:0' -show_entries 'stream=codec_name' -of 'default=noprint_wrappers=1:nokey=1' -- $Path 2>$null)
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($codec)) {
        throw '无法检查下载视频的实际编码。'
    }
    if ($codec.Trim() -eq 'h264') { return $Path }

    $convertedPath = $Path + '.h264.mp4'
    try {
        & $FfmpegPath `
            -hide_banner `
            -loglevel error `
            -y `
            -i $Path `
            -map '0:v:0' `
            -map '0:a?' `
            -c:v libx264 `
            -preset medium `
            -crf 18 `
            -pix_fmt yuv420p `
            -c:a aac `
            -b:a 192k `
            -movflags '+faststart' `
            $convertedPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $convertedPath)) {
            throw '无法将抖音视频转换为 H.264 兼容 MP4。'
        }
        Move-Item -LiteralPath $convertedPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $convertedPath) {
            Remove-Item -LiteralPath $convertedPath -Force -ErrorAction SilentlyContinue
        }
    }
    return $Path
}

function Save-DouyinVideoMedia {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Media,
        [Parameter(Mandatory)][string]$OutputDirectory,
        [ValidateSet('best', 'mp4', 'audio')][string]$Mode = 'best',
        [ValidateSet('best', '480', '720', '1080', '1440', '2160', '4320')][string]$MaxResolution = 'best',
        [string]$FfmpegPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\ffmpeg\ffmpeg.exe'),
        [ValidateRange(1, 300)][int]$RequestTimeoutSeconds = 60,
        [ValidateRange(1, 600)][int]$TotalTimeoutSeconds = 120
    )

    if ($Media.Kind -ne 'Video' -or $Media.Videos.Count -eq 0) {
        throw '该抖音视频页面没有可下载的媒体地址。'
    }
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $uri = [uri]$Media.Url
    $videoId = ($uri.AbsolutePath.Trim('/') -split '/')[-1]
    if ([string]::IsNullOrWhiteSpace($videoId)) { $videoId = [guid]::NewGuid().ToString('N') }
    $videoPath = Join-Path $OutputDirectory ("抖音视频_$videoId.mp4")
    $partPath = $videoPath + '.part'

    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AutomaticDecompression = [System.Net.DecompressionMethods]::GZip -bor [System.Net.DecompressionMethods]::Deflate
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
    $client.DefaultRequestHeaders.UserAgent.ParseAdd('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36')
    $client.DefaultRequestHeaders.Referrer = [uri]$Media.Url
    $downloaded = $false
    $lastDownloadError = $null
    $downloadDeadline = [DateTime]::UtcNow.AddSeconds($TotalTimeoutSeconds)
    try {
        foreach ($source in @(Get-DouyinVideoSources -Media $Media -Mode $Mode -MaxResolution $MaxResolution)) {
            $remainingSeconds = [int][Math]::Ceiling(($downloadDeadline - [DateTime]::UtcNow).TotalSeconds)
            if ($remainingSeconds -le 0) { break }
            $requestSeconds = [Math]::Min($RequestTimeoutSeconds, $remainingSeconds)
            $cancellation = [System.Threading.CancellationTokenSource]::new([TimeSpan]::FromSeconds($requestSeconds))
            $response = $null
            try {
                $response = $client.GetAsync([string]$source, $cancellation.Token).GetAwaiter().GetResult()
                [void]$response.EnsureSuccessStatusCode()
                $bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
                [System.IO.File]::WriteAllBytes($partPath, $bytes)
                Move-Item -LiteralPath $partPath -Destination $videoPath -Force
                $downloaded = $true
                break
            }
            catch {
                $lastDownloadError = $_
                if (Test-Path -LiteralPath $partPath) { Remove-Item -LiteralPath $partPath -Force -ErrorAction SilentlyContinue }
            }
            finally {
                if ($null -ne $response) { $response.Dispose() }
                $cancellation.Dispose()
            }
        }
    }
    finally {
        $client.Dispose()
        $handler.Dispose()
        if (Test-Path -LiteralPath $partPath) { Remove-Item -LiteralPath $partPath -Force -ErrorAction SilentlyContinue }
    }
    if (-not $downloaded) {
        if ([DateTime]::UtcNow -ge $downloadDeadline) {
            throw "抖音视频媒体源在 $TotalTimeoutSeconds 秒内没有完成响应。"
        }
        if ($null -ne $lastDownloadError) { throw $lastDownloadError }
        throw '抖音视频没有可用的下载地址。'
    }

    if ($Mode -eq 'mp4') {
        [void](ConvertTo-DouyinCompatibleMp4 -Path $videoPath -FfmpegPath $FfmpegPath)
    }
    if ($Mode -ne 'audio') { return $videoPath }
    if (-not (Test-Path -LiteralPath $FfmpegPath)) { throw "未找到 FFmpeg：$FfmpegPath" }
    $audioPath = Join-Path $OutputDirectory ("抖音音频_$videoId.mp3")
    & $FfmpegPath -hide_banner -loglevel error -y -i $videoPath -vn -codec:a libmp3lame -q:a 2 $audioPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $audioPath)) {
        throw '无法从抖音视频提取 MP3 音频。'
    }
    Remove-Item -LiteralPath $videoPath -Force
    return $audioPath
}

function Stop-DouyinSession {
    [CmdletBinding()]
    param([Parameter(Mandatory)][hashtable]$Session)

    if ($Session.ContainsKey('ProcessId') -and $Session.ProcessId) {
        & taskkill.exe /PID $Session.ProcessId /T /F 2>$null | Out-Null
        if ($Session.ContainsKey('Process') -and $null -ne $Session.Process) {
            try { [void]$Session.Process.WaitForExit(5000) } catch {}
        }
    }

    if (-not $Session.ContainsKey('ProfileDir') -or [string]::IsNullOrWhiteSpace([string]$Session.ProfileDir)) {
        if ($Session.ContainsKey('Persistent') -and $Session.Persistent) {
            if ($Session.ContainsKey('CookieFile') -and (Test-Path -LiteralPath $Session.CookieFile)) {
                Remove-Item -LiteralPath $Session.CookieFile -Force -ErrorAction SilentlyContinue
            }
            return $true
        }
        return $false
    }
    $profileDir = [System.IO.Path]::GetFullPath([string]$Session.ProfileDir)
    $leaf = Split-Path -Leaf $profileDir
    $isDisposableProfile = $false
    if ($Session.ContainsKey('TempRoot') -and -not [string]::IsNullOrWhiteSpace([string]$Session.TempRoot)) {
        $tempRoot = [System.IO.Path]::GetFullPath([string]$Session.TempRoot).TrimEnd('\') + '\'
        $isDisposableProfile = $profileDir.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
            $leaf.StartsWith('edge-session-')
    }

    if ($Session.ContainsKey('Persistent') -and $Session.Persistent -and -not $isDisposableProfile) {
        if ($Session.ContainsKey('CookieFile') -and (Test-Path -LiteralPath $Session.CookieFile)) {
            Remove-Item -LiteralPath $Session.CookieFile -Force -ErrorAction SilentlyContinue
        }
        return $true
    }

    if (-not $isDisposableProfile) {
        throw '拒绝清理不属于视频下载助手的浏览器目录。'
    }

    for ($attempt = 0; $attempt -lt 5; $attempt++) {
        if (-not (Test-Path -LiteralPath $profileDir)) { return $true }
        try {
            Remove-Item -LiteralPath $profileDir -Recurse -Force -ErrorAction Stop
            return $true
        }
        catch {
            Start-Sleep -Milliseconds 300
        }
    }
    return -not (Test-Path -LiteralPath $profileDir)
}

function Test-VideoLooksStatic {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Source,
        [string]$FfmpegPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\ffmpeg\ffmpeg.exe'),
        [ValidateRange(3, 15)][int]$SampleSeconds = 4
    )

    if (-not (Test-Path -LiteralPath $FfmpegPath)) { return $false }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $lines = @(& $FfmpegPath `
            -hide_banner `
            -nostdin `
            -t $SampleSeconds `
            -rw_timeout 10000000 `
            -i $Source `
            -an `
            -vf 'freezedetect=n=0.003:d=2' `
            -f null `
            - 2>&1)
        $ffmpegExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($ffmpegExitCode -ne 0) { return $false }
    $text = $lines -join "`n"
    $durations = @([regex]::Matches($text, 'freeze_duration:\s*(?<value>\d+(?:\.\d+)?)') | ForEach-Object {
        [double]$_.Groups['value'].Value
    })
    if ($durations.Count -gt 0 -and (($durations | Measure-Object -Maximum).Maximum -ge ($SampleSeconds - 1.0))) {
        return $true
    }
    $startMatch = [regex]::Match($text, 'freeze_start:\s*(?<value>\d+(?:\.\d+)?)')
    $hasFreezeEnd = [regex]::IsMatch($text, 'freeze_end:')
    return $startMatch.Success -and ([double]$startMatch.Groups['value'].Value -le 0.2) -and -not $hasFreezeEnd
}

function Export-VideoFrameImage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$VideoPath,
        [Parameter(Mandatory)][string]$OutputPath,
        [string]$FfmpegPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'runtime\ffmpeg\ffmpeg.exe')
    )

    if (-not (Test-Path -LiteralPath $FfmpegPath)) { throw "未找到 FFmpeg：$FfmpegPath" }
    $parent = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    & $FfmpegPath -hide_banner -loglevel error -nostdin -y -ss 0.5 -i $VideoPath -frames:v 1 -q:v 2 $OutputPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $OutputPath)) {
        throw '无法从视频中提取画面图。'
    }
    return $OutputPath
}

Export-ModuleMember -Function Test-IsDouyinUrl, Get-DouyinPageKind, Get-DouyinSessionAction, Get-DouyinFailureDisposition, Resolve-DouyinBrowserPath, New-DouyinSessionDescriptor, ConvertFrom-DouyinPageSnapshot, ConvertFrom-DouyinDetailResponse, Export-DouyinCookies, Select-DouyinPageTarget, Resolve-CdpResponse, Start-DouyinSession, Test-DouyinSessionAlive, Set-DouyinSessionUrl, Get-DouyinSessionCookies, Wait-DouyinSessionPage, Wait-DouyinSessionMedia, Save-DouyinNoteMedia, Get-DouyinVideoSources, Save-DouyinVideoMedia, Stop-DouyinSession, Test-VideoLooksStatic, Export-VideoFrameImage
