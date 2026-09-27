# Install the `onemoretask` command on Windows (Windows PowerShell 5.1 or PowerShell 7).
#   From anywhere:  irm https://raw.githubusercontent.com/astanar01/onemoretask/main/install.ps1 | iex
#   From a clone:   powershell -ExecutionPolicy Bypass -File install.ps1
# Downloads (or updates) the repo in $env:ONEMORETASK_DIR (default ~\.onemoretask) unless run from a clone,
# then puts an onemoretask.cmd in ~\.local\bin and adds that folder to your user PATH if needed.
# Also installs the task-observer Claude skill in ~\.claude\skills (skip: $env:ONEMORETASK_NO_OBSERVER=1).
# No `exit` in the body: under `irm | iex` it would close the user's PowerShell window.

# A fresh clone gets installed, our own clone gets updated, a user's copy is left alone. Never fails the install.
function Install-TaskObserver {
    $repo = $env:TASK_OBSERVER_REPO
    if (-not $repo) { $repo = 'https://github.com/rebelytics/one-skill-to-rule-them-all.git' }
    $root = $env:CLAUDE_CONFIG_DIR
    if (-not $root) { $root = Join-Path $HOME '.claude' }
    $dir = Join-Path $root 'skills\task-observer'
    $norm = { param($u) "$u".Trim().TrimEnd('/') -replace '\.git$', '' }
    $env:GIT_TERMINAL_PROMPT = '0'
    try {
        if (-not (Test-Path (Join-Path $dir 'SKILL.md'))) {
            if ((Test-Path $dir) -and @(Get-ChildItem -Force $dir).Count -gt 0) {
                Write-Host "Note: $dir has files but no SKILL.md - left alone, task-observer skill not installed." -ForegroundColor Yellow
                return
            }
            New-Item -ItemType Directory -Force -Path (Split-Path $dir) | Out-Null
            git clone -q --depth 1 "$repo" "$dir" | Out-Host
            if ($LASTEXITCODE -eq 0) { Write-Host "Installed the task-observer skill in $dir" }
            else { Write-Host 'Note: could not download the task-observer skill - skipped.' -ForegroundColor Yellow }
            return
        }
        # Check .git here: ~\.claude itself may be a repo, and git -C would read its origin.
        $origin = $null
        if (Test-Path (Join-Path $dir '.git')) { $origin = git -C "$dir" remote get-url origin 2>$null }
        if ($origin -and ((& $norm $origin) -eq (& $norm $repo))) {
            git -C "$dir" pull --ff-only -q | Out-Host
            if ($LASTEXITCODE -eq 0) { Write-Host 'Updated the task-observer skill' }
            else { Write-Host "Note: could not update the task-observer skill in $dir - kept as is." -ForegroundColor Yellow }
        } else {
            Write-Host "Kept your own task-observer skill in $dir as is."
        }
    } catch {
        Write-Host "Note: task-observer skill step failed ($($_.Exception.Message)) - skipped." -ForegroundColor Yellow
    } finally {
        Remove-Item Env:GIT_TERMINAL_PROMPT -ErrorAction SilentlyContinue
    }
}

