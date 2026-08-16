#!/usr/bin/env python3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.server import run_server

if __name__ == "__main__":
    print("Iniciando FocusFeed en http://localhost:8080 ...")
    run_server()
