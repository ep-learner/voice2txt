@echo off
chcp 65001 >nul
setlocal
set "LAUNCHER=%~dp0launcher.py"
where pyw >nul 2>nul
if not errorlevel 1 (
  pyw -3 "%LAUNCHER%"
  if not errorlevel 1 exit /b 0
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  pythonw "%LAUNCHER%"
  if not errorlevel 1 exit /b 0
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%LAUNCHER%"
  if not errorlevel 1 exit /b 0
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%LAUNCHER%"
  if not errorlevel 1 exit /b 0
)
echo 无法启动：未找到 Python。请安装 Python 3.10 或更高版本后重新双击。
pause
exit /b 1
