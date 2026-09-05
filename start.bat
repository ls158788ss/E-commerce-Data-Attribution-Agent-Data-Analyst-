@echo off
chcp 65001 >nul
title 智能问数 Data Analyst Agent
cd /d %~dp0

echo ============================================================
echo   智能问数 Data Analyst Agent - 一键启动
echo ============================================================
echo.
echo   [1] Web UI（Streamlit 三栏界面）   <- 默认，回车直接启动
echo   [2] API 服务（FastAPI, 端口 8000）
echo   [3] 命令行提问
echo.

set /p choice="请选择 [1/2/3]，回车默认 1: "

call conda activate data-agent

if "%choice%"=="2" goto api
if "%choice%"=="3" goto cli

:ui
if not exist "data\ecommerce.duckdb" (
    echo 首次运行：生成模拟数据...
    python data\generate_data.py
)
streamlit run ui\streamlit_app.py --server.headless true
goto end

:api
uvicorn api.main:app --host 0.0.0.0 --port 8000
goto end

:cli
set /p q="请输入业务问题: "
python ask.py "%q%"
goto end

:web
where node >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Node.js，请先安装：https://nodejs.org/
    goto end
)
pushd frontend
if not exist "node_modules" (
    echo 首次运行：安装前端依赖（约 1~2 分钟）...
    call npm install
)
call npm run dev
popd

:end
pause
