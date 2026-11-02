import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # llm.py lee prompts/ con ruta relativa al proyecto

# Claves falsas: ningún test llama a servicios externos, pero los módulos
# crean sus clientes al importarse.
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:1/test")
