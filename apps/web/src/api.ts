export type PublicConfig = {
  name: string
  demo: boolean
  season: string | null
  legacy_season: string
  support_email: string
  support_phone: string
}

export type FAQItem = {
  id: string
  question: string
  answer: string
  category: string
  season: string
  featured: boolean
  status?: string
  revision?: number
  display_order?: number
}

export type KnowledgeItem = {
  id: string
  title: string
  content: string
  layer: 'qa' | 'general'
  section: string
  season: string
  source_label: string
  status: string
  display_order: number
}

export type EscalationSetting = {
  value: string
  revision: number
}

export type Source = { id: string; title: string; section: string; url: string | null }

export type ChatMessage = {
  id: string
  sequence: number
  role: 'user' | 'assistant'
  text: string
  created_at: string
  result_status: string | null
  request_id: string | null
  sources: Source[]
  feedback: 'helpful' | 'unhelpful' | null
}

export type ConversationSummary = {
  id: string
  created_at: string
  last_message_at: string
  last_question: string
  message_count: number
  result: string | null
  feedback: string | null
  channel: string
}

type ApiErrorBody = { error?: { message?: string; code?: string } }

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, message: string, code = 'REQUEST_ERROR') {
    super(message)
    this.status = status
    this.code = code
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })
  if (!response.ok) {
    let body: ApiErrorBody = {}
    try { body = await response.json() as ApiErrorBody } catch { /* empty */ }
    throw new ApiError(response.status, body.error?.message ?? 'Ошибка запроса', body.error?.code)
  }
  return response.json() as Promise<T>
}

export function csrfHeaders(): HeadersInit {
  const token = sessionStorage.getItem('cm_csrf')
  return token ? { 'X-CSRF-Token': token } : {}
}
