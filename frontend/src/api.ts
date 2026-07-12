import type { Attachment, Conversation, Message, Mode } from './types'

export const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765'

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init)
  if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`)
  return response.status === 204 ? (undefined as T) : response.json()
}

export const api = {
  conversations: () => json<Conversation[]>('/api/conversations'),
  create: () => json<Conversation>('/api/conversations', { method: 'POST' }),
  messages: (id: string) => json<Message[]>(`/api/conversations/${id}/messages`),
  remove: (id: string) => json<void>(`/api/conversations/${id}`, { method: 'DELETE' }),
  upload: async (file: File) => {
    const body = new FormData(); body.append('file', file)
    return json<Attachment>('/api/uploads', { method: 'POST', body })
  },
  stream: async (payload: { conversation_id?: string; message: string; attachment_ids: string[]; mode: Mode }, onEvent: (event: any) => void, signal?: AbortSignal) => {
    const response = await fetch(`${API}/api/chat/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal })
    if (!response.ok || !response.body) throw new Error(await response.text())
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { value, done } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n'); buffer = chunks.pop() || ''
      for (const chunk of chunks) if (chunk.startsWith('data: ')) onEvent(JSON.parse(chunk.slice(6)))
    }
  },
}

