# Docker Installation Script for Windows
# Installs Docker Desktop using winget

$ErrorActionPreference = "Stop"

Write-Host "Detecting Docker installation..."

# Check if Docker is already available
try {
    $dockerVersion = docker --version 2>$null
    if ($dockerVersion) {
        Write-Host "Docker is already installed and available!"
        Write-Host $dockerVersion
        Write-Host "No installation needed."
        exit 0
    }
} catch {
    # Docker not found, continue with installation
}

Write-Host "Docker not found. Attempting installation..."

# Check if winget is available
if (Get-Command winget -ErrorAction SilentlyContinue) {
    Write-Host "Installing Docker Desktop via winget..."
    winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements
    
    Write-Host ""
    Write-Host "Docker Desktop installed!"
    Write-Host "Please restart your computer, then start Docker Desktop from the Start menu."
    Write-Host ""
    Write-Host "After Docker Desktop is running, test with: docker --version"
} else {
    Write-Host "winget is not available on this system."
    Write-Host ""
    Write-Host "Please install Docker Desktop manually:"
    Write-Host "  1. Download from: https://www.docker.com/products/docker-desktop"
    Write-Host "  2. Run the installer"
    Write-Host "  3. Restart your computer"
    Write-Host "  4. Start Docker Desktop from the Start menu"
    exit 1
}
