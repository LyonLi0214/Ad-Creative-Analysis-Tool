@echo off
cd /d "%~dp0"
call venv\Scripts\activate
python agent_tagger.py --limit 20
pause
