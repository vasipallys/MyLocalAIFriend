import { useState } from 'react'
import { App as ChatApp } from './App'
import { HomeScreen } from './HomeScreen'
import { TalkScreen } from './TalkScreen'

type Page = 'home' | 'chat' | 'talk'

export function DesktopApp() {
  const [page, setPage] = useState<Page>('home')
  if (page === 'chat') return <ChatApp onHome={() => setPage('home')} />
  if (page === 'talk') return <TalkScreen onHome={() => setPage('home')} />
  return <HomeScreen onChat={() => setPage('chat')} onTalk={() => setPage('talk')} />
}

