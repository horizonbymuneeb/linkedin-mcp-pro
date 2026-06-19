# LinkedIn MCP Pro - one-line installer (Windows PowerShell)
# Usage: iwr https://raw.githubusercontent.com/horizonbymuneeb/linkedin-mcp-pro/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }

# --- Detect OS ---------------------------------------------------------------
$os = "$([System.Environment]::OSVersion.Platform)"
Say "Detected platform: $os"

# --- Check Python ------------------------------------------------------------
$py = $null
foreach ($c in @('python','python3','py')) {
  $found = (Get-Command $c -ErrorAction SilentlyContinue)
  if ($found) { $py = $found.Source; break }
}
if (-not $py) { Die "Python not found. Install Python 3.11+ from https://python.org and re-run." }

$pyVerOutput = & $py -c "import sys;print('%d.%d'%sys.version_info[:2])"
$parts = $pyVerOutput.Split('.')
$major = [int]$parts[0]
$minor = [int]$parts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
  Die "Python $pyVerOutput found, but 3.11+ is required."
}
Say "Python $pyVerOutput OK"

# --- Install package ---------------------------------------------------------
Say "Installing linkedin-mcp-pro (user)"
& $py -m pip install --user --upgrade linkedin-mcp-pro
if ($LASTEXITCODE -ne 0) { Die "pip install failed." }

# --- Create profile dir ------------------------------------------------------
$profileDir = Join-Path $env:USERPROFILE ".linkedin-mcp\profile"
New-Item -ItemType Directory -Force -Path $profileDir | Out-Null
Say "Profile directory ready: $profileDir"

# --- Print next steps --------------------------------------------------------
@"

Installation complete.

Next steps:
  1. Configure an MCP agent (in an elevated or user PowerShell):
       linkedin-mcp-install add claude-desktop-win
  2. (Optional) provide a LinkedIn session cookie:
       $env:LI_AT = "<your li_at cookie>"
  3. Verify:
       linkedin-mcp-install doctor

"@ | Write-Host

# --- Show snippet for Windows Claude Desktop --------------------------------
Say "Config snippet for Claude Desktop (Windows):"
$snippet = & $py -m linkedin_mcp.install print-configs 2>$null |
            Select-String -Pattern "claude-desktop-win" -Context 0,12
if ($snippet) { $snippet } else { Warn "(run 'linkedin-mcp-install print-configs' to view snippets)" }
