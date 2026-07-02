import os
import logging
from groq import Groq
from config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger(__name__)

# Cliente Groq (singleton)
_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Ruta al ADN — relativa al directorio de ejecución (raíz del proyecto)
_DNA_PATH = os.path.join(os.path.dirname(__file__), 'prompts', 'gaia_dna.txt')


def load_dna() -> str:
    """Lee el ADN de GaIA desde archivo. Cacheable en Fase 1."""
    try:
        with open(_DNA_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.error(f'[LLM] ADN no encontrado en {_DNA_PATH}')
        return ''


def _build_knowledge_block(knowledge_context: str) -> str:
    """
    Construye el bloque de contexto recuperado SIEMPRE marcado explícitamente,
    esté vacío o no — así el modelo sabe con certeza en qué modo está en cada
    turno, en vez de tener que inferirlo (ver prompts/gaia_dna.txt,
    sección 'CÓMO USAR EL CONTEXTO RECUPERADO').
    """
    header = '## CONTEXTO RECUPERADO DE TU KNOWLEDGE PARA ESTA PREGUNTA'
    if knowledge_context:
        return f'{header}\n{knowledge_context}'
    return f'{header}\n(vacío — no se encontró nada relevante en tu Knowledge sobre esta pregunta concreta)'


def call_groq(history: list, cross_memory: str = '', knowledge_context: str = '') -> str:
    """
    Llama a Groq con el historial de la conversación.
    Args:
        history:           Lista de dicts {'role': 'user'|'assistant', 'content': str}
        cross_memory:      Contexto adicional de conversaciones anteriores (FASE 0)
        knowledge_context: Chunks relevantes recuperados por RAG (FASE 2), ya
                            filtrados por relevancia y priorizados por carpeta
                            en knowledge.py — puede venir vacío ('')
    Returns:
        Respuesta de GaIA como string.
    """
    if not _client:
        raise RuntimeError('Groq client no inicializado — revisa GROQ_API_KEY')

    dna    = load_dna()
    system = dna + cross_memory + '\n\n' + _build_knowledge_block(knowledge_context)

    messages = [{'role': 'system', 'content': system}]

    for m in history:
        role = m['role'] if m['role'] in ('user', 'assistant') else 'user'
        messages.append({'role': role, 'content': m['content']})

    logger.info(f'[LLM] Llamando Groq | msgs={len(messages)} | model={GROQ_MODEL} | rag={bool(knowledge_context)}')

    completion = _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1024,
        temperature=0.8,
    )

    response = completion.choices[0].message.content
    logger.info(f'[LLM] ✅ Respuesta: {len(response)} chars')
    return response