function Install-OneMoreTask {
    $repo = $env:ONEMORETASK_REPO
    if (-not $repo) { $repo = 'https://github.com/astanar01/onemoretask.git' }
    $bin = Join-Path $HOME '.local\bin'

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host 'onemoretask needs git. Install Git for Windows: https://git-scm.com/download/win (or: winget install Git.Git)' -ForegroundColor Red
        return $false
    }

    # Prefer the py launcher: a bare "python" can be the Microsoft Store stub, which runs nothing.
    $check = 'import sys; print(sys.version_info >= (3, 7))'
    $python = $null
    foreach ($cand in @('py -3', 'python')) {
        $parts = $cand -split ' '
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        $cargs = @($parts | Select-Object -Skip 1) + @('-c', $check)
        $out = $null
        try { $out = & $parts[0] @cargs 2>$null } catch { $out = $null }
        if ("$out".Trim() -eq 'True') { $python = $cand; break }
    }
    if (-not $python) {
        Write-Host 'onemoretask needs Python 3.7 or newer. Install it from https://www.python.org/downloads/ (or: winget install Python.Python.3.12), then open a new terminal.' -ForegroundColor Red
        return $false
    }

    $dir = $null
    if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot 'server.py')) -and (Test-Path (Join-Path $PSScriptRoot 'bin\onemoretask.cmd'))) {
        $dir = $PSScriptRoot
    } else {
        $dir = $env:ONEMORETASK_DIR
        if (-not $dir) { $dir = Join-Path $HOME '.onemoretask' }
        if (Test-Path (Join-Path $dir '.git')) {
            Write-Host "Updating $dir"
            git -C "$dir" pull --ff-only -q | Out-Host
        } else {
            Write-Host "Downloading to $dir"
            git clone -q "$repo" "$dir" | Out-Host
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "git failed (exit $LASTEXITCODE)." -ForegroundColor Red
            return $false
        }
    }

    # A small .cmd, not a symlink: symlinks need admin rights or Developer Mode on Windows.
    New-Item -ItemType Directory -Force -Path $bin | Out-Null
    $shim = Join-Path $bin 'onemoretask.cmd'
    $target = Join-Path $dir 'bin\onemoretask.cmd'
    $home_ = $HOME.TrimEnd('\')
    if ($target.StartsWith($home_ + '\', [StringComparison]::OrdinalIgnoreCase)) {
        $target = '%USERPROFILE%' + $target.Substring($home_.Length)
    }
    # No "call": control passes to the repo's script, so Ctrl-C asks "Terminate batch job?" once, not twice.
    # cmd reads .cmd files in the OEM code page, so write it in that (matters for non-ASCII folder names).
    try { $oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage) } catch { $oem = [Text.Encoding]::ASCII }
    [IO.File]::WriteAllText($shim, "@echo off`r`n`"$target`" %*`r`n", $oem)
    Write-Host "Installed $shim"

    if ($env:ONEMORETASK_NO_OBSERVER -ne '1') { Install-TaskObserver }

    $inSession = @($env:Path -split ';' | ForEach-Object { $_.TrimEnd('\') }) -contains $bin.TrimEnd('\')
    $key = 'HKCU:\Environment'
    try {
        $raw = (Get-Item $key -ErrorAction Stop).GetValue('Path', '', 'DoNotExpandEnvironmentNames')
        $userEntries = @("$raw" -split ';' | Where-Object { $_ } | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') })
        if ($userEntries -notcontains $bin.TrimEnd('\')) {
            $new = $bin
            if ($raw) { $new = $raw.TrimEnd(';') + ';' + $bin }
            # Write the registry value directly to keep it REG_EXPAND_SZ (entries like %USERPROFILE%\... keep working).
            New-ItemProperty -Path $key -Name Path -Value $new -PropertyType ExpandString -Force -ErrorAction Stop | Out-Null
            # Setting any user variable broadcasts the change, so new terminals see the new PATH.
            [Environment]::SetEnvironmentVariable('ONEMORETASK_PATH_REFRESH', '1', 'User')
            [Environment]::SetEnvironmentVariable('ONEMORETASK_PATH_REFRESH', $null, 'User')
            Write-Host "Added $bin to your user PATH. Other open terminals need a restart to see it."
        }
    } catch {
        Write-Host "Could not add $bin to your user PATH ($($_.Exception.Message)). Add it yourself." -ForegroundColor Yellow
    }
    if (-not $inSession) { $env:Path = "$env:Path".TrimEnd(';') + ';' + $bin }

    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host 'Note: Send to Claude needs the Claude Code CLI (claude) on PATH.'
    }
    Write-Host "Done (using $python). Run: onemoretask"
    return $true
}

$ok = Install-OneMoreTask
if (-not $ok -and $PSCommandPath) { exit 1 }
