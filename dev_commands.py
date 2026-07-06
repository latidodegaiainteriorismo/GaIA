"""
dev_commands.py

Comandos especiales disponibles solo para el desarrollador de GaIA
(identificado por su email, ver config.DEVELOPER_EMAIL / auth.is_developer).

Permite editar en caliente el ADN de GaIA (personalidad general) y el ADN
de astrología (interpretación de cartas) directamente desde el chat, sin
necesidad de tocar el repositorio ni redesplegar.

Sintaxis reconocida (insensible a mayúsculas, con o sin barra inicial):
  /dna añade: <texto>              -> añade texto al final del ADN general
  /dna sustituye: <texto>          -> sustituye el ADN general completo
  /dna ver                         -> devuelve el ADN general actual
  /dna astro añade: <texto>        -> añade texto al ADN de astrología
  /dna astro sustituye: <texto>    -> sustituye el ADN de astrología completo
  /dna astro ver                   -> devuelve el ADN de astrología actual

También se reconocen frases naturales equivalentes, ver _NATURAL_PATTERNS.
"""

import re
import logging
from llm import load_dna, save_dna, load_dna_astrologia, save_dna_astrologia

logger = logging.getLogger(__name__)

# Patrones de comando explícito con barra
_SLASH_PATTERNS = [
    (re.compile(r'^/dna\s+astro\s+a[ñn]ade\s*:\s*(.+)', re.IGNORECASE | re.DOTALL), 'astro_append'),
    (re.compile(r'^/dna\s+astro\s+sustituye\s*:\s*(.+)', re.IGNORECASE | re.DOTALL), 'astro_replace'),
    (re.compile(r'^/dna\s+astro\s+ver\s*$', re.IGNORECASE), 'astro_view'),
    (re.compile(r'^/dna\s+a[ñn]ade\s*:\s*(.+)', re.IGNORECASE | re.DOTALL), 'append'),
    (re.compile(r'^/dna\s+sustituye\s*:\s*(.+)', re.IGNORECASE | re.DOTALL), 'replace'),
    (re.compile(r'^/dna\s+ver\s*$', re.IGNORECASE), 'view'),
]

# Frases naturales equivalentes, para no obligar a recordar sintaxis exacta.
# Se comprueban solo si no hubo match de barra, y de forma más permisiva.
_NATURAL_PATTERNS = [
    (re.compile(r'a[ñn]ade[s]?\s+a\s+tu\s+adn\s+de\s+astrolog[ií]a\s*:?\s*(.+)', re.IGNORECASE | re.DOTALL), 'astro_append'),
    (re.compile(r'incluye\s+en\s+tu\s+adn\s+de\s+astrolog[ií]a\s*:?\s*(.+)', re.IGNORECASE | re.DOTALL), 'astro_append'),
    (re.compile(r'a[ñn]ade[s]?\s+a\s+tu\s+adn\s*:?\s*(.+)', re.IGNORECASE | re.DOTALL), 'append'),
    (re.compile(r'incluye\s+(?:esto\s+)?en\s+tu\s+adn\s*:?\s*(.+)', re.IGNORECASE | re.DOTALL), 'append'),
]


def parse_dev_command(message: str):
    """
    Comprueba si el mensaje es un comando de desarrollador reconocido.
    Devuelve (action, payload) si lo es, o None si el mensaje debe tratarse
    como conversación normal.
    """
    stripped = message.strip()

    for pattern, action in _SLASH_PATTERNS:
        m = pattern.match(stripped)
        if m:
            payload = m.group(1).strip() if m.groups() else None
            return action, payload

    for pattern, action in _NATURAL_PATTERNS:
        m = pattern.search(stripped)
        if m:
            payload = m.group(1).strip()
            return action, payload

    return None


def execute_dev_command(action: str, payload: str) -> str:
    """
    Ejecuta el comando y devuelve el texto de respuesta que GaIA dará al
    desarrollador (esto NO pasa por Groq — es una respuesta directa y
    determinista, apropiada para una acción de configuración).
    """
    if action == 'view':
        current = load_dna()
        return f"Aquí está mi ADN actual ({len(current)} caracteres):\n\n{current}"

    if action == 'astro_view':
        current = load_dna_astrologia()
        if not current:
            return "Todavía no tengo un ADN de astrología específico guardado."
        return f"Aquí está mi ADN de astrología actual ({len(current)} caracteres):\n\n{current}"

    if action == 'append':
        current = load_dna()
        updated = current.rstrip() + '\n\n' + payload.strip()
        ok = save_dna(updated)
        return ("Hecho. Lo acabo de grabar en mi núcleo." if ok
               else "Algo falló al guardar — no se ha modificado nada.")

    if action == 'astro_append':
        current = load_dna_astrologia()
        updated = (current.rstrip() + '\n\n' + payload.strip()) if current else payload.strip()
        ok = save_dna_astrologia(updated)
        return ("Hecho. Actualizado mi forma de leer cartas y tránsitos." if ok
               else "Algo falló al guardar — no se ha modificado nada.")

    if action == 'replace':
        ok = save_dna(payload.strip())
        return ("Hecho. He sustituido mi ADN completo por el nuevo texto." if ok
               else "Algo falló al guardar — no se ha modificado nada.")

    if action == 'astro_replace':
        ok = save_dna_astrologia(payload.strip())
        return ("Hecho. He sustituido mi ADN de astrología completo." if ok
               else "Algo falló al guardar — no se ha modificado nada.")

    return "No he reconocido ese comando."
