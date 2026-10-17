@echo off
:: Run from the project folder, whichever folder this file is launched from
cd /d "%~dp0"

:: Use the project's own Python environment (.venv) directly, so the app never
:: runs on a different Python that lacks the right packages
".venv\Scripts\python.exe" -m streamlit run main.py

:: Keep the window open if the app crashes
pause
