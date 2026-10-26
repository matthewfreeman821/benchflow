[CmdletBinding()]
param([Parameter(Mandatory)][ValidateSet('backup','bootstrap','recover','demo-reset')][string]$Action,
      [string]$LoginId, [string]$DisplayName, [string]$AccountId,
      [ValidateSet('in_person','phone')][string]$VerificationMethod,
      [string]$Confirm, [switch]$Demo)
. "$PSScriptRoot/Common.ps1"
$a = @($Action)
if ($LoginId) { $a += @('--login-id', $LoginId) }
if ($DisplayName) { $a += @('--display-name', $DisplayName) }
if ($AccountId) { $a += @('--account-id', $AccountId) }
if ($VerificationMethod) { $a += @('--verification-method', $VerificationMethod) }
if ($Confirm) { $a += @('--confirm', $Confirm) }
if ($Demo) { $a += '--demo' }
Invoke-BenchFlow $a
