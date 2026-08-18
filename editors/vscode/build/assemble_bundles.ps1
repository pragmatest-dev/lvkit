<#
  Windows (win32-x64) counterpart of assemble_bundles.sh. Assembles the Claude
  Code plugin archive and the Claude Desktop .mcpb FROM THE ALREADY-BUILT onedir
  binary (editors\vscode\bin\lvkit) — reusing the one build.

    param TARGET  (win32-x64)
    param VER     (bundled lvkit version, e.g. 0.5.8)

  Uses [ZipFile]::CreateFromDirectory (not Compress-Archive) so dot-prefixed
  entries (.claude-plugin, .mcp.json) land in the zip — the same reason the
  MCPB/labview-mcp packers do.
#>
param(
  [Parameter(Mandatory = $true)][string]$Target,
  [Parameter(Mandatory = $true)][string]$Ver
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
Set-Location $repo
Add-Type -AssemblyName System.IO.Compression.FileSystem

$bin = Join-Path $repo 'editors\vscode\bin\lvkit'   # onedir; exe at $bin\lvkit.exe
$skills = @('lvkit', 'lvkit-describe', 'lvkit-query', 'lvkit-convert', 'lvkit-document', 'lvkit-review', 'lvkit-resolve')

# Customer-facing plugin name (must equal the marketplace entry name). $Target is
# an internal arch triple; the published plugin uses a friendlier name.
$name = switch ($Target) {
  'darwin-arm64' { 'lvkit-mac-arm64' }
  'darwin-x64'   { 'lvkit-mac-intel' }
  'linux-x64'    { 'lvkit-linux' }
  'win32-x64'    { 'lvkit-windows' }
  default        { "lvkit-$Target" }
}

# ---- Claude Code plugin archive ----
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("plugin-" + [System.Guid]::NewGuid())
New-Item -ItemType Directory -Force -Path (Join-Path $stage 'skills'), (Join-Path $stage 'bin') | Out-Null
Copy-Item -Recurse -Force 'plugin\.claude-plugin' $stage
Copy-Item -Force 'plugin\.mcp.json' $stage
Copy-Item -Force 'plugin\README.md' $stage
foreach ($s in $skills) { Copy-Item -Recurse -Force "src\lvkit\skill_templates\$s" (Join-Path $stage "skills\$s") }
Copy-Item -Recurse -Force $bin (Join-Path $stage 'bin\lvkit')
# Stamp plugin.json (name/version) and rewrite .mcp.json command to the .exe.
node -e "const f=process.argv[1],j=require(f);j.name='$name';j.version='$Ver';require('fs').writeFileSync(f,JSON.stringify(j,null,2)+'\n')" (Join-Path $stage '.claude-plugin\plugin.json')
node -e "const f=process.argv[1],j=require(f);j.mcpServers.lvkit.command='`${CLAUDE_PLUGIN_ROOT}/bin/lvkit/lvkit.exe';require('fs').writeFileSync(f,JSON.stringify(j,null,2)+'\n')" (Join-Path $stage '.mcp.json')
$pluginZip = Join-Path $repo "lvkit-plugin-$Target.zip"
if (Test-Path $pluginZip) { Remove-Item $pluginZip -Force }
[System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $pluginZip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
Write-Host "wrote lvkit-plugin-$Target.zip"

# ---- Claude Desktop .mcpb (win32) ----
$mb = Join-Path ([System.IO.Path]::GetTempPath()) ("mcpb-" + [System.Guid]::NewGuid())
New-Item -ItemType Directory -Force -Path (Join-Path $mb 'server') | Out-Null
Copy-Item -Recurse -Force "$bin\*" (Join-Path $mb 'server')
node -e "const j=require(process.argv[1]);j.version='$Ver';j.compatibility.platforms=['win32'];require('fs').writeFileSync(process.argv[2],JSON.stringify(j,null,2)+'\n')" (Join-Path $repo 'mcpb\manifest.json') (Join-Path $mb 'manifest.json')
npx --yes '@anthropic-ai/mcpb' pack $mb (Join-Path $repo "lvkit-$Target.mcpb")
Write-Host "wrote lvkit-$Target.mcpb"
