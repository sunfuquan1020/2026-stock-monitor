#!/bin/zsh
cd "$(dirname "$0")"

# 加载用户环境变量（API Keys等）
[ -f ~/.zshrc ] && source ~/.zshrc

exec .venv/bin/python -m src.main --config config.yaml "$@"
