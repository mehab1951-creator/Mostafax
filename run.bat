@echo off
title QUREO Red Edition Platform
pip install -r requirements.txt
cls
echo ========================================================
echo QUREO Red Edition Solver is running!
echo Open your browser at: http://localhost:1111
echo ========================================================
python app.py
pause
