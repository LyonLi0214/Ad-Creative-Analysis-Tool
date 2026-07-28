#!/usr/bin/env bash
# ==============================================================
# macOS setup script for Ad Creative Analysis Tool
# ==============================================================
set -euo pipefail

echo "=== Ad Creative Analysis Tool — macOS Setup ==="

# 1. Check Python
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v $cmd &>/dev/null; then
        PYTHON=$cmd
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: No Python 3.10+ found. Install via: brew install python@3.12"
    exit 1
fi
echo "Using: $($PYTHON --version)"

# 2. Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv venv
fi
source venv/bin/activate

# 3. Install dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# 4. Setup .env
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — please fill in your credentials."
else
    echo ".env already exists, skipping."
fi

# 5. Create required directories
mkdir -p data downloads logs

# 6. Optional: Install CLI tools
if ! command -v lark-cli &>/dev/null; then
    echo ""
    echo "INFO: lark-cli not found. Install via: npm install -g @lark-project/cli"
    echo "      (needed only for Feishu Bot features)"
fi

echo ""
echo "=== Setup complete! ==="
echo "Quick test:  python analyze_parallel.py --help"
echo "Run:         python analyze_parallel.py --game 心动防线 --limit 5"
