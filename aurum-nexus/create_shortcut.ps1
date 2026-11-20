# Alternative to the installer: create Desktop + Start Menu shortcuts for Aurum Nexus.
# Usage (PowerShell, in the project folder):  powershell -ExecutionPolicy Bypass -File .\create_shortcut.ps1
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root "build\out\dist\Aurum Nexus\Aurum Nexus.exe"
if (Test-Path $exe) { $target = $exe; $args = ""; $work = Split-Path $exe }
else { $target = Join-Path $root "run_app.bat"; $args = ""; $work = $root }
$icon = Join-Path $root "assets\icon.ico"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
  $lnk = $shell.CreateShortcut((Join-Path $dir "Aurum Nexus.lnk"))
  $lnk.TargetPath = $target; $lnk.Arguments = $args; $lnk.WorkingDirectory = $work
  $lnk.IconLocation = $icon; $lnk.Description = "Aurum Nexus - multi-agent MT5 trading system"
  $lnk.Save()
  Write-Host "created" (Join-Path $dir "Aurum Nexus.lnk")
}
