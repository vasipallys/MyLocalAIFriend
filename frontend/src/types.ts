export type Role = 'user' | 'assistant' | 'system' | 'tool'
export type Mode = 'auto' | 'chat' | 'code' | 'research' | 'image' | 'document'
export interface Attachment { id: string; name: string; content_type: string; size: number }
export interface Message { id: string; role: Role; content: string; created_at: string; attachments?: Attachment[]; metadata?: Record<string, unknown> }
export interface Conversation { id: string; title: string; created_at: string; updated_at: string }

