@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "PORT=8503"
set "APP_FILE=app.py"
set "VENV_DIR=.venv"
set "VENV_PY=.venv\Scripts\python.exe"
set "PYTHON_CMD="
set "BASE_VERSION="
set "BASE_MINOR="
set "VENV_VERSION="
set "VERSION_SUPPORTED="
set "REUSE_VENV="
title 航运数据异常检测系统

echo ======================================================
echo 正在启动：航运数据异常检测系统
echo Python 支持范围：3.10 - 3.14
echo 入口文件：%APP_FILE%
echo 本地地址：http://localhost:%PORT%
echo ======================================================
echo.

if not exist "%APP_FILE%" (
    echo [错误] 找不到入口文件 %APP_FILE%。
    echo 请确认启动脚本位于项目根目录。
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo [错误] 找不到 requirements.txt。
    pause
    exit /b 1
)

echo [1/5] 检查现有虚拟环境...
if exist "%VENV_PY%" (
    for /f "tokens=2" %%V in ('"%VENV_PY%" --version 2^>^&1') do set "VENV_VERSION=%%V"
    call :check_supported_version "!VENV_VERSION!"
    if defined VERSION_SUPPORTED (
        set "REUSE_VENV=1"
        echo 检测已有兼容虚拟环境：Python !VENV_VERSION!
        echo 将复用现有 %VENV_DIR%，不重新创建。
    ) else (
        if defined VENV_VERSION (
            echo 检测到旧虚拟环境使用 Python !VENV_VERSION!。
        ) else (
            echo 检测到无法运行或版本信息损坏的旧虚拟环境。
        )
        echo 该环境与当前项目不兼容。
    )
) else if exist "%VENV_DIR%" (
    echo 检测到不完整的旧虚拟环境。
    echo 该环境将被重建。
) else (
    echo 未检测到现有虚拟环境。
)

if not defined REUSE_VENV (
    echo.
    echo [2/5] 查找兼容 Python...
    call :find_compatible_python
    if not defined PYTHON_CMD goto :unsupported_python

    echo 检测到兼容 Python：!BASE_VERSION!
    echo 本项目将使用 Python !BASE_MINOR! 创建运行环境。

    if exist "%VENV_DIR%" (
        echo 正在重建虚拟环境...
        rmdir /s /q "%VENV_DIR%"
        if exist "%VENV_DIR%" (
            echo [错误] 无法删除旧虚拟环境 %VENV_DIR%。
            echo 请关闭占用该目录的 Python 或 Streamlit 进程，手动删除 %VENV_DIR% 后重试。
            pause
            exit /b 1
        )
    ) else (
        echo 正在创建虚拟环境...
    )

    !PYTHON_CMD! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [错误] 使用 Python !BASE_VERSION! 创建虚拟环境失败。
        echo 请检查 Python 安装是否包含 venv 模块，以及项目目录是否可写。
        pause
        exit /b 1
    )
)

echo.
echo [3/5] 验证虚拟环境 Python...
set "VENV_VERSION="
for /f "tokens=2" %%V in ('"%VENV_PY%" --version 2^>^&1') do set "VENV_VERSION=%%V"
call :check_supported_version "!VENV_VERSION!"
if not defined VERSION_SUPPORTED (
    echo [错误] 虚拟环境 Python 版本无效或不受支持：!VENV_VERSION!
    echo 本项目需要 Python 3.10 - 3.14。
    pause
    exit /b 1
)
echo 虚拟环境版本：Python !VENV_VERSION!

echo.
echo [4/5] 安装/检查生产依赖...
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [错误] pip 升级失败。请检查网络、代理或 PyPI 访问状态。
    pause
    exit /b 1
)
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 生产依赖安装失败。
    echo 请检查网络连接和 requirements.txt 中的错误信息。
    pause
    exit /b 1
)

echo.
echo [5/5] 启动 Streamlit...
echo 如果浏览器没有自动打开，请访问：http://localhost:%PORT%
echo.
"%VENV_PY%" -m streamlit run "%APP_FILE%" --server.port %PORT% --server.address localhost
set "APP_EXIT_CODE=%ERRORLEVEL%"
if not "%APP_EXIT_CODE%"=="0" echo [错误] Streamlit 已退出，错误码：%APP_EXIT_CODE%
exit /b %APP_EXIT_CODE%

:find_compatible_python
for %%V in (3.13 3.12 3.11 3.10 3.14) do call :try_py_launcher %%V
if defined PYTHON_CMD exit /b 0
for %%C in (python3.13 python3.12 python3.11 python3.10 python3.14 python3 python) do call :try_python_command %%C
exit /b 0

:try_py_launcher
if defined PYTHON_CMD exit /b 0
where py >nul 2>nul
if errorlevel 1 exit /b 0
py -%~1 --version >nul 2>nul
if errorlevel 1 exit /b 0
set "PYTHON_CMD=py -%~1"
for /f "tokens=2" %%V in ('py -%~1 --version 2^>^&1') do set "BASE_VERSION=%%V"
set "BASE_MINOR=%~1"
exit /b 0

:try_python_command
if defined PYTHON_CMD exit /b 0
where %~1 >nul 2>nul
if errorlevel 1 exit /b 0
%~1 --version >nul 2>nul
if errorlevel 1 exit /b 0
set "CANDIDATE_VERSION="
for /f "tokens=2" %%V in ('%~1 --version 2^>^&1') do set "CANDIDATE_VERSION=%%V"
call :check_supported_version "!CANDIDATE_VERSION!"
if not defined VERSION_SUPPORTED exit /b 0
set "PYTHON_CMD=%~1"
set "BASE_VERSION=!CANDIDATE_VERSION!"
for /f "tokens=1,2 delims=." %%A in ("!CANDIDATE_VERSION!") do set "BASE_MINOR=%%A.%%B"
exit /b 0

:check_supported_version
set "VERSION_SUPPORTED="
for /f "tokens=1,2 delims=." %%A in ("%~1") do (
    if "%%A"=="3" for %%M in (10 11 12 13 14) do if "%%B"=="%%M" set "VERSION_SUPPORTED=1"
)
exit /b 0

:unsupported_python
echo.
echo [错误] 当前 Python 版本过低，或未找到可以正常运行的兼容 Python。
echo.
echo 本项目需要 Python 3.10 - 3.14。
echo 推荐使用 Python 3.12 或 Python 3.13。
echo.
echo 请安装兼容 Python 后重新启动。
pause
exit /b 1
