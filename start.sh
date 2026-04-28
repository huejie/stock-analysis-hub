#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "========================================"
echo "  Stock Analysis Hub - One-Click Start"
echo "========================================"
echo

# Check .env
if [ ! -f .env ]; then
    echo "[ERROR] .env file not found!"
    echo "Please copy .env.example to .env and fill in your Baidu OCR credentials."
    echo
    echo "  cp .env.example .env"
    echo
    exit 1
fi

# Check Python
if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    echo "[ERROR] Python not found! Please install Python 3.10+."
    exit 1
fi

PYTHON="${PYTHON:-python3}"
[ ! -x "$(command -v python3)" ] && PYTHON=python

# Create venv if not exists
if [ ! -f .venv/bin/activate ]; then
    echo "[INFO] Creating virtual environment..."
    "$PYTHON" -m venv .venv
    echo "[OK] Virtual environment created."
fi

# Activate venv
source .venv/bin/activate

# Install dependencies
echo "[INFO] Installing dependencies..."
pip install -r requirements.txt -q
echo "[OK] Dependencies installed."

# Create directories
mkdir -p data uploads

# Start server
echo
echo "[OK] Starting server on http://localhost:8888"
echo "Press Ctrl+C to stop."
echo

python run.py
