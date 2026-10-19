$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $repoRoot "dotnet\AddCheckSum\AddCheckSum.csproj"
$outputDir = Join-Path $repoRoot "out"
$expectedExe = Join-Path $outputDir "AddCheckSum.exe"

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

& dotnet msbuild $project /t:Build /p:Configuration=Release /p:Platform=AnyCPU "/p:OutputPath=$outputDir\"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to build the x86 AddCheckSum helper. Install the .NET Framework 4.8 developer pack if reference assemblies are missing."
}

if (-not (Test-Path -LiteralPath $expectedExe)) {
    throw "Build completed without producing $expectedExe"
}

Write-Host "Built $expectedExe"
