# PowerShell deployment script for Windows development
# Requires PowerShell 5.1 or later

# Stop on any error
$ErrorActionPreference = "Stop"

Write-Host "🚀 Starting ELBOT deployment..."

# Function to backup files
function Backup-Project {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "backup_$timestamp"
    
    Write-Host "🗄️ Backing up current code to $backupDir..."
    
    # Create backup directory
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    
    # Copy all files except backups and venv
    Get-ChildItem -Exclude "backup_*", "venv" | Copy-Item -Destination $backupDir -Recurse -Force
    
    # Copy .env if it exists
    if (Test-Path ".env") {
        Copy-Item ".env" -Destination $backupDir
    }
    
    return $backupDir
}

# Function to restore from backup
function Restore-FromBackup {
    param($backupDir)
    Write-Host "🔄 Restoring from backup..."
    Get-ChildItem $backupDir | Copy-Item -Destination . -Recurse -Force
}

try {
    # Create backup
    $backupDir = Backup-Project

    # Pull latest changes
    Write-Host "⬇️ Pulling latest code..."
    git pull origin main

    # Create/activate virtual environment
    if (-not (Test-Path "venv")) {
        Write-Host "🔧 Creating virtual environment..."
        python -m venv venv
    }
    
    Write-Host "📦 Activating virtual environment..."
    .\venv\Scripts\Activate.ps1

    # Install dependencies
    Write-Host "📦 Installing dependencies..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt

    # Run tests
    Write-Host "🧪 Running tests..."
    python -m pytest --maxfail=1 --disable-warnings
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed!"
    }

    # Development server startup
    Write-Host "🚀 Starting development server..."
    Start-Process python -ArgumentList "web/app.py" -WindowStyle Hidden
    Start-Process python -ArgumentList "main.py" -WindowStyle Hidden

    Write-Host "✅ Deployment completed successfully!"
    Write-Host "ℹ️ Use 'Get-Process python' to see running processes"
    Write-Host "ℹ️ Web interface available at http://localhost:8080"

} catch {
    Write-Host "❌ Error during deployment: $_"
    Restore-FromBackup $backupDir
    Write-Host "🔄 Rolled back to previous state"
    exit 1
}
