<#
.SYNOPSIS
    Installs the twixt runtime: mpv, VapourSynth and the interpolation plugins.

.DESCRIPTION
    Everything lands in .\runtime\ -- nothing is installed system-wide, no
    registry keys, no PATH changes. Delete the folder to uninstall.

    Release URLs on GitHub change every build, so assets are resolved through
    the releases API by filename pattern rather than hard-coded links.

.PARAMETER Component
    Install only the named components. Default is all of them.

.PARAMETER Force
    Re-download and overwrite components that are already present.

.PARAMETER WhatIf
    Resolve and list every download without fetching anything.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -WhatIf
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -Component mpv,vapoursynth
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [ValidateSet('full', 'light')]
    [string]   $Tier = 'full',
    [string[]] $Component,
    [switch]   $Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

$Root    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Runtime = Join-Path $Root 'runtime'
$Cache   = Join-Path $Runtime '_downloads'
$VsDir   = Join-Path $Runtime 'vapoursynth'
# VapourSynth portable autoloads this directory, so plugins go here.
$Plugins = Join-Path $VsDir 'vapoursynth64\plugins'

# --- component manifest -----------------------------------------------------
# Kind 'github' : resolve the latest release asset matching 'Match'
#   Match       : wildcard the asset filename must match
#   Dest        : where the archive contents end up
#   Split       : asset is a multi-part .7z (.001/.002/...); fetch every part
# Kind 'vsrepo' : install through VapourSynth's own package manager, which is
#                 how these plugins are actually distributed -- their GitHub
#                 releases carry no Windows binaries.

$Manifest = @(
    @{ Name = 'vapoursynth'
       Tier = 'core'
       Kind = 'vapoursynth'
       Repo = 'vapoursynth/vapoursynth'
       Match = 'VapourSynth64-Portable-R*.zip'
       Dest = $VsDir
       Probe = 'vsscript.dll'
       Why  = 'the frame-processing runtime the filter script runs inside' }

    @{ Name = 'mpv'
       Tier = 'core'
       Kind = 'github'
       Repo = 'shinchiro/mpv-winbuild-cmake'
       Match = 'mpv-x86_64-2*.7z'
       Dest = (Join-Path $Runtime 'mpv')
       Probe = 'mpv.exe'
       Why  = 'the player; its vapoursynth filter is where interpolation happens' }

    @{ Name = 'plugins'
       Tier = 'core'
       Kind = 'vsrepo'
       Packages = @('mv', 'bs')
       Dest = $Plugins
       Probe = 'libmvtools.dll'
       Why  = 'MVTools backend, plus the source filter used for benchmarking' }

    @{ Name = 'vsmlrt'
       Tier = 'full'
       Kind = 'github'
       Repo = 'AmusementClub/vs-mlrt'
       Match = 'vsmlrt-windows-x64-tensorrt.v*.7z.001'
       Split = $true
       Dest = $Plugins
       Probe = 'vsmlrt.py'
       Why  = 'RIFE inference via TensorRT - the fast, high quality backend' }

    @{ Name = 'models'
       Tier = 'full'
       Kind = 'github'
       Repo = 'AmusementClub/vs-mlrt'
       Match = 'models.v*.7z'
       Dest = $Plugins
       Probe = 'models'
       Why  = 'the RIFE network weights' }

    @{ Name = 'rife-ncnn'
       Tier = 'light'
       Kind = 'vsrepo'
       Packages = @('rife')
       Dest = $Plugins
       Probe = 'RIFE.dll'
       Why  = 'RIFE over Vulkan - same network, no 2.5 GB TensorRT download' }
)

# --- helpers ----------------------------------------------------------------

function Write-Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    $msg"   -ForegroundColor Green }
function Write-Skip($msg)  { Write-Host "    $msg"   -ForegroundColor DarkGray }
function Write-Warn2($msg) { Write-Host "    $msg"   -ForegroundColor Yellow }

