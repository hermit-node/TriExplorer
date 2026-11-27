"""Windows Recycle Bin operations via the native Shell namespace."""
from __future__ import annotations
import base64
import json
import os
import subprocess

# Use the documented Shell.Application namespace instead of manipulating $I/$R files.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject Shell.Application
$bin = $shell.NameSpace(10)
if ($null -eq $bin) { throw 'Windows Recycle Bin unavailable' }
$action = $env:TRI_RECYCLE_ACTION
$key = $env:TRI_RECYCLE_KEY
if ($action -eq 'list') {
    $rows = @(
        foreach ($item in $bin.Items()) {
            $path = [string]$item.Path
            if (-not $path) { continue }
            $name = [string]$item.ExtendedProperty('System.ItemNameDisplay')
            if (-not $name) { $name = [string]$item.Name }
            $original = [string]$item.ExtendedProperty('System.Recycle.DeletedFrom')
            $date = $item.ExtendedProperty('System.Recycle.DateDeleted')
            if ($date -is [datetime]) { $date = $date.ToString('yyyy-MM-dd HH:mm:ss') }
            [pscustomobject]@{key=$path; name=$name; original=$original; deleted=[string]$date}
        }
    )
    ConvertTo-Json -InputObject $rows -Compress
} else {
    if ($action -notin @('restore','purge')) { throw 'Invalid action' }
    $matches = @($bin.Items() | Where-Object { [string]$_.Path -ceq $key })
    if ($matches.Count -ne 1) { throw 'Recycle item no longer exists or is ambiguous' }
    $verb = if ($action -eq 'restore') { 'undelete' } else { 'delete' }
    $matches[0].InvokeVerb($verb)
    # Shell commands may be asynchronous or request native confirmation.
    $deadline = [datetime]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = @($shell.NameSpace(10).Items() | Where-Object { [string]$_.Path -ceq $key })
        if ($remaining.Count -eq 0) { '{"completed":true}'; exit 0 }
    } while ([datetime]::UtcNow -lt $deadline)
    throw 'Windows Shell did not confirm completion; refresh the bin to check the outcome'
}
"""

def _call(action, key=""):
    if os.name != "nt":
        raise OSError("Windows Recycle Bin is only available on Windows")
    env = dict(os.environ, TRI_RECYCLE_ACTION=action, TRI_RECYCLE_KEY=key)
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Sta", "-EncodedCommand", encoded],
        env=env, capture_output=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=35,
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or "Windows Shell operation failed")
    return json.loads(result.stdout.strip())

def native_entries():
    return _call("list")

def restore_native(key):
    _call("restore", key)

def purge_native(key):
    _call("purge", key)
