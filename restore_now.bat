@echo off
cd /d %~dp0
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
set /p DBFILE=Chemin du fichier DB (.db ou .sql): 
set /p UPZIP=Chemin du zip uploads: 
python tools\restore_instance.py --db "%DBFILE%" --uploads "%UPZIP%"
pause
