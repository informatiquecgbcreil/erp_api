@echo off
setlocal
cd /d %~dp0

if not exist .venv (
  echo [INFO] Creation de l'environnement Python...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

if not exist .env (
  echo [INFO] Fichier .env absent: copie depuis .env.example
  copy .env.example .env >nul
)

echo [INFO] Installation/verification des dependances...
python -m pip install -r requirements.txt


echo [INFO] Demarrage de l'application...
start "" http://127.0.0.1:8000/setup/
python run_waitress.py
