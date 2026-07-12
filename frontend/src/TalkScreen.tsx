import { FormEvent, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Mic, RotateCcw, Send, Sparkles, Square, Video } from 'lucide-react'
import { API } from './api'

type AgentState = 'connecting' | 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'

function LinkedText({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/[^\s)]+)/g).map((part, index) =>
    part.startsWith('http')
      ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a>
      : part
  )}</>
}

export function TalkScreen({ onHome }: { onHome: () => void }) {
  const [state, setState] = useState<AgentState>('connecting')
  const [transcript, setTranscript] = useState('')
  const [response, setResponse] = useState('')
  const [status, setStatus] = useState('Connecting to your local companion…')
  const [error, setError] = useState('')
  const [videoUrl, setVideoUrl] = useState('')
  const [text, setText] = useState('')
  const socketRef = useRef<WebSocket | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    const endpoint = API.replace(/^http/, 'ws') + '/api/talk/ws'
    const socket = new WebSocket(endpoint); socketRef.current = socket
    socket.onopen = () => { setState('idle'); setStatus('Ready when you are') }
    socket.onmessage = event => {
      const data = JSON.parse(event.data)
      if (data.type === 'state') {
        if (!(data.value === 'idle' && audioRef.current && !audioRef.current.paused)) setState(data.value)
        if (data.value === 'thinking') setStatus('Gemma is thinking locally…')
      }
      if (data.type === 'status') setStatus(data.content)
      if (data.type === 'transcript') { setTranscript(data.content); setResponse('') }
      if (data.type === 'token') setResponse(value => value + data.content)
      if (data.type === 'text_complete') setResponse(data.content)
      if (data.type === 'animation_state') setStatus('Creating a visual explanation…')
      if (data.type === 'video_ready') setVideoUrl(API + data.url)
      if (data.type === 'audio_ready') {
        const audio = new Audio(API + data.url); audioRef.current = audio; setState('speaking')
        audio.onended = () => { setState('idle'); setStatus('Ready when you are') }
        audio.onerror = () => setError('The generated voice audio could not be played.')
        audio.play().catch(e => setError(`Audio playback failed: ${e.message}`))
      }
      if (data.type === 'media_warning') setError(data.message)
      if (data.type === 'error') { setError(data.message); setState('error') }
    }
    socket.onerror = () => { setError('Cannot connect to the Talk service. Is the backend running?'); setState('error') }
    socket.onclose = () => setState(value => value === 'error' ? value : 'connecting')
    return () => { recorderRef.current?.stop(); audioRef.current?.pause(); socket.close() }
  }, [])

  async function beginListening() {
    setError(''); setVideoUrl('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm'
      const recorder = new MediaRecorder(stream, { mimeType: mime }); recorderRef.current = recorder; chunksRef.current = []
      recorder.ondataavailable = event => { if (event.data.size) chunksRef.current.push(event.data) }
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop())
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType })
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(await blob.arrayBuffer())
          socketRef.current.send(JSON.stringify({ type: 'commit', mime: recorder.mimeType }))
          setState('thinking'); setStatus('Transcribing locally with Whisper…')
        }
      }
      recorder.start(); setState('listening'); setStatus('Listening… tap again when finished')
    } catch (e) { setError(`Microphone unavailable: ${(e as Error).message}`); setState('error') }
  }

  function stopListening() { recorderRef.current?.stop(); recorderRef.current = null }
  function submitText(event: FormEvent) {
    event.preventDefault(); const content = text.trim(); if (!content || socketRef.current?.readyState !== WebSocket.OPEN) return
    setText(''); setTranscript(content); setResponse(''); setError(''); setState('thinking')
    socketRef.current.send(JSON.stringify({ type: 'text', content }))
  }
  function reset() { setTranscript(''); setResponse(''); setVideoUrl(''); setError(''); socketRef.current?.send(JSON.stringify({ type: 'reset' })) }

  return <div className="talk-screen">
    <header className="talk-header"><button onClick={onHome}><ArrowLeft size={18}/> Home</button><div><Sparkles size={18}/><b>Talk with Gemma</b><span>Local voice companion</span></div><button onClick={reset}><RotateCcw size={16}/> Reset</button></header>
    <main className="talk-main">
      <section className="voice-stage">
        <div className={`voice-orbit state-${state}`}><div className="orbit-ring ring-one"/><div className="orbit-ring ring-two"/><div className="voice-core"><Sparkles size={42}/></div>{state === 'speaking' && <div className="sound-bars">{[1,2,3,4,5].map(x => <i key={x}/>)}</div>}</div>
        <div className="state-label">{state}</div><h1>{status}</h1>
        <button className={`talk-button ${state === 'listening' ? 'recording' : ''}`} disabled={!['idle','listening','error'].includes(state)} onClick={state === 'listening' ? stopListening : beginListening}>{state === 'listening' ? <Square size={21}/> : <Mic size={23}/>}<span>{state === 'listening' ? 'Finish' : 'Talk'}</span></button>
      </section>
      <section className="talk-dialogue">
        {(transcript || response) && <div className="voice-conversation">{transcript && <div className="voice-turn user-turn"><small>YOU SAID</small><p>{transcript}</p></div>}{response && <div className="voice-turn agent-turn"><small>GEMMA</small><p><LinkedText text={response}/></p></div>}</div>}
        {videoUrl && <div className="visual-player"><div><Video size={16}/> Visual explanation</div><video src={videoUrl} controls autoPlay/></div>}
        {error && <div className="talk-error">{error}</div>}
      </section>
    </main>
    <form className="talk-text" onSubmit={submitText}><input value={text} onChange={e => setText(e.target.value)} placeholder="Or type something to Gemma…" disabled={state === 'thinking'}/><button disabled={!text.trim() || state === 'thinking'}><Send size={17}/></button></form>
  </div>
}
