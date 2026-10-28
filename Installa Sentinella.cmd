@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 (
    echo Installa Python 3.12 con Tkinter, poi riprova. Consulta README.md.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo Installazione dipendenze non riuscita. Nessuna configurazione e stata azzerata.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "from pathlib import Path; from config_store import ensure_local_files; ensure_local_files(Path.cwd())"
if errorlevel 1 exit /b 1
echo Installazione completata. Avvia Sentinella.cmd apre il programma.
pause