function Get-SevenZip {
    $existing = Get-Command '7z' -ErrorAction SilentlyContinue
    if ($existing) { return $existing.Source }

    foreach ($p in @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe")) {
        if (Test-Path $p) { return $p }
    }

    Write-Step '7-Zip (needed to unpack .7z release archives)'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw '7-Zip is required but winget is unavailable. Install 7-Zip manually.'
    }
    winget install --id 7zip.7zip --silent --accept-package-agreements --accept-source-agreements | Out-Null

    foreach ($p in @("$env:ProgramFiles\7-Zip\7z.exe", "${env:ProgramFiles(x86)}\7-Zip\7z.exe")) {
        if (Test-Path $p) { Write-Ok "installed $p"; return $p }
    }
    throw '7-Zip install did not produce 7z.exe.'
}

function Get-Release($repo) {
    $uri = "https://api.github.com/repos/$repo/releases/latest"
    $headers = @{ 'User-Agent' = 'twixt-bootstrap'; 'Accept' = 'application/vnd.github+json' }
    if ($env:GITHUB_TOKEN) { $headers['Authorization'] = "Bearer $env:GITHUB_TOKEN" }
    try {
        return Invoke-RestMethod -Uri $uri -Headers $headers -TimeoutSec 45
    } catch {
        # Some projects only publish pre-releases; fall back to the full list.
        return (Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases" `
                    -Headers $headers -TimeoutSec 45 | Select-Object -First 1)
    }
}

function Resolve-Assets($repo, $match, $split) {
    $release = Get-Release $repo
    $assets = @($release.assets | Where-Object { $_.name -like $match } |
                Sort-Object { $_.name.Length })

    if (-not $assets) {
        $available = ($release.assets | ForEach-Object { $_.name }) -join ', '
        throw "no asset matching '$match' in $repo release $($release.tag_name). Available: $available"
    }

    $first = $assets[0]
    $parts = @($first)

    if ($split) {
        # Multi-part 7z: 7-Zip needs every .00N sibling present next to the
        # .001 before it can unpack anything.
        $stem = $first.name -replace '\.001$', ''
        $parts = @($release.assets | Where-Object { $_.name -like "$stem.*" } |
                   Sort-Object name)
    }

    return [pscustomobject]@{
        Parts = $parts
        Entry = $first.name
        Tag   = $release.tag_name
        Size  = ($parts | Measure-Object -Property size -Sum).Sum
    }
}

