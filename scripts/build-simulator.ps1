param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"

$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$runtimeRoot = Join-Path $repoRoot "HSL Debugger\hsl_runtime"
$entryPoint = Join-Path $runtimeRoot "main.py"
$targetRoot = [IO.Path]::GetFullPath(
    (Join-Path $repoRoot "HSL Debugger\dist\hsl_debugger")
)
$buildRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "hsl-debugger-build-" + [guid]::NewGuid().ToString("N")
)
$builtRoot = Join-Path $buildRoot "dist\hsl_debugger"

if (-not $targetRoot.StartsWith($repoRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to replace a simulator directory outside the repository."
}

New-Item -ItemType Directory -Path $buildRoot | Out-Null
try {
    & $Python -m PyInstaller `
        --onedir `
        --name hsl_debugger `
        --console `
        --noconfirm `
        --distpath (Join-Path $buildRoot "dist") `
        --workpath (Join-Path $buildRoot "work") `
        --specpath $buildRoot `
        $entryPoint
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $builtExe = Join-Path $builtRoot "hsl_debugger.exe"
    if (-not (Test-Path -LiteralPath $builtExe)) {
        throw "PyInstaller did not produce $builtExe."
    }

    if (Test-Path -LiteralPath $targetRoot) {
        Remove-Item -LiteralPath $targetRoot -Recurse -Force
    }
    Copy-Item -LiteralPath $builtRoot -Destination $targetRoot -Recurse -Force

    $hashes = [ordered]@{}
    Get-ChildItem -LiteralPath $runtimeRoot -Filter "*.py" |
        Sort-Object Name |
        ForEach-Object {
            $hashes[$_.Name] = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
        }
    $manifest = [ordered]@{
        format = 1
        files = $hashes
    }
    $manifestPath = Join-Path $targetRoot "source-manifest.json"
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding utf8

    Write-Host "Simulator rebuilt: $builtExe"
    Write-Host "Source manifest: $manifestPath"
}
finally {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
}
