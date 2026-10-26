[CmdletBinding()]
param([switch]$Demo)
. "$PSScriptRoot/Common.ps1"
$a = @('start')
if ($Demo) { $a += '--demo' }
Invoke-BenchFlow $a
