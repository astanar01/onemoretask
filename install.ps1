# Install the `onemoretask` command on Windows (Windows PowerShell 5.1 or PowerShell 7).
#   From anywhere:  irm https://raw.githubusercontent.com/astanar01/onemoretask/main/install.ps1 | iex
#   From a clone:   powershell -ExecutionPolicy Bypass -File install.ps1
# Downloads (or updates) the repo in $env:ONEMORETASK_DIR (default ~\.onemoretask) unless run from a clone,
# then puts an onemoretask.cmd in ~\.local\bin and adds that folder to your user PATH if needed.
# No `exit` in the body: under `irm | iex` it would close the user's PowerShell window.

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
