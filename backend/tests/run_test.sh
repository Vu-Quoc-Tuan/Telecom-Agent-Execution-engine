#!/bin/bash
export PYTHONPATH=backend
./backend/.venv/bin/python -m unittest backend/tests/test_agent_skill_execution_integration.py
