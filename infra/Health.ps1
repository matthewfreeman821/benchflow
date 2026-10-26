[CmdletBinding()]
param()
. "$PSScriptRoot/Common.ps1"
Invoke-BenchFlow @('health')