function Invoke-Native {
    <#
        Runs an external program and returns its exit code and output.

        PowerShell 5.1 wraps a native program's stderr in ErrorRecords, which
        under $ErrorActionPreference='Stop' turns ordinary diagnostic output
        into a terminating error. Relaxing the preference for the duration of
        the call keeps a non-zero exit code as data rather than an exception.
    #>
    param([string] $Exe, [string[]] $Arguments)

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1 | Out-String
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-LatestEmbeddedPythonUrl {
    <#
        VapourSynth R79+ ships as a cp312-abi3 wheel with no interpreter, so we
        supply one: the official Windows embeddable package, unpacked into the
        same folder its pip.bat/vspipe.bat expect (`python.exe` at the root).
        The version is discovered rather than pinned so this keeps working.
    #>
    $index = Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 `
                -Uri 'https://www.python.org/ftp/python/'
    $all = [regex]::Matches($index.Content, 'href="(3\.1[2-9]\.\d+)/"') |
           ForEach-Object { [version]$_.Groups[1].Value } |
           Sort-Object -Descending -Unique

    # The wheel is cp312-abi3, so anything from 3.12 up will load it. Prefer the
    # versions VapourSynth and the plugin ecosystem are actually built against,
    # and only reach for a newer interpreter if those are unavailable.
    $preferred = $all | Where-Object { $_.Minor -in 13, 12 }
    $versions = @($preferred) + @($all | Where-Object { $_ -notin $preferred })

    foreach ($v in $versions) {
        $url = "https://www.python.org/ftp/python/$v/python-$v-embed-amd64.zip"
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $url -Method Head -TimeoutSec 30 | Out-Null
            return @{ Url = $url; Version = $v }
        } catch { continue }
    }
    throw 'Could not find a Windows embeddable Python build on python.org.'
}

function Install-VapourSynth($item, $sevenZip) {
    New-Item -ItemType Directory -Force -Path $VsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $Cache | Out-Null

    # 1. embedded interpreter
    if (-not (Test-Path (Join-Path $VsDir 'python.exe'))) {
        $py = Get-LatestEmbeddedPythonUrl
        Write-Host "    python $($py.Version) embeddable" -ForegroundColor Gray
        $pyZip = Join-Path $Cache "python-$($py.Version)-embed-amd64.zip"
        if (-not (Test-Path $pyZip)) {
            Invoke-WebRequest -UseBasicParsing -Uri $py.Url -OutFile $pyZip -TimeoutSec 600
        }
        Expand-Archive -Path $pyZip -DestinationPath $VsDir -Force
    }

    # 2. the embeddable build disables site-packages by default; re-enable it
    #    so pip can install into this tree and imports resolve normally.
    Get-ChildItem -Path $VsDir -Filter 'python*._pth' -File | ForEach-Object {
        $lines = Get-Content $_.FullName
        $lines = $lines -replace '^\s*#\s*import\s+site\s*$', 'import site'
        if ($lines -notcontains 'Lib\site-packages') { $lines += 'Lib\site-packages' }
        # The embeddable build ignores PYTHONPATH entirely when a ._pth file is
        # present, so every import root has to be listed here explicitly.
        foreach ($extra in 'vapoursynth64\scripts', 'vapoursynth64\plugins') {
            if ($lines -notcontains $extra) { $lines += $extra }
        }
        Set-Content -Path $_.FullName -Value $lines -Encoding ascii
    }

    # 3. pip
    $pyExe = Join-Path $VsDir 'python.exe'
    if ((Invoke-Native $pyExe @('-c', 'import pip')).ExitCode -ne 0) {
        $getPip = Join-Path $Cache 'get-pip.py'
        if (-not (Test-Path $getPip)) {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' `
                -OutFile $getPip -TimeoutSec 300
        }
        $r = Invoke-Native $pyExe @($getPip, '--no-warn-script-location')
        if ($r.ExitCode -ne 0) { throw "get-pip failed:`n$($r.Output)" }
    }

    # 4. the VapourSynth portable payload (wheel + launchers)
    $asset = Resolve-Assets $item.Repo $item.Match $false
    $zip = Join-Path $Cache $asset.Entry
    if (-not (Test-Path $zip)) {
        Write-Host "      downloading $($asset.Entry)" -ForegroundColor DarkGray
        Invoke-WebRequest -UseBasicParsing -Uri $asset.Parts[0].browser_download_url `
            -OutFile $zip -TimeoutSec 900
    }
    Expand-Archive -Path $zip -DestinationPath $VsDir -Force

    $wheel = Get-ChildItem -Path (Join-Path $VsDir 'wheel') -Filter '*.whl' |
             Select-Object -First 1
    if (-not $wheel) { throw 'VapourSynth wheel not found in the portable archive.' }

    Write-Host "    installing $($wheel.Name)" -ForegroundColor Gray
    $r = Invoke-Native $pyExe @('-m', 'pip', 'install', '--upgrade',
                                '--no-warn-script-location', $wheel.FullName)
    if ($r.ExitCode -ne 0) { throw "pip failed to install $($wheel.Name):`n$($r.Output)" }

    New-Item -ItemType Directory -Force -Path $Plugins | Out-Null
    return $true
}

