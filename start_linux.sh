#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[INFO] Création de l'environnement Python..."
  python3 -m venv .venv
fi

source .venv/bin/activate

if [ ! -f ".env" ]; then
  echo "[INFO] Fichier .env absent: copie depuis .env.example"
  cp .env.example .env
fi

echo "[INFO] Installation/vérification des dépendances..."
python -m pip install -r requirements.txt

echo "[INFO] Démarrage de l'application..."
python run_waitress.py
