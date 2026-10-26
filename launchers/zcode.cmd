@echo off
rem zcode CLI launcher - auto-discovers the ZCode Desktop bundled CLI.
rem The ZCode CLI has no standalone distribution; it ships inside the desktop app
rem at resources/glm/zcode.cjs. Precedence: ZCODE_CLI_ENTRY > known install locations.
setlocal
if defined ZCODE_CLI_ENTRY (
  if exist "%ZCODE_CLI_ENTRY%" (
    node "%ZCODE_CLI_ENTRY%" %*
    exit /b %ERRORLEVEL%
  )
)
if exist "%LOCALAPPDATA%\Programs\ZCode\resources\glm\zcode.cjs" (
  node "%LOCALAPPDATA%\Programs\ZCode\resources\glm\zcode.cjs" %*
  exit /b %ERRORLEVEL%
)
if exist "C:\Program Files\ZCode\resources\glm\zcode.cjs" (
  node "C:\Program Files\ZCode\resources\glm\zcode.cjs" %*
  exit /b %ERRORLEVEL%
)
if exist "E:\ZCode\resources\glm\zcode.cjs" (
  node "E:\ZCode\resources\glm\zcode.cjs" %*
  exit /b %ERRORLEVEL%
)
echo zcode: CLI entry not found. Set ZCODE_CLI_ENTRY to ZCodeDesktop\resources\glm\zcode.cjs 1>&2
exit /b 1