function Install-VsPackages($item, $sevenZip) {
    <#
        Installs plugins straight from VapourSynth's package database.

        The database entry for each package names the release archive, the exact
        path of each file inside it, and that file's SHA-256. Reading it directly
        gives verified binaries without depending on the vsrepo client, whose
        current release fails to start.
    #>
    $base = 'https://raw.githubusercontent.com/vapoursynth/vsrepo/master/local'
    New-Item -ItemType Directory -Force -Path $Plugins, $Cache | Out-Null
    $ok = $true

    foreach ($pkg in $item.Packages) {
        $meta = Invoke-RestMethod -UseBasicParsing -Uri "$base/$pkg.json" -TimeoutSec 60
        $release = $meta.releases | Where-Object { $_.win64 } | Select-Object -First 1
        if (-not $release) {
            Write-Warn2 "$pkg`: no win64 build listed"; $ok = $false; continue
        }

        $wanted = $release.win64.files.PSObject.Properties | ForEach-Object { $_.Name }
        $missing = $wanted | Where-Object { -not (Test-Path (Join-Path $Plugins $_)) }
        if (-not $missing -and -not $Force) {
            Write-Skip "$($meta.name) already present"
            continue
        }

        $url = $release.win64.url
        $archive = Join-Path $Cache (Split-Path $url -Leaf)
        Write-Host "    $($meta.name) $($release.version)" -ForegroundColor Gray

        if (-not (Test-Path $archive) -or $Force) {
            Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $archive -TimeoutSec 900
        }

        $staging = Join-Path $Cache "stage-$pkg"
        Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
        Expand-Any $archive $staging $sevenZip

        # 'files' maps the installed name to [path inside archive, sha256].
        foreach ($target in $release.win64.files.PSObject.Properties) {
            $inner, $expected = $target.Value
            $src = Join-Path $staging ($inner -replace '/', '\')
            if (-not (Test-Path $src)) {
                Write-Warn2 "$pkg`: '$inner' missing from the archive"; $ok = $false; continue
            }

            $actual = (Get-FileHash $src -Algorithm SHA256).Hash
            if ($expected -and $actual -ne $expected.ToUpper()) {
                Write-Warn2 "$pkg`: checksum mismatch on $($target.Name) - not installing"
                $ok = $false
                continue
            }

            Copy-Item $src (Join-Path $Plugins $target.Name) -Force
            Write-Host "      $($target.Name) verified" -ForegroundColor DarkGray
        }

        Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    }

    return $ok
}

function Expand-Any($archive, $target, $sevenZip) {
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    if ($archive -like '*.zip') {
        Expand-Archive -Path $archive -DestinationPath $target -Force
    } else {
        $r = Invoke-Native $sevenZip @('x', $archive, "-o$target", '-y', '-bso0', '-bsp0')
        if ($r.ExitCode -ne 0) { throw "7z failed to extract ${archive}:`n$($r.Output)" }
    }
}

function Test-Installed($item) {
    $probe = Join-Path $item.Dest $item.Probe
    if (Test-Path $probe) { return $true }
    # Plugins can also land in a nested folder depending on the archive layout.
    return [bool](Get-ChildItem -Path $item.Dest -Filter $item.Probe -Recurse `
                    -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Install-Component($item, $sevenZip) {
    # Multi-package components decide per package, since one of several plugins
    # being present says nothing about the others.
    if ($item.Kind -ne 'vsrepo' -and (Test-Installed $item) -and -not $Force) {
        Write-Skip "already present ($($item.Probe))"
        return $true
    }

    if ($item.Kind -eq 'vapoursynth') {
        if ($PSCmdlet.ShouldProcess('VapourSynth portable + embedded Python', 'install') -eq $false) {
            return $true
        }
        Install-VapourSynth $item $sevenZip | Out-Null
        if (Test-Installed $item) { Write-Ok "installed -> $($item.Dest)"; return $true }
        Write-Warn2 "installed, but '$($item.Probe)' was not found"
        return $false
    }

    if ($item.Kind -eq 'vsrepo') {
        if ($PSCmdlet.ShouldProcess("plugins: $($item.Packages -join ', ')", 'install') -eq $false) {
            return $true
        }
        Install-VsPackages $item $sevenZip | Out-Null
        if (Test-Installed $item) { Write-Ok "installed -> $($item.Dest)"; return $true }
        Write-Warn2 "vsrepo ran but '$($item.Probe)' was not found"
        return $false
    }

    $asset = Resolve-Assets $item.Repo $item.Match $item.Split
    $mb = [math]::Round($asset.Size / 1MB, 1)
    $label = if ($asset.Parts.Count -gt 1) { "$($asset.Entry) (+$($asset.Parts.Count - 1) parts)" }
             else { $asset.Entry }
    Write-Host "    $label  ${mb} MB  [$($asset.Tag)]" -ForegroundColor Gray

    if ($PSCmdlet.ShouldProcess($asset.Entry, 'download and extract') -eq $false) { return $true }

    New-Item -ItemType Directory -Force -Path $Cache | Out-Null
    foreach ($part in $asset.Parts) {
        $file = Join-Path $Cache $part.name
        if ((Test-Path $file) -and ((Get-Item $file).Length -eq $part.size) -and -not $Force) {
            Write-Skip "cached $($part.name)"
            continue
        }
        Write-Host "      downloading $($part.name)" -ForegroundColor DarkGray
        Invoke-WebRequest -UseBasicParsing -Uri $part.browser_download_url -OutFile $file -TimeoutSec 1800
    }

    # For a split archive 7-Zip is pointed at the .001 and finds the rest itself.
    Expand-Any (Join-Path $Cache $asset.Entry) $item.Dest $sevenZip

    if (Test-Installed $item) {
        Write-Ok "installed -> $($item.Dest)"
        return $true
    }
    Write-Warn2 "extracted, but '$($item.Probe)' was not found - check $($item.Dest)"
    return $false
}

# --- run --------------------------------------------------------------------

Write-Host ''
Write-Host '  twixt runtime bootstrap' -ForegroundColor White
Write-Host "  target: $Runtime" -ForegroundColor DarkGray
Write-Host "  tier:   $Tier" -ForegroundColor DarkGray

$selected = if ($Component) {
    # `powershell -File script.ps1 -Component a,b` hands over one literal
    # "a,b" rather than an array, so normalise before matching.
    $wanted = $Component -split ',' | ForEach-Object { $_.Trim() } |
              Where-Object { $_ }
    $Manifest | Where-Object { $wanted -contains $_.Name }
} else {
    # 'core' is always needed; the tier decides which inference backend joins it.
    $Manifest | Where-Object { $_.Tier -eq 'core' -or $_.Tier -eq $Tier }
}

if (-not $selected) {
    throw "No components matched. Known: $(($Manifest | ForEach-Object { $_.Name }) -join ', ')"
}

# Package archives are .7z regardless of which components were selected, and
# resolving an already-installed 7-Zip is just a path lookup.
$sevenZip = Get-SevenZip

$failed = @()
foreach ($item in $selected) {
    Write-Step "$($item.Name) - $($item.Why)"
    try {
        if (-not (Install-Component $item $sevenZip)) { $failed += $item.Name }
    } catch {
        Write-Warn2 "failed: $($_.Exception.Message)"
        $failed += $item.Name
    }
}

# VapourSynth portable needs this marker to use its bundled interpreter
# rather than any Python installed on the machine.
$portableMarker = Join-Path $VsDir 'portable.vs'
if ((Test-Path $VsDir) -and -not (Test-Path $portableMarker)) {
    New-Item -ItemType File -Path $portableMarker -Force | Out-Null
}

Write-Host ''
if ($failed) {
    Write-Host "  finished with problems: $($failed -join ', ')" -ForegroundColor Yellow
    Write-Host '  re-run for just those:  bootstrap.ps1 -Component ' -NoNewline -ForegroundColor DarkGray
    Write-Host ($failed -join ',') -ForegroundColor DarkGray
} else {
    Write-Host '  runtime ready.' -ForegroundColor Green
}
Write-Host ''
Write-Host '  verify with:  python -m twixt doctor' -ForegroundColor White
Write-Host ''
