@echo off
rem 以分离进程启动全量评测（不受本窗口关闭影响）
chcp 65001 >nul
cd /d %~dp0
call conda activate data-agent
python scripts\run_eval_detached.py %*
pause
