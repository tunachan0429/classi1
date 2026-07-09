@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   過去問 自動答え合わせツール
echo ============================================
echo.

REM ---- Python があるか確認 ----
python --version > nul 2>&1
if errorlevel 1 (
    echo [エラー] Python が見つかりません。
    echo         https://www.python.org/downloads/ からインストールし、
    echo         インストール時に "Add Python to PATH" にチェックを入れてください。
    echo.
    pause
    exit /b 1
)

REM ---- 初回のみ: 仮想環境と依存関係をセットアップ ----
if not exist ".venv\" (
    echo [初回セットアップ] 環境を準備します。少し時間がかかります...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    echo [初回セットアップ] ブラウザをインストール中...
    python -m playwright install chromium
    echo [初回セットアップ] 完了しました。
    echo.
) else (
    call ".venv\Scripts\activate.bat"
)

REM ---- .env の存在チェック ----
if not exist ".env" (
    echo [注意] .env ファイルがありません。
    echo        ".env.example" を ".env" にコピーして、
    echo        APIキーと問題ページのURLを設定してください。
    echo.
    pause
    exit /b 1
)

REM ---- 本体を実行 (引数はそのまま渡す) ----
python solve.py %*

echo.
pause
