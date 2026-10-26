[CmdletBinding()]
param([switch]$Demo)
. "$PSScriptRoot/Common.ps1"
$a = @('stop')
if ($Demo) { $a += '--demo' }
Invoke-BenchFlow $a
