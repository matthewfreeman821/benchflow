[CmdletBinding()]
param([Parameter(Mandatory)][string]$BackupId,
      [Parameter(Mandatory)][string]$Confirm, [switch]$Demo)
. "$PSScriptRoot/Common.ps1"
$a = @('restore', '--backup-id', $BackupId, '--confirm', $Confirm)
if ($Demo) { $a += '--demo' }
Invoke-BenchFlow $a
