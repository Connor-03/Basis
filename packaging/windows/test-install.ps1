# Checks the installed program on a real Windows desktop (run by the build after a silent install):
# the shortcut exists, the program starts the way the shortcut starts it, its window opens, and closing the window
# stops everything. Leaves a picture of the desktop in dist\shots.
$ErrorActionPreference = 'Stop'
$app = "$env:LOCALAPPDATA\Programs\Basis"
$port = 8470
$fails = 0
function Check($ok, $what) { if ($ok) { "ok   $what" } else { "FAIL $what"; $script:fails++ } }
function Status { try { (Invoke-WebRequest "http://127.0.0.1:$port/api/status" -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json } catch { $null } }

Check (Test-Path "$app\python\pythonw.exe") 'the bundled Python is installed'
Check (Test-Path "$app\app\basis.pyw") 'the program files are installed'
Check (Test-Path "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Basis.lnk") 'the Start menu shortcut exists'
Check (Test-Path "$app\app\MANIFEST.json") 'the manifest for updates is installed'

$p = Start-Process "$app\python\pythonw.exe" -ArgumentList '-B', '-X', 'utf8', "`"$app\app\basis.pyw`"", '--port', "$port" -WorkingDirectory "$app\app" -PassThru
$st = $null
for ($i = 0; $i -lt 90 -and -not $st; $i++) { Start-Sleep 1; $st = Status }
Check ($st -and $st.app -eq 'basis') "the program starts (version $($st.version))"
Start-Sleep 15

New-Item -ItemType Directory -Force dist\shots | Out-Null
try {
  Add-Type -AssemblyName System.Windows.Forms, System.Drawing
  $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  [System.Drawing.Graphics]::FromImage($bmp).CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
  $bmp.Save("$pwd\dist\shots\desktop.png")
  "screenshot $($b.Width)x$($b.Height)"
} catch { "no screenshot: $_" }

$win = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*--app=http://127.0.0.1:$port/*" })
Check ($win.Count -ge 1) "the app window is open ($($win.Count) browser processes: $(($win | Select-Object -First 1).Name))"
Check (Test-Path "$env:LOCALAPPDATA\Basis\data\finance.db") 'the data folder is in the user profile, outside the program'
Check (-not (Test-Path "$app\app\data")) 'no data is written into the program folder'

# close the window the hard way, then say what a closing page says
$win | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
try { Invoke-WebRequest "http://127.0.0.1:$port/api/bye" -UseBasicParsing -TimeoutSec 3 | Out-Null } catch {}
$gone = $false
for ($i = 0; $i -lt 60 -and -not $gone; $i++) { Start-Sleep 1; $gone = $p.HasExited }
Check $gone 'closing the window stops the program'
Check (-not (Status)) 'nothing is left listening'
Get-Content "$env:LOCALAPPDATA\Basis\logs\basis.log" -ErrorAction SilentlyContinue | Select-Object -Last 12

& "$app\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART | Out-Null
Start-Sleep 6
Check (-not (Test-Path "$app\app\basis.pyw")) 'uninstall removes the program'
Check (Test-Path "$env:LOCALAPPDATA\Basis\data") 'uninstall leaves your data'
if ($fails) { throw "$fails checks failed" } else { 'all passed' }
