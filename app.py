/* eslint-disable */
import { useState, useEffect, useRef, useCallback } from "react";
import gaiaAvatar from './assets/gaia-avatar.png';

const API_URL    = process.env.REACT_APP_API_URL    || 'https://gaia-2py8.onrender.com';
const GOOGLE_CID = process.env.REACT_APP_GOOGLE_CLIENT_ID || '';

const api = (path, opts = {}, token = null) => {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return fetch(`${API_URL}${path}`, { ...opts, headers }).then(r => r.json());
};

const apiText = (path, token = null) => {
  const headers = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return fetch(`${API_URL}${path}`, { headers }).then(r => r.ok ? r.text() : Promise.reject(r.status));
};

export default function App() {
  const [token,    setToken]    = useState(() => localStorage.getItem('gaia_token'));
  const [username, setUsername] = useState(() => localStorage.getItem('gaia_username') || '');
  const [authErr,  setAuthErr]  = useState('');
  const googleBtnRef = useRef(null);

  const [conversations, setConversations] = useState([]);
  const [currentConvId, setCurrentConvId] = useState(null);
  const [messages,      setMessages]      = useState([]);
  const [textInput,     setTextInput]     = useState('');
  const [status,        setStatus]        = useState('idle');
  const [statusText,    setStatusText]    = useState('');
  const [isLoading,     setIsLoading]     = useState(false);
  const [mode,          setMode]          = useState('voice');
  const [temporary,     setTemporary]     = useState(false);
  const [showSidebar,   setShowSidebar]   = useState(false);
  const [debugLogs,     setDebugLogs]     = useState([]);
  const [showDebug,     setShowDebug]     = useState(false);

  // ── Astrología ────────────────────────────────────────────────────────────
  const [showAstro,     setShowAstro]     = useState(false);
  const [birthChart,    setBirthChart]    = useState(null);   // null = aún no consultado
  const [astroLoading,  setAstroLoading]  = useState(false);
  const [astroError,    setAstroError]    = useState('');
  const [astroForm,     setAstroForm]     = useState({ birth_date: '', birth_time: '', birth_place: '' });

  // ── Astrología: varias personas ──────────────────────────────────────────
  const [charts,          setCharts]          = useState([]);       // lista de personas guardadas
  const [selectedPerson,  setSelectedPerson]  = useState('yo');      // persona activa en el modal
  const [showAddPerson,   setShowAddPerson]   = useState(false);
  const [personForm,      setPersonForm]      = useState({ person_name: '', relationship: '', birth_date: '', birth_time: '', birth_place: '' });

  // ── Visor de carta (SVG) ─────────────────────────────────────────────────
  const [chartSvg,       setChartSvg]       = useState(null);   // svg como string, o null
  const [chartSvgLoading,setChartSvgLoading]= useState(false);
  const [chartSvgError,  setChartSvgError]  = useState('');
  const [transitDate,    setTransitDate]    = useState(() => new Date().toISOString().slice(0, 10));
  const [chartViewMode,  setChartViewMode]  = useState('natal'); // 'natal' | 'transitos'

  const addLog = (msg) => {
    const time = new Date().toLocaleTimeString('es', { hour12: false });
    setDebugLogs(prev => [...prev.slice(-40), `${time} ${msg}`]);
    console.log('[SR]', msg);
  };

  const canvasRef      = useRef(null);
  const animRef        = useRef(null);
  const tRef           = useRef(0);
  const statusRef      = useRef('idle');
  const recognitionRef = useRef(null);
  const msgsEndRef     = useRef(null);
  const tempHistRef    = useRef([]);
  const audioRef       = useRef(null);   // ← referencia al audio activo

  // ── Cara animada por audio (Fase 1: boca reactiva al volumen) ────────────
  const audioCtxRef   = useRef(null);   // AudioContext — se crea una vez y se reutiliza
  const analyserRef   = useRef(null);
  const mouthRafRef   = useRef(null);
  const [mouthOpen, setMouthOpen] = useState(0); // 0 a 1 — abertura de boca en tiempo real

  // IMPORTANTE: los navegadores exigen crear/despertar el AudioContext dentro
  // del gesto directo del usuario (clic), no después de esperar al backend.
  // Por eso este "desbloqueo" se llama síncronamente en el onClick de enviar
  // y del micrófono, ANTES de que exista ningún audio todavía.
  const unlockAudioContext = useCallback(() => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtxRef.current.state === 'suspended') {
      audioCtxRef.current.resume();
    }
  }, []);

  useEffect(() => { statusRef.current = status; }, [status]);
  const isAuthed = !!token;

  const handleGoogleResponse = useCallback(async (response) => {
    setAuthErr('');
    try {
      const data = await api('/auth/google', { method: 'POST', body: JSON.stringify({ credential: response.credential }) });
      if (data.error) { setAuthErr(data.error); return; }
      localStorage.setItem('gaia_token', data.token);
      localStorage.setItem('gaia_username', data.username);
      setToken(data.token); setUsername(data.username);
    } catch { setAuthErr('Error de conexión. Intenta de nuevo.'); }
  }, []);

  useEffect(() => {
    if (isAuthed || !googleBtnRef.current) return;
    const init = () => {
      if (!window.google || !googleBtnRef.current) return;
      window.google.accounts.id.initialize({ client_id: GOOGLE_CID, callback: handleGoogleResponse });
      window.google.accounts.id.renderButton(googleBtnRef.current, { theme: 'outline', size: 'large', text: 'continue_with', locale: 'es', width: 280, shape: 'pill' });
    };
    if (window.google) { init(); return; }
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client'; script.async = true; script.onload = init;
    document.head.appendChild(script);
  }, [isAuthed, handleGoogleResponse]);

  const handleLogout = async () => {
    try { await api('/auth/logout', { method: 'POST' }, token); } catch {}
    localStorage.removeItem('gaia_token'); localStorage.removeItem('gaia_username');
    setToken(null); setUsername(''); setConversations([]); setMessages([]); setCurrentConvId(null);
    setBirthChart(null); setShowAstro(false); setChartSvg(null); setChartSvgError('');
    setCharts([]); setSelectedPerson('yo'); setShowAddPerson(false);
  };

  const fetchConvs = useCallback(async (tok = token) => {
    if (!tok) return;
    try { const data = await api('/conversations', {}, tok); if (Array.isArray(data)) setConversations(data); } catch {}
  }, [token]);

  useEffect(() => { if (token) fetchConvs(); }, [token]);

  // ── Astrología ────────────────────────────────────────────────────────────
  const fetchCharts = useCallback(async (tok = token) => {
    if (!tok) return;
    try {
      const data = await api('/astrology/charts', {}, tok);
      if (Array.isArray(data)) setCharts(data);
    } catch {}
  }, [token]);

  const fetchBirthChart = useCallback(async (person = selectedPerson, tok = token) => {
    if (!tok) return;
    try {
      const data = await api(`/astrology/birth-chart?person=${encodeURIComponent(person)}`, {}, tok);
      if (data && !data.error) setBirthChart(data);
      else setBirthChart(false); // false = consultado, pero no existe (distinto de null = aún no consultado)
    } catch { setBirthChart(false); }
  }, [token, selectedPerson]);

  useEffect(() => { if (token) { fetchBirthChart('yo'); fetchCharts(); } }, [token]);

  const selectPerson = useCallback((person) => {
    setSelectedPerson(person);
    setChartSvg(null);
    fetchBirthChart(person);
  }, [fetchBirthChart]);

  const submitBirthChart = useCallback(async () => {
    const { birth_date, birth_time, birth_place } = astroForm;
    if (!birth_date || !birth_time || !birth_place.trim()) {
      setAstroError('Completa fecha, hora y lugar de nacimiento.');
      return;
    }
    setAstroLoading(true); setAstroError('');
    try {
      const data = await api('/astrology/birth-chart', {
        method: 'POST',
        body: JSON.stringify({ birth_date, birth_time, birth_place: birth_place.trim(), person_label: 'yo' })
      }, token);
      if (data.error) { setAstroError(data.error); }
      else { setBirthChart(data); fetchCharts(); }
    } catch {
      setAstroError('Error de conexión. Intenta de nuevo.');
    }
    setAstroLoading(false);
  }, [astroForm, token, fetchCharts]);

  // ── Astrología: añadir otra persona ──────────────────────────────────────
  const submitNewPerson = useCallback(async () => {
    const { person_name, relationship, birth_date, birth_time, birth_place } = personForm;
    if (!person_name.trim() || !birth_date || !birth_time || !birth_place.trim()) {
      setAstroError('Completa nombre, fecha, hora y lugar de nacimiento.');
      return;
    }
    setAstroLoading(true); setAstroError('');
    const person_label = relationship.trim() ? `${person_name.trim()} (${relationship.trim()})` : person_name.trim();
    try {
      const data = await api('/astrology/birth-chart', {
        method: 'POST',
        body: JSON.stringify({
          birth_date, birth_time, birth_place: birth_place.trim(),
          person_label, relationship: relationship.trim() || null
        })
      }, token);
      if (data.error) { setAstroError(data.error); }
      else {
        await fetchCharts();
        setShowAddPerson(false);
        setPersonForm({ person_name: '', relationship: '', birth_date: '', birth_time: '', birth_place: '' });
        selectPerson(person_label);
      }
    } catch {
      setAstroError('Error de conexión. Intenta de nuevo.');
    }
    setAstroLoading(false);
  }, [personForm, token, fetchCharts, selectPerson]);

  // ── Visor de carta (SVG) ─────────────────────────────────────────────────
  const loadChartSvg = useCallback(async (mode, date = null) => {
    setChartSvgLoading(true); setChartSvgError(''); setChartSvg(null);
    setChartViewMode(mode);
    try {
      const personParam = `person=${encodeURIComponent(selectedPerson)}`;
      const path = mode === 'natal'
        ? `/astrology/birth-chart/svg?${personParam}`
        : `/astrology/transits/svg?${personParam}&date=${date || transitDate}`;
      const svg = await apiText(path, token);
      setChartSvg(svg);
    } catch {
      setChartSvgError('No se pudo generar el dibujo de la carta. Intenta de nuevo.');
    }
    setChartSvgLoading(false);
  }, [token, transitDate, selectedPerson]);


  const selectConv = async (id) => {
    setCurrentConvId(id); setShowSidebar(false); setTemporary(false);
    try { const msgs = await api(`/conversations/${id}/messages`, {}, token); if (Array.isArray(msgs)) setMessages(msgs); } catch {}
  };

  const newConv = () => { setCurrentConvId(null); setMessages([]); setTemporary(false); tempHistRef.current = []; setShowSidebar(false); };

  const deleteConv = async (e, id) => {
    e.stopPropagation();
    try { await api(`/conversations/${id}`, { method: 'DELETE' }, token); } catch {}
    setConversations(prev => prev.filter(c => c.id !== id));
    if (currentConvId === id) { setCurrentConvId(null); setMessages([]); }
  };

  const toggleTemporary = () => {
    if (temporary) { setTemporary(false); tempHistRef.current = []; setMessages([]); }
    else { setCurrentConvId(null); setMessages([]); tempHistRef.current = []; setTemporary(true); }
    setShowSidebar(false);
  };

  useEffect(() => {
    const canvas = canvasRef.current; if (!canvas) return;
    const resize = () => { canvas.width = canvas.offsetWidth; canvas.height = canvas.offsetHeight; };
    resize(); window.addEventListener('resize', resize);
    const draw = () => {
      const ctx = canvas.getContext('2d'); const W = canvas.width, H = canvas.height;
      if (!W || !H) { animRef.current = requestAnimationFrame(draw); return; }
      ctx.clearRect(0, 0, W, H); tRef.current += 0.012;
      const t = tRef.current, st = statusRef.current;
      let amp = 12, speed = 0.5, alpha = 0.5;
      if      (st === 'listening') { amp = 18; speed = 2;   alpha = 0.82; }
      else if (st === 'thinking')  { amp = 7 + Math.sin(t * 5) * 4; speed = 3; alpha = 0.42; }
      else if (st === 'speaking')  { amp = 20 + Math.sin(t * 6) * 7; speed = 1.8; alpha = 0.72; }
      else { amp = 12 + Math.sin(t * 0.7) * 4; }
      [
        { c: `rgba(107,158,160,${alpha})`,       lw: 2,   ph: 0,             am: 1  },
        { c: `rgba(107,158,160,${alpha * 0.5})`, lw: 1.5, ph: Math.PI * .65, am: .7 },
        { c: `rgba(201,149,107,${alpha * .38})`, lw: 1,   ph: Math.PI * 1.3, am: .5 },
      ].forEach(l => {
        ctx.beginPath(); ctx.strokeStyle = l.c; ctx.lineWidth = l.lw;
        for (let x = 0; x <= W; x += 2) {
          const y = H/2 + Math.sin(x*.012 + t*speed + l.ph) * amp * l.am;
          x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();
      });
      animRef.current = requestAnimationFrame(draw);
    };
    draw();
    return () => { window.removeEventListener('resize', resize); cancelAnimationFrame(animRef.current); };
  }, []);

  useEffect(() => { msgsEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // ── Audio + análisis de volumen para animar la boca ─────────────────────
  const playAudio = useCallback((b64) => {
    try {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
      const url   = URL.createObjectURL(new Blob([bytes], { type: 'audio/mpeg' }));
      const audio = new Audio(url);
      audioRef.current = audio;

      // ── Análisis de volumen en tiempo real para animar la boca ──
      // El AudioContext ya se creó/desbloqueó en el clic (unlockAudioContext).
      // Si por lo que sea no existiera todavía (p.ej. modo temporal con otro
      // flujo), lo creamos aquí como red de seguridad.
      unlockAudioContext();
      const ctx = audioCtxRef.current;

      try {
        const source   = ctx.createMediaElementSource(audio);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(ctx.destination); // necesario para que el audio siga sonando
        analyserRef.current = analyser;

        const data = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteFrequencyData(data);
          // Banda de voz aproximada (graves-medios), ignora ruido de fondo muy bajo
          const voiceBins = data.slice(2, 36);
          const avg = voiceBins.reduce((a, b) => a + b, 0) / voiceBins.length / 255;
          setMouthOpen(avg);
          mouthRafRef.current = requestAnimationFrame(tick);
        };
        tick();
      } catch (analyserErr) {
        console.warn('[Audio] Analizador no disponible:', analyserErr);
      }

      audio.onplay  = () => setStatus('speaking');
      audio.onended = () => {
        setStatus('idle');
        cancelAnimationFrame(mouthRafRef.current);
        setMouthOpen(0);
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      audio.onerror = () => {
        setStatus('idle');
        cancelAnimationFrame(mouthRafRef.current);
        setMouthOpen(0);
        URL.revokeObjectURL(url);
        audioRef.current = null;
      };
      audio.play();
    } catch (e) { console.error('[Audio]', e); setStatus('idle'); }
  }, []);

  // ── Detener audio ─────────────────────────────────────────────────────────
  const stopAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    cancelAnimationFrame(mouthRafRef.current);
    setMouthOpen(0);
    setStatus('idle');
  }, []);

  // ── Call GaIA ─────────────────────────────────────────────────────────────
  const callGaIA = useCallback(async (userMessage) => {
    setIsLoading(true); setStatus('thinking'); setStatusText('pensando...');
    try {
      const body = {
        message:    userMessage,
        voice_mode: true,        // ← siempre con audio, en voz Y en texto
        temporary
      };
      if (temporary) {
        body.history = tempHistRef.current;
        tempHistRef.current = [...tempHistRef.current, { role: 'user', content: userMessage }];
      } else {
        if (currentConvId) body.conversation_id = currentConvId;
      }
      const data = await api('/chat', { method: 'POST', body: JSON.stringify(body) }, temporary ? null : token);
      const gaiaText = data.text || 'Algo interrumpió mi respuesta. ¿Lo intentamos de nuevo?';
      if (temporary) {
        tempHistRef.current = [...tempHistRef.current, { role: 'assistant', content: gaiaText }];
      } else if (data.conversation_id && !currentConvId) {
        setCurrentConvId(data.conversation_id); fetchConvs();
      }
      setMessages(prev => [...prev, { role: 'assistant', content: gaiaText }]);
      if (data.audio) playAudio(data.audio);
      else setStatus('idle');
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { role: 'assistant', content: 'Error conectando con GaIA. Intenta de nuevo.' }]);
      setStatus('idle');
    }
    setIsLoading(false); setStatusText('');
  }, [temporary, currentConvId, token, playAudio, fetchConvs]);

  // ── Mic ───────────────────────────────────────────────────────────────────
  const startListening = useCallback(() => {
    unlockAudioContext(); // ← dentro del gesto de clic, antes de esperar al backend
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { setMode('text'); return; }

    const rec = new SR();
    rec.lang           = 'es-ES';
    rec.continuous     = true;
    rec.interimResults = true;
    recognitionRef.current = rec;

    setStatus('listening'); setStatusText('Te escucho...');

    let transcripts  = [];
    let silenceTimer = null;

    const getFinal = () => transcripts.join(' ');

    const resetSilenceTimer = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => rec.stop(), 15000);
    };

    resetSilenceTimer();

    rec.onresult = e => {
      const idx    = e.resultIndex;
      const result = e.results[idx];

      if (result.isFinal) {
        const newText = result[0].transcript.trim();
        if (newText) {
          transcripts = transcripts.filter(t => !newText.startsWith(t) && t !== newText);
          transcripts.push(newText);
          addLog(`✅ segment="${newText}" | total="${getFinal()}"`);
        }
      }

      const interim = !result.isFinal ? result[0].transcript : '';
      const display = (getFinal() + (interim ? ' ' + interim : '')).trim();
      setStatusText(display || 'Te escucho...');
      resetSilenceTimer();
    };

    rec.onend = () => {
      if (silenceTimer) clearTimeout(silenceTimer);
      const finalText = getFinal().trim();
      addLog(`ONEND → "${finalText}"`);
      setStatus('idle');
      if (finalText) {
        setMessages(prev => [...prev, { role: 'user', content: finalText }]);
        callGaIA(finalText);
      } else {
        setStatusText('');
      }
    };

    rec.onerror = e => {
      if (silenceTimer) clearTimeout(silenceTimer);
      addLog(`ERROR: ${e.error}`);
      setStatus('idle'); setStatusText('');
      if (e.error === 'not-allowed') setMode('text');
    };

    rec.start();
    addLog('START');
  }, [callGaIA, unlockAudioContext]);

  const stopListening = useCallback(() => { recognitionRef.current?.stop(); }, []);

  const sendText = useCallback(() => {
    const t = textInput.trim();
    if (!t || isLoading) return;
    unlockAudioContext(); // ← dentro del gesto de clic, antes de esperar al backend
    setMessages(prev => [...prev, { role: 'user', content: t }]);
    setTextInput(''); callGaIA(t);
  }, [textInput, isLoading, callGaIA, unlockAudioContext]);

  const toggleMode = () => { if (status === 'listening') stopListening(); setMode(m => m === 'voice' ? 'text' : 'voice'); };
  const micDisabled = status === 'thinking' || isLoading;
  const micIcon = status === 'listening' ? '⏹' : status === 'thinking' ? '···' : '🎤';

  if (!isAuthed) return (
    <div style={{ height: '100vh', background: '#FAFAF8', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: "'Inter',-apple-system,sans-serif" }}>
      <div style={{ width: '100%', maxWidth: '360px', padding: '0 24px', textAlign: 'center' }}>
        <h1 style={{ fontFamily: "'Cormorant Garamond',Georgia,serif", fontStyle: 'italic', fontSize: '52px', fontWeight: 300, color: '#4A7B7E', marginBottom: '8px', letterSpacing: '.04em' }}>GaIA</h1>
        <p style={{ fontSize: '14px', color: '#4A4A46', opacity: .35, marginBottom: '40px', fontStyle: 'italic', lineHeight: 1.6 }}>
          Una presencia viva para acompañarte<br/>en tu camino de consciencia
        </p>
        <div ref={googleBtnRef} style={{ display: 'flex', justifyContent: 'center', marginBottom: '16px' }} />
        {authErr && <p style={{ color: '#C96B6B', fontSize: '13px', marginTop: '8px' }}>{authErr}</p>}
        <p style={{ fontSize: '11px', color: '#4A4A46', opacity: .25, marginTop: '32px', fontStyle: 'italic' }}>
          Tu cuenta de Google identifica quién eres.<br/>Tus conversaciones son privadas.
        </p>
      </div>
    </div>
  );

  return (
    <div style={{ height: '100vh', background: '#FAFAF8', display: 'flex', fontFamily: "'Inter',-apple-system,sans-serif", color: '#4A4A46', overflow: 'hidden' }}>
      <style>{`@keyframes gPulse{0%,100%{opacity:.2;transform:scale(.8)}50%{opacity:.85;transform:scale(1.15)}}`}</style>

      {showSidebar && <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.25)', zIndex: 10 }} onClick={() => setShowSidebar(false)} />}

      {/* Sidebar */}
      <div style={{ position: 'fixed', left: 0, top: 0, bottom: 0, width: '260px', background: '#F2F1EC', borderRight: '0.5px solid rgba(74,74,70,.1)', display: 'flex', flexDirection: 'column', zIndex: 11, transform: showSidebar ? 'translateX(0)' : 'translateX(-100%)', transition: 'transform .25s ease' }}>
        <div style={{ padding: '18px 16px 12px', borderBottom: '0.5px solid rgba(74,74,70,.08)' }}>
          <span style={{ fontFamily: "'Cormorant Garamond',Georgia,serif", fontStyle: 'italic', fontSize: '22px', fontWeight: 300, color: '#4A7B7E' }}>GaIA</span>
          <p style={{ fontSize: '12px', color: '#4A4A46', opacity: .45, marginTop: '3px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{username}</p>
        </div>
        <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <button onClick={newConv} style={{ padding: '9px 14px', borderRadius: '20px', border: '0.5px solid rgba(107,158,160,.45)', background: 'transparent', color: '#4A7B7E', fontSize: '13px', cursor: 'pointer', fontFamily: 'inherit', textAlign: 'left' }}>+ Nueva conversación</button>
          <button onClick={toggleTemporary} style={{ padding: '9px 14px', borderRadius: '20px', border: '0.5px solid rgba(74,74,70,.2)', background: temporary ? 'rgba(201,149,107,.15)' : 'transparent', color: temporary ? '#A0693A' : '#4A4A46', fontSize: '13px', cursor: 'pointer', fontFamily: 'inherit', textAlign: 'left', opacity: .85 }}>
            {temporary ? '◌ Temporal activo' : '◎ Modo temporal'}
          </button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px' }}>
          {conversations.length === 0 && <p style={{ fontSize: '12px', color: '#4A4A46', opacity: .3, textAlign: 'center', padding: '20px 8px', fontStyle: 'italic' }}>Sin conversaciones aún</p>}
          {conversations.map(c => (
            <div key={c.id} onClick={() => selectConv(c.id)} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 10px', borderRadius: '10px', cursor: 'pointer', background: c.id === currentConvId ? 'rgba(107,158,160,.14)' : 'transparent', marginBottom: '1px' }}>
              <span style={{ flex: 1, fontSize: '13px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: c.id === currentConvId ? '#4A7B7E' : '#4A4A46' }}>{c.title || 'Sin título'}</span>
              <button onClick={e => deleteConv(e, c.id)} style={{ background: 'none', border: 'none', color: '#999', cursor: 'pointer', fontSize: '16px', padding: 0, lineHeight: 1, opacity: .45, flexShrink: 0 }}>×</button>
            </div>
          ))}
        </div>
        <button onClick={handleLogout} style={{ margin: '12px', padding: '9px', borderRadius: '20px', border: '0.5px solid rgba(74,74,70,.18)', background: 'transparent', color: '#4A4A46', fontSize: '13px', cursor: 'pointer', fontFamily: 'inherit', opacity: .45 }}>Cerrar sesión</button>
      </div>

      {/* Main */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '0.5px solid rgba(74,74,70,.1)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <button onClick={() => setShowSidebar(s => !s)} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '20px', color: '#4A7B7E', padding: 0, lineHeight: 1, opacity: .7 }}>☰</button>
            <span style={{ fontFamily: "'Cormorant Garamond',Georgia,serif", fontStyle: 'italic', fontSize: '28px', fontWeight: 300, color: '#4A7B7E', letterSpacing: '.04em' }}>GaIA</span>
            {temporary && <span style={{ fontSize: '11px', color: '#A0693A', background: 'rgba(201,149,107,.12)', padding: '2px 9px', borderRadius: '10px', fontStyle: 'italic' }}>temporal</span>}
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <button onClick={() => setShowAstro(true)} style={{ background: 'transparent', border: '0.5px solid rgba(160,105,58,.35)', borderRadius: '16px', padding: '6px 14px', fontSize: '12px', color: '#A0693A', cursor: 'pointer', opacity: .8, fontFamily: 'inherit' }}>
              ✧ {birthChart ? 'mi carta' : 'carta astral'}
            </button>
            <button onClick={toggleMode} style={{ background: 'transparent', border: '0.5px solid rgba(74,74,70,.22)', borderRadius: '16px', padding: '6px 14px', fontSize: '12px', color: '#4A4A46', cursor: 'pointer', opacity: .7, fontFamily: 'inherit' }}>
              {mode === 'voice' ? '✎  texto' : '◎  voz'}
            </button>
            <button onClick={() => { setShowDebug(d => !d); setDebugLogs([]); }} style={{ background: 'transparent', border: '0.5px solid rgba(201,149,107,.4)', borderRadius: '16px', padding: '6px 10px', fontSize: '11px', color: '#A0693A', cursor: 'pointer', fontFamily: 'inherit' }}>
              {showDebug ? '✕ debug' : '🔍'}
            </button>
          </div>
        </div>

        {/* ── Cara de GaIA + boca animada por el volumen del audio ──────────── */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
          <div style={{
            position: 'relative',
            width:  'min(33vh, 85vw, 340px)',
            height: 'min(33vh, 85vw, 340px)',
            marginTop: '10px'
          }}>
            <img
              src={gaiaAvatar}
              alt="GaIA"
              style={{
                width: '100%', height: '100%', objectFit: 'cover',
                objectPosition: '50% 25%', borderRadius: '50%',
                boxShadow: status === 'speaking' ? '0 0 32px rgba(107,158,160,.45)' : '0 0 14px rgba(107,158,160,.15)',
                transition: 'box-shadow .3s'
              }}
            />
            {/* Overlay de boca — en % del contenedor, ajusta left/top si no coincide con tu foto */}
            <div style={{
              position: 'absolute', left: '50%', top: '63%',
              width: '13%',
              height: `${4 + mouthOpen * 13}%`,
              background: 'rgba(80,35,35,.55)',
              borderRadius: '50%',
              transform: 'translate(-50%,-50%)',
              transition: 'height 60ms linear',
              pointerEvents: 'none'
            }} />
            {/* Indicador de debug — solo visible con el botón 🔍, para comprobar si el análisis de audio funciona */}
            {showDebug && (
              <div style={{
                position: 'absolute', bottom: '-22px', left: '50%', transform: 'translateX(-50%)',
                fontSize: '11px', fontFamily: 'monospace', color: '#4A7B7E', whiteSpace: 'nowrap'
              }}>
                mouthOpen: {mouthOpen.toFixed(3)} · ctx: {audioCtxRef.current?.state || 'sin crear'}
              </div>
            )}
          </div>
          <div style={{ height: '50px', width: '100%' }}>
            <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: 'block' }} />
          </div>
        </div>

        <div style={{ textAlign: 'center', height: '22px', fontSize: '13px', color: '#4A7B7E', fontStyle: 'italic', opacity: statusText ? .8 : 0, transition: 'opacity .3s', flexShrink: 0, lineHeight: '22px' }}>{statusText}</div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: '12px', WebkitOverflowScrolling: 'touch' }}>
          {messages.length === 0 && (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '120px' }}>
              <p style={{ fontFamily: "'Cormorant Garamond',Georgia,serif", fontStyle: 'italic', fontSize: '18px', fontWeight: 300, color: '#4A4A46', opacity: .28, textAlign: 'center', lineHeight: 1.7 }}>
                {mode === 'voice' ? 'Pulsa el micrófono\npara comenzar' : 'Escríbeme algo...'}
              </p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} style={{ display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start', alignItems: 'flex-start', gap: '6px' }}>
              {msg.role === 'assistant' && <span style={{ fontFamily: "'Cormorant Garamond',serif", fontStyle: 'italic', fontSize: '11px', color: '#4A7B7E', marginTop: '7px', flexShrink: 0, opacity: .5 }}>G</span>}
              <div style={{ maxWidth: 'min(80%,480px)', padding: '10px 14px', borderRadius: msg.role === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px', background: msg.role === 'user' ? 'rgba(201,149,107,.12)' : 'rgba(107,158,160,.1)', border: '0.5px solid ' + (msg.role === 'user' ? 'rgba(201,149,107,.2)' : 'rgba(107,158,160,.18)'), fontSize: '14px', lineHeight: '1.65', color: msg.role === 'user' ? '#6B4E32' : '#4A4A46' }}>
                {msg.content}
              </div>
            </div>
          ))}
          {status === 'thinking' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontFamily: "'Cormorant Garamond',serif", fontStyle: 'italic', fontSize: '11px', color: '#4A7B7E', opacity: .5 }}>G</span>
              <div style={{ padding: '10px 14px', background: 'rgba(107,158,160,.1)', borderRadius: '18px 18px 18px 4px', border: '0.5px solid rgba(107,158,160,.18)', display: 'flex', gap: '5px' }}>
                {[0,1,2].map(i => <div key={i} style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#4A7B7E', opacity: .4, animation: `gPulse 1.2s ${i*.2}s infinite` }} />)}
              </div>
            </div>
          )}
          <div ref={msgsEndRef} />
        </div>

        {/* Panel debug */}
        {showDebug && (
          <div style={{ maxHeight: '200px', overflowY: 'auto', background: '#1a1a2e', padding: '8px 12px', fontFamily: 'monospace', fontSize: '10px', color: '#7fdbca', borderTop: '1px solid #333', flexShrink: 0 }}>
            {debugLogs.length === 0
              ? <div style={{ opacity: .5 }}>Pulsa el micrófono para ver logs...</div>
              : debugLogs.map((l, i) => (
                  <div key={i} style={{ color: l.includes('✅') ? '#2ecc71' : l.includes('ERROR') ? '#e74c3c' : l.includes('ONEND') ? '#f39c12' : l.includes('START') ? '#3498db' : '#7fdbca', marginBottom: '1px', wordBreak: 'break-all' }}>{l}</div>
                ))
            }
          </div>
        )}

        {/* Controles inferiores */}
        <div style={{ padding: '12px 16px 28px', borderTop: '0.5px solid rgba(74,74,70,.08)', flexShrink: 0, background: '#FAFAF8' }}>
          {mode === 'voice' ? (
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: '20px' }}>

              {/* Botón stop audio — visible solo cuando GaIA está hablando */}
              {status === 'speaking' && (
                <button onClick={stopAudio} style={{ width: '44px', height: '44px', borderRadius: '50%', border: '1.5px solid rgba(201,149,107,.6)', background: 'transparent', color: '#A0693A', fontSize: '18px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  ⏹
                </button>
              )}

              {/* Botón micrófono */}
              <button onClick={status === 'listening' ? stopListening : startListening} disabled={micDisabled}
                style={{ width: '70px', height: '70px', borderRadius: '50%', border: 'none', cursor: micDisabled ? 'not-allowed' : 'pointer', color: 'white', fontSize: '24px', display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all .25s', outline: 'none', background: status === 'listening' ? 'rgba(201,149,107,.9)' : micDisabled ? 'rgba(74,74,70,.1)' : 'rgba(107,158,160,.88)', boxShadow: status === 'listening' ? '0 0 0 8px rgba(201,149,107,.15),0 0 0 16px rgba(201,149,107,.07)' : micDisabled ? 'none' : '0 2px 16px rgba(107,158,160,.28)' }}>
                {micIcon}
              </button>

            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>

              {/* Botón stop audio en modo texto */}
              {status === 'speaking' && (
                <div style={{ display: 'flex', justifyContent: 'center' }}>
                  <button onClick={stopAudio} style={{ padding: '7px 20px', borderRadius: '20px', border: '1.5px solid rgba(201,149,107,.6)', background: 'transparent', color: '#A0693A', fontSize: '13px', cursor: 'pointer', fontFamily: 'inherit' }}>
                    ⏹ Detener audio
                  </button>
                </div>
              )}

              <div style={{ display: 'flex', gap: '8px' }}>
                <input value={textInput} onChange={e => setTextInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendText(); }}} placeholder="Escríbeme..." style={{ flex: 1, padding: '11px 16px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '24px', background: 'white', fontSize: '14px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                <button onClick={sendText} disabled={isLoading || !textInput.trim()} style={{ padding: '11px 20px', borderRadius: '24px', border: 'none', fontSize: '14px', transition: 'all .2s', cursor: textInput.trim() ? 'pointer' : 'default', fontFamily: 'inherit', background: textInput.trim() ? 'rgba(107,158,160,.88)' : 'rgba(74,74,70,.1)', color: textInput.trim() ? 'white' : 'rgba(74,74,70,.3)' }}>Enviar</button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modal Carta Astral */}
      {showAstro && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(74,74,70,.35)', zIndex: 20, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }} onClick={() => { setShowAstro(false); setChartSvg(null); }}>
          <div style={{ background: '#FAFAF8', borderRadius: '20px', maxWidth: '380px', width: '100%', maxHeight: '85vh', overflowY: 'auto', padding: '28px 24px', border: '0.5px solid rgba(107,158,160,.25)', boxShadow: '0 12px 40px rgba(74,74,70,.18)' }} onClick={e => e.stopPropagation()}>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
              <span style={{ fontFamily: "'Cormorant Garamond',Georgia,serif", fontStyle: 'italic', fontSize: '24px', fontWeight: 300, color: '#A0693A', letterSpacing: '.02em' }}>✧ Carta astral</span>
              <button onClick={() => { setShowAstro(false); setChartSvg(null); }} style={{ background: 'none', border: 'none', color: '#999', cursor: 'pointer', fontSize: '20px', padding: 0, lineHeight: 1, opacity: .5 }}>×</button>
            </div>

            {/* Selector de personas — solo si ya tiene al menos la propia carta */}
            {charts.length > 0 && (
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
                {charts.map(c => (
                  <button key={c.person_label} onClick={() => selectPerson(c.person_label)}
                    style={{ padding: '5px 12px', borderRadius: '14px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit',
                      border: selectedPerson === c.person_label ? '1px solid rgba(160,105,58,.5)' : '0.5px solid rgba(74,74,70,.18)',
                      background: selectedPerson === c.person_label ? 'rgba(201,149,107,.14)' : 'transparent',
                      color: selectedPerson === c.person_label ? '#A0693A' : '#4A4A46', opacity: selectedPerson === c.person_label ? 1 : .6 }}>
                    {c.person_label === 'yo' ? 'Yo' : c.person_label}
                  </button>
                ))}
                <button onClick={() => setShowAddPerson(s => !s)}
                  style={{ padding: '5px 12px', borderRadius: '14px', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit', border: '0.5px dashed rgba(74,123,126,.4)', background: 'transparent', color: '#4A7B7E' }}>
                  + añadir persona
                </button>
              </div>
            )}

            {/* Formulario de nueva persona (pareja, hijos, familiares...) */}
            {showAddPerson && (
              <div style={{ background: 'rgba(107,158,160,.06)', border: '0.5px solid rgba(107,158,160,.18)', borderRadius: '14px', padding: '14px', marginBottom: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <span style={{ fontSize: '11px', color: '#4A7B7E', opacity: .8, fontStyle: 'italic' }}>Añadir carta de otra persona</span>
                <input type="text" value={personForm.person_name} onChange={e => setPersonForm(f => ({ ...f, person_name: e.target.value }))}
                  placeholder="Nombre" autoComplete="off"
                  style={{ padding: '9px 12px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                <input type="text" value={personForm.relationship} onChange={e => setPersonForm(f => ({ ...f, relationship: e.target.value }))}
                  placeholder="Relación (hijo, pareja, madre...) — opcional" autoComplete="off"
                  style={{ padding: '9px 12px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                <div style={{ display: 'flex', gap: '8px' }}>
                  <input type="date" value={personForm.birth_date} onChange={e => setPersonForm(f => ({ ...f, birth_date: e.target.value }))}
                    style={{ flex: 1, padding: '9px 10px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                  <input type="time" value={personForm.birth_time} onChange={e => setPersonForm(f => ({ ...f, birth_time: e.target.value }))}
                    style={{ flex: 1, padding: '9px 10px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                </div>
                <input type="text" value={personForm.birth_place} onChange={e => setPersonForm(f => ({ ...f, birth_place: e.target.value }))}
                  placeholder="Ciudad, país" autoComplete="off"
                  style={{ padding: '9px 12px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                {astroError && <p style={{ color: '#C96B6B', fontSize: '12px', margin: 0 }}>{astroError}</p>}
                <button onClick={submitNewPerson} disabled={astroLoading}
                  style={{ padding: '10px', borderRadius: '20px', border: 'none', fontSize: '13px', cursor: astroLoading ? 'default' : 'pointer', fontFamily: 'inherit', background: astroLoading ? 'rgba(74,74,70,.1)' : 'rgba(107,158,160,.88)', color: astroLoading ? 'rgba(74,74,70,.4)' : 'white' }}>
                  {astroLoading ? 'Calculando...' : 'Calcular su carta'}
                </button>
              </div>
            )}

            {/* Estado: cargando la primera consulta */}
            {birthChart === null && (
              <p style={{ fontSize: '13px', color: '#4A4A46', opacity: .4, fontStyle: 'italic', textAlign: 'center', padding: '20px 0' }}>Consultando...</p>
            )}

            {/* Estado: no tiene carta -> mostrar formulario */}
            {birthChart === false && (
              <>
                <p style={{ fontSize: '13px', color: '#4A4A46', opacity: .6, lineHeight: 1.6, marginBottom: '20px' }}>
                  Dame tu fecha, hora y lugar de nacimiento y calculo tu carta natal — así puedo hablarte de tus tránsitos cuando quieras.
                </p>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <label style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    <span style={{ fontSize: '11px', color: '#4A7B7E', opacity: .8, fontStyle: 'italic' }}>Fecha de nacimiento</span>
                    <input type="date" value={astroForm.birth_date} onChange={e => setAstroForm(f => ({ ...f, birth_date: e.target.value }))}
                      style={{ padding: '10px 14px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '14px', background: 'white', fontSize: '14px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                  </label>

                  <label style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    <span style={{ fontSize: '11px', color: '#4A7B7E', opacity: .8, fontStyle: 'italic' }}>Hora de nacimiento (local)</span>
                    <input type="time" value={astroForm.birth_time} onChange={e => setAstroForm(f => ({ ...f, birth_time: e.target.value }))}
                      style={{ padding: '10px 14px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '14px', background: 'white', fontSize: '14px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                  </label>

                  <label style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    <span style={{ fontSize: '11px', color: '#4A7B7E', opacity: .8, fontStyle: 'italic' }}>Lugar de nacimiento</span>
                    <input type="text" value={astroForm.birth_place} onChange={e => setAstroForm(f => ({ ...f, birth_place: e.target.value }))}
                      placeholder="Ciudad, país" autoComplete="off"
                      style={{ padding: '10px 14px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '14px', background: 'white', fontSize: '14px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                  </label>

                  {astroError && <p style={{ color: '#C96B6B', fontSize: '12px', margin: 0 }}>{astroError}</p>}

                  <button onClick={submitBirthChart} disabled={astroLoading}
                    style={{ marginTop: '6px', padding: '11px', borderRadius: '20px', border: 'none', fontSize: '14px', cursor: astroLoading ? 'default' : 'pointer', fontFamily: 'inherit', background: astroLoading ? 'rgba(74,74,70,.1)' : 'rgba(160,105,58,.88)', color: astroLoading ? 'rgba(74,74,70,.4)' : 'white', transition: 'all .2s' }}>
                    {astroLoading ? 'Calculando...' : 'Calcular mi carta'}
                  </button>

                  <p style={{ fontSize: '11px', color: '#4A4A46', opacity: .3, textAlign: 'center', fontStyle: 'italic', marginTop: '4px' }}>
                    Si no sabes tu hora exacta, usa una aproximada — el Ascendente puede variar, pero el resto de la carta seguirá siendo fiable.
                  </p>
                </div>
              </>
            )}

            {/* Estado: ya tiene carta -> mostrar resumen */}
            {birthChart && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
                  {[
                    { label: 'Sol', value: birthChart.planets?.find(p => p.name === 'sun')?.sign },
                    { label: 'Luna', value: birthChart.planets?.find(p => p.name === 'moon')?.sign },
                    { label: 'Ascendente', value: birthChart.ascendant?.sign },
                  ].map((item, i) => (
                    <div key={i} style={{ flex: '1 1 90px', background: 'rgba(107,158,160,.08)', border: '0.5px solid rgba(107,158,160,.18)', borderRadius: '14px', padding: '12px 8px', textAlign: 'center' }}>
                      <div style={{ fontSize: '10px', color: '#4A7B7E', opacity: .6, fontStyle: 'italic', marginBottom: '3px' }}>{item.label}</div>
                      <div style={{ fontSize: '14px', color: '#4A4A46', fontWeight: 500 }}>{item.value || '—'}</div>
                    </div>
                  ))}
                </div>

                <p style={{ fontSize: '12px', color: '#4A4A46', opacity: .45, fontStyle: 'italic', lineHeight: 1.6, textAlign: 'center', margin: '4px 0 0' }}>
                  {selectedPerson === 'yo'
                    ? 'Ya tengo tu carta natal guardada. Pregúntame cuando quieras por tus tránsitos actuales o lo que veas en tu cielo hoy.'
                    : `Ya tengo la carta natal de ${selectedPerson}. Pregúntame por su carta o sus tránsitos cuando quieras.`}
                </p>

                <div style={{ borderTop: '0.5px solid rgba(74,74,70,.1)', paddingTop: '14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button onClick={() => loadChartSvg('natal')} style={{ flex: 1, padding: '8px', borderRadius: '14px', border: chartViewMode === 'natal' && chartSvg ? '1px solid rgba(74,123,126,.5)' : '0.5px solid rgba(74,74,70,.2)', background: chartViewMode === 'natal' && chartSvg ? 'rgba(107,158,160,.1)' : 'white', color: '#4A7B7E', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit' }}>
                      Ver carta natal
                    </button>
                    <button onClick={() => loadChartSvg('transitos')} style={{ flex: 1, padding: '8px', borderRadius: '14px', border: chartViewMode === 'transitos' && chartSvg ? '1px solid rgba(160,105,58,.5)' : '0.5px solid rgba(74,74,70,.2)', background: chartViewMode === 'transitos' && chartSvg ? 'rgba(201,149,107,.1)' : 'white', color: '#A0693A', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit' }}>
                      Ver tránsitos
                    </button>
                  </div>

                  {chartViewMode === 'transitos' && (
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      <input type="date" value={transitDate} onChange={e => setTransitDate(e.target.value)}
                        style={{ flex: 1, padding: '8px 10px', border: '0.5px solid rgba(74,74,70,.2)', borderRadius: '12px', background: 'white', fontSize: '13px', outline: 'none', fontFamily: 'inherit', color: '#4A4A46' }} />
                      <button onClick={() => loadChartSvg('transitos', transitDate)} style={{ padding: '8px 14px', borderRadius: '12px', border: 'none', background: 'rgba(160,105,58,.88)', color: 'white', fontSize: '12px', cursor: 'pointer', fontFamily: 'inherit' }}>
                        Calcular
                      </button>
                    </div>
                  )}

                  {chartSvgLoading && (
                    <p style={{ fontSize: '12px', color: '#4A4A46', opacity: .4, fontStyle: 'italic', textAlign: 'center', padding: '12px 0' }}>Dibujando tu carta...</p>
                  )}

                  {chartSvgError && <p style={{ color: '#C96B6B', fontSize: '12px', margin: 0, textAlign: 'center' }}>{chartSvgError}</p>}

                  {chartSvg && !chartSvgLoading && (
                    <div style={{ background: 'white', borderRadius: '14px', border: '0.5px solid rgba(74,74,70,.12)', padding: '8px', display: 'flex', justifyContent: 'center' }}
                      dangerouslySetInnerHTML={{ __html: chartSvg }} />
                  )}
                </div>
              </div>
            )}

          </div>
        </div>
      )}
    </div>
  );
}
