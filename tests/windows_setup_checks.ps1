# Exercises actual detection functions without installing or invoking applications.
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$source = Join-Path $PSScriptRoot '..\deploy\windows-setup.ps1'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$functions = $ast.FindAll({ param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -in @('Get-QBSoftware', 'Get-QBDesktopSoftware')
}, $true)
foreach ($definition in $functions) { . ([scriptblock]::Create($definition.Extent.Text)) }

function Get-ItemProperty { param($Path, $ErrorAction) @() }
function Test-Path { param($LiteralPath) $LiteralPath.EndsWith('QBWebConnector.exe') }
$script:signatureStatus = 'Valid'
function Get-AuthenticodeSignature {
    param($LiteralPath)
    [pscustomobject]@{ Status = $script:signatureStatus; SignerCertificate = [pscustomobject]@{ Subject = 'CN="Intuit, Inc."' } }
}
$detected = @(Get-QBSoftware)
if ($detected.Count -ne 1 -or $detected[0].DisplayName -ne 'QuickBooks Web Connector') {
    throw 'Bundled signed QBWC was not detected without an uninstall entry.'
}
$script:signatureStatus = 'HashMismatch'
if (@(Get-QBSoftware).Count) { throw 'Invalid executable was accepted as QBWC.' }
$auxiliary = @('QuickBooks Runtime Redistributable', 'QuickBooks Desktop File Doctor', 'QuickBooks_VC10_Debug', 'QBWebConnector') |
    ForEach-Object { [pscustomobject]@{ DisplayName = $_ } }
if (@(Get-QBDesktopSoftware $auxiliary).Count) { throw 'Auxiliary packages were mistaken for QuickBooks Desktop.' }
$desktop = [pscustomobject]@{ DisplayName = 'QuickBooks Enterprise Solutions 24.0' }
if (@(Get-QBDesktopSoftware @($desktop)).Count -ne 1) { throw 'QuickBooks Desktop was not detected.' }
Write-Host 'PASS bundled QBWC detection, signature refusal and Desktop auxiliary filtering'
