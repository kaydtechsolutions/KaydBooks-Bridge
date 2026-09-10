#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$CheckOnly,
    [Parameter(Mandatory=$true)][uri]$ServerUrl,
    [string]$QwcPath,
    [string]$QuickBooksInstaller,
    [string]$WebConnectorInstaller
)
$ErrorActionPreference = 'Stop'
$failures = 0
$actions = 0

function Report([string]$Name, [bool]$Passed) {
    if ($Passed) { Write-Host "PASS $Name" }
    else { Write-Host "FAIL $Name"; $script:failures++ }
}

function Get-QBSoftware {
    # Query explicit registry roots; never invoke Win32_Product (which repairs MSIs).
    $roots = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    @(Get-ItemProperty -Path $roots -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match 'QuickBooks' })
}

function Install-IntuitPackage([string]$Path) {
    $file = Get-Item -LiteralPath $Path
    if ($file.Extension -notin @('.exe', '.msi')) { throw 'Use the original Intuit EXE or MSI installer.' }
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(?i)\bIntuit\b') {
        throw 'Installer must have a valid Intuit Authenticode signature.'
    }
    if ($PSCmdlet.ShouldProcess($file.FullName, 'Install Intuit software')) {
        if ($file.Extension -eq '.msi') {
            $process = Start-Process msiexec.exe -ArgumentList @('/i', ('"' + $file.FullName + '"')) -PassThru -Wait
        } else {
            $process = Start-Process -FilePath $file.FullName -PassThru -Wait
        }
        if ($process.ExitCode -notin @(0, 3010)) { throw "Intuit setup exited $($process.ExitCode)." }
        if ($process.ExitCode -eq 3010) { Write-Host 'ACTION Restart Windows, then rerun this setup.'; $script:actions++ }
    }
}

if ($ServerUrl.Scheme -ne 'https' -or $ServerUrl.Port -ne 443 -or
    $ServerUrl.UserInfo -or $ServerUrl.Query -or $ServerUrl.Fragment -or
    $ServerUrl.AbsolutePath -ne '/' -or $ServerUrl.DnsSafeHost -notmatch '^[a-z0-9-]+\.[a-z0-9-]+\.ts\.net$') {
    throw 'ServerUrl must be the HTTPS Tailscale hostname on port 443, with no path.'
}

Report '64-bit Windows' ([Environment]::Is64BitOperatingSystem)
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $CheckOnly -and -not $admin) { throw 'Run PowerShell as Administrator, or use -CheckOnly.' }

$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
if (-not (Test-Path -LiteralPath $tailscale) -and -not $CheckOnly) {
    if (Get-Command winget.exe -ErrorAction SilentlyContinue) {
        if ($PSCmdlet.ShouldProcess('Tailscale', 'Install missing prerequisite from winget')) {
            & winget.exe install --id Tailscale.Tailscale --exact --source winget --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) { throw 'Tailscale installation failed. Install from https://tailscale.com/download/windows then rerun.' }
        }
    } else {
        Write-Host 'ACTION Install Tailscale from https://tailscale.com/download/windows (winget unavailable).'
        $actions++
    }
}
Report 'Tailscale installed' (Test-Path -LiteralPath $tailscale)
if (Test-Path -LiteralPath $tailscale) {
    $ts = & $tailscale status --json | ConvertFrom-Json
    Report 'Tailscale logged in' ($ts.BackendState -eq 'Running')
    if ($ts.BackendState -ne 'Running') {
        Write-Host 'ACTION Open Tailscale, sign in to the same tailnet as Linux, then rerun.'
        $actions++
    }
}

$software = Get-QBSoftware
$qb = @($software | Where-Object { $_.DisplayName -notmatch 'Web Connector|SDK|Tools|Tool Hub|Database Server' })
$qbwc = @($software | Where-Object { $_.DisplayName -match 'Web Connector' })
if (-not $CheckOnly) {
    if (-not $qb.Count -and $QuickBooksInstaller) { Install-IntuitPackage $QuickBooksInstaller }
    if (-not $qbwc.Count -and $WebConnectorInstaller) { Install-IntuitPackage $WebConnectorInstaller }
    $software = Get-QBSoftware
    $qb = @($software | Where-Object { $_.DisplayName -notmatch 'Web Connector|SDK|Tools|Tool Hub|Database Server' })
    $qbwc = @($software | Where-Object { $_.DisplayName -match 'Web Connector' })
}
Report 'QuickBooks Desktop installed' ($qb.Count -gt 0)
Report 'QuickBooks Web Connector installed' ($qbwc.Count -gt 0)
if (-not $qb.Count) { Write-Host 'ACTION Supply -QuickBooksInstaller with your licensed Intuit installer.'; $actions++ }
if (-not $qbwc.Count) { Write-Host 'ACTION Supply -WebConnectorInstaller with the official Intuit installer matching QuickBooks.'; $actions++ }

# Report .NET 4.x; install/repair its required version through the selected Intuit installer.
$net = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue
Report '.NET Framework 4.x runtime' ($null -ne $net -and $null -ne $net.Release)
try {
    $health = Invoke-RestMethod -Uri ($ServerUrl.AbsoluteUri.TrimEnd('/') + '/healthz') -TimeoutSec 15
    Report 'Private HTTPS / KB health' ($health.status -eq 'ready' -and $health.live_posting -eq $false)
} catch { Report 'Private HTTPS / KB health' $false }

if ($QwcPath) {
    $file = Get-Item -LiteralPath $QwcPath
    if ($file.Extension -ne '.qwc' -or $file.Length -gt 65536) { throw 'A bounded .qwc file is required.' }
    $xmlSettings = New-Object System.Xml.XmlReaderSettings
    $xmlSettings.DtdProcessing = [System.Xml.DtdProcessing]::Prohibit
    $xmlSettings.XmlResolver = $null
    $reader = [System.Xml.XmlReader]::Create($file.FullName, $xmlSettings)
    try { $qwc = New-Object System.Xml.XmlDocument; $qwc.Load($reader) }
    finally { $reader.Dispose() }
    $endpoint = [uri]$qwc.QBWCXML.AppURL
    if ($endpoint.Scheme -ne 'https' -or $endpoint.Authority -ne $ServerUrl.Authority -or
        $endpoint.AbsolutePath -notmatch '^/qbwc/[a-z][a-z0-9_-]{0,63}$' -or
        $endpoint.Query -or $endpoint.Fragment -or $endpoint.UserInfo) { throw 'QWC endpoint does not match this KB server.' }
    Report 'QWC company URL matches server' $true
    Write-Host 'ACTION Open the intended sample company as QuickBooks Admin. In Web Connector choose Add an Application and select this QWC.'
    Write-Host 'ACTION Enter its connector password locally. Keep Auto-Run off. Complete company identity binding before testing.'
    $actions++
} else {
    Write-Host 'ACTION Copy the Linux-generated .qwc to this PC, then rerun with -QwcPath.'
    $actions++
}
Write-Host "RESULT: $failures failed checks; $actions user steps. No accounting test has been run."
if ($failures) { exit 1 }
if ($actions) { exit 3 }
exit 0
