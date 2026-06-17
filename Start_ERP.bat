@echo off
:: Navigate to your project folder (Optional if the bat is already in the project folder)
cd /d "%~dp0"

:: Activate the virtual environment (change 'venv' to your folder name)
call venv\Scripts\activate

:: Run the Streamlit app
streamlit run main.py

:: Keep the window open if the app crashes
pause