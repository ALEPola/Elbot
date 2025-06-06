#!/bin/sh
# Simple installer for Unix environments
python -m venv .venv
. .venv/bin/activate
pip install -e .
