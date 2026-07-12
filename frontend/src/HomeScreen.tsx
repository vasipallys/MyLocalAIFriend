import { ArrowRight, LockKeyhole, MessageSquare, Mic2, Sparkles } from 'lucide-react'

export function HomeScreen({ onChat, onTalk }: { onChat: () => void; onTalk: () => void }) {
  return <div className="home-screen">
    <div className="home-glow" />
    <header className="home-header"><div className="brand-mark"><Sparkles size={18}/></div><b>Gemma Studio</b><span><LockKeyhole size={13}/> Private & local</span></header>
    <main className="home-content">
      <div className="home-kicker">YOUR LOCAL AI COMPANION</div>
      <h1>How would you like<br/>to connect?</h1>
      <p>Work deeply in chat, or have a natural voice conversation with your local Gemma agent.</p>
      <div className="choice-grid">
        <button className="choice-card chat-choice" onClick={onChat}><div className="choice-icon"><MessageSquare size={28}/></div><div><small>FOCUS & CREATE</small><h2>Chat</h2><p>Write code, analyze documents, research the web, and build ideas.</p></div><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card talk-choice" onClick={onTalk}><div className="choice-icon"><Mic2 size={28}/></div><div><small>SPEAK & EXPLORE</small><h2>Talk</h2><p>Have a hands-free conversation with voice, audio, and visual explanations.</p></div><ArrowRight className="choice-arrow"/></button>
      </div>
    </main>
    <footer className="home-footer">Gemma runs on this machine · Your conversations stay private</footer>
  </div>
}

