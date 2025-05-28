# Enhanced PowerShell deployment script for Windows development
# Requires PowerShell 5.1 or later

# Stop on any error
$ErrorActionPreference = "Stop"

# Log file for deployment
$logFile = "deploy.log"

# Function to log messages with timestamps
function Log {
    param([string]$message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logMessage = "[$timestamp] $message"
    Write-Host $logMessage
    Add-Content -Path $logFile -Value $logMessage
}

Log "🚀 Starting ELBOT deployment..."

# Function to validate environment
function Validate-Environment {
    Log "🔍 Validating environment..."
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git is not installed or not available in PATH. Please install Git and try again."
    }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python is not installed or not available in PATH. Please install Python and try again."
    }
    Log "✅ Environment validation passed."
}

# Function to backup files
function Backup-Project {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "backup_$timestamp"
    
    Log "🗄️ Backing up current code to $backupDir..."
    
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
    Log "🔄 Restoring from backup..."
    Get-ChildItem $backupDir | Copy-Item -Destination . -Recurse -Force
}

# Function to clean up temporary files
function Cleanup {
    param($backupDir)
    Log "🧹 Cleaning up temporary files..."
    if (Test-Path $backupDir) {
        Remove-Item -Recurse -Force $backupDir
        Log "🧹 Removed backup directory: $backupDir"
    }
}

# Function to rotate logs
function Set-LogRotation {
    param([string]$logDir, [int]$maxLogs = 5)
    Log "📝 Rotating logs in $logDir..."
    if (Test-Path $logDir) {
        $logs = Get-ChildItem -Path $logDir -Filter "*.log" | Sort-Object LastWriteTime -Descending
        if ($logs.Count -gt $maxLogs) {
            $logs | Select-Object -Skip $maxLogs | Remove-Item -Force
            Log "📝 Removed old logs, keeping the latest $maxLogs logs."
        }
    } else {
        Log "ℹ️ Log directory does not exist, skipping rotation."
    }
}

# Function to perform health checks
function Test-ServiceHealth {
    param([string]$serviceName)
    Log "🔍 Performing health check for $serviceName..."
    Start-Sleep -Seconds 5  # Give the service time to start
    $serviceStatus = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
    if ($serviceStatus -and $serviceStatus.Status -eq 'Running') {
        Log "✅ $serviceName is running."
    } else {
        throw "❌ $serviceName failed to start!"
    }
}

# Function to manage services
function Control-Services {
    param([string[]]$services, [string]$action)
    foreach ($service in $services) {
        Log "🛠️ $action service: $service..."
        if ($action -eq 'stop') {
            Stop-Service -Name $service -Force -ErrorAction SilentlyContinue
        } elseif ($action -eq 'start') {
            Start-Service -Name $service -ErrorAction SilentlyContinue
        }
    }
}

# Optional parameters
param(
    [string]$Branch = "main",  # Default branch to deploy
    [switch]$SkipTests          # Option to skip running tests
)

try {
    # Validate environment
    Validate-Environment

    # Rotate logs
    $logDir = "C:\Logs\ELBOT"
    Set-LogRotation -logDir $logDir -maxLogs 5

    # Create backup
    $backupDir = Backup-Project

    # Pull latest changes
    Log "⬇️ Pulling latest code from branch '$Branch'..."
    git pull origin $Branch

    # Create/activate virtual environment
    if (-not (Test-Path "venv")) {
        Log "🔧 Creating virtual environment..."
        python -m venv venv
    }
    
    Log "📦 Activating virtual environment..."
    .\venv\Scripts\Activate.ps1

    # Install dependencies
    Log "📦 Installing dependencies..."
    python -m pip install --upgrade pip
    pip install -r requirements.txt

    # Run tests (if not skipped)
    if (-not $SkipTests) {
        Log "🧪 Running tests..."
        python -m pytest --maxfail=1 --disable-warnings
        if ($LASTEXITCODE -ne 0) {
            throw "Tests failed! Deployment aborted."
        }
    } else {
        Log "⚠️ Skipping tests as per user request."
    }

    # Stop services
    $services = @("elbot", "elbot-web")
    Control-Services -services $services -action 'stop'

    # Start services
    Control-Services -services $services -action 'start'

    # Perform health checks
    foreach ($service in $services) {
        Test-ServiceHealth -serviceName $service
    }

    Log "✅ Deployment completed successfully!"
    Log "ℹ️ Web interface available at http://localhost:8080"

} catch {
    Log "❌ Error during deployment: $_"
    Restore-FromBackup $backupDir
    Log "🔄 Rolled back to previous state"
    exit 1
} finally {
    Cleanup $backupDir
}
