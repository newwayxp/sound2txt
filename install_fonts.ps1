# install_fonts.ps1 - Install custom fonts via PowerShell
#
# Usage (run as Administrator):
#   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force
#   .\install_fonts.ps1
#
# Or use the batch file: install_fonts.bat

param(
    [switch]$Force = $false,
    [switch]$Quiet = $false
)

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")

if (-not $isAdmin) {
    Write-Host "❌ This script must be run as Administrator"
    Write-Host ""
    Write-Host "💡 Right-click PowerShell and select 'Run as administrator', then:"
    Write-Host "   Set-ExecutionPolicy -ExecutionPolicy Bypass -Scope Process -Force"
    Write-Host "   .\install_fonts.ps1"
    exit 1
}

# Paths
$fontDir = Join-Path (Split-Path $script:MyInvocation.MyCommandPath) "fonts"
$windowsFontDir = "C:\Windows\Fonts"

# Fonts to install
$fonts = @(
    "ShareTechMono-Regular.ttf",
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMono-Bold.ttf",
    "JetBrainsMono-BoldItalic.ttf"
)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Sound2Text Font Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $fontDir)) {
    Write-Host "❌ Font directory not found: $fontDir" -ForegroundColor Red
    exit 1
}

Write-Host "Font directory: $fontDir"
Write-Host "Target directory: $windowsFontDir"
Write-Host ""
Write-Host "Installing fonts..."
Write-Host ""

$successCount = 0
$failedCount = 0

foreach ($fontFile in $fonts) {
    $src = Join-Path $fontDir $fontFile
    $dst = Join-Path $windowsFontDir $fontFile

    if (-not (Test-Path $src)) {
        Write-Host "[SKIP] $fontFile - source not found"
        continue
    }

    try {
        Copy-Item -Path $src -Destination $dst -Force -ErrorAction Stop
        Write-Host "[✓] $fontFile" -ForegroundColor Green
        $successCount++
    } catch {
        Write-Host "[✗] $fontFile - $_" -ForegroundColor Red
        $failedCount++
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Installation Summary" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Success: $successCount fonts installed"
Write-Host "Failed: $failedCount fonts"
Write-Host ""

if ($successCount -gt 0) {
    Write-Host "✓ Font installation completed successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "💡 Tip: Restart the application for changes to take effect."
    exit 0
} else {
    Write-Host "✗ No fonts were installed." -ForegroundColor Red
    exit 1
}
