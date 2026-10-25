@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  Сборка программы "Практика Python" в один файл .exe
echo  Всё нужное для сборки ставится во временное виртуальное окружение и после сборки удаляется.
echo  Интернет нужен один раз, чтобы скачать сборщик.
echo.

set PY=
where py >nul 2>nul && set PY=py
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  echo  Python не найден. Установи его с сайта python.org, при установке поставь галочку Add python.exe to PATH,
  echo  потом запусти этот файл ещё раз.
  pause
  exit /b 1
)

set VENV=%TEMP%\pyi_venv
if exist "%VENV%" rmdir /s /q "%VENV%"
echo  Создаю временное виртуальное окружение...
%PY% -m venv "%VENV%"
if errorlevel 1 goto fail

echo  Устанавливаю сборщик PyInstaller...
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip pyinstaller
if errorlevel 1 goto fail

echo  Собираю программу, это займёт пару минут...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --noconsole --name "Практика Python" ^
  --distpath "%~dp0..\Готовая программа" --workpath "%TEMP%\pyi_work" --specpath "%TEMP%\pyi_spec" ^
  --add-data "%~dp0интерфейс.html;." --add-data "%~dp0..\Задания;Задания" ^
  --hidden-import random --hidden-import math --hidden-import datetime --hidden-import string ^
  --hidden-import collections --hidden-import itertools --hidden-import functools --hidden-import statistics ^
  --hidden-import json --hidden-import re --hidden-import time --hidden-import decimal --hidden-import fractions ^
  --hidden-import textwrap --hidden-import calendar ^
  "%~dp0программа.py"
if errorlevel 1 goto fail

echo  Удаляю временные файлы...
rmdir /s /q "%VENV%" 2>nul
rmdir /s /q "%TEMP%\pyi_work" 2>nul
rmdir /s /q "%TEMP%\pyi_spec" 2>nul

echo.
echo  Готово. Программа лежит здесь: "%~dp0..\Готовая программа\Практика Python.exe"
echo  Её можно копировать и отправлять другим людям: больше ничего не нужно.
pause
exit /b 0

:fail
echo.
echo  Сборка не удалась. Сделай снимок этого окна и попроси помощи.
rmdir /s /q "%VENV%" 2>nul
pause
exit /b 1
