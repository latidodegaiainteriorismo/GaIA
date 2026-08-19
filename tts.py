# ── Añadir a chat.py ────────────────────────────────────────────────────────
#
# Sintetiza un fragmento de texto suelto, sin pasar por Groq ni por la base
# de datos. Pensado para "reproducir solo la parte seleccionada" de una
# respuesta que el frontend ya tiene guardada.
#
# Importa también text_to_speech_fragment junto al text_to_speech que ya
# tienes arriba del archivo:
#
#   from tts import text_to_speech, text_to_speech_fragment

@chat_bp.route('/tts', methods=['POST'])
def tts_fragment():
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()

    if not text:
        return jsonify({'error': 'Texto vacío'}), 400

    # Requiere usuario autenticado, igual que el resto del chat, para que
    # nadie use este endpoint como TTS gratis suelto.
    token   = request.headers.get('Authorization', '').replace('Bearer ', '')
    user_id = get_user_from_token(token) if token else None
    if not user_id:
        return jsonify({'error': 'No autorizado'}), 401

    # text_to_speech_fragment() ya trunca internamente a
    # _TTS_FRAGMENT_MAX_CHARS (1500) cortando en frase completa — no hace
    # falta truncar aquí también.
    audio_b64 = text_to_speech_fragment(text)
    if not audio_b64:
        return jsonify({'error': 'No se pudo generar el audio'}), 500

    return jsonify({'audio': audio_b64})
