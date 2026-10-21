import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Admin } from './Admin'
import { ApiError, api, csrfHeaders } from './api'
import type { ChatMessage, FAQItem, PublicConfig } from './api'
import { RichText } from './RichText'
import './App.css'

type MascotState = 'idle' | 'thinking' | 'answered' | 'clarification' | 'no_answer' | 'error'

const suggestions = [
  'Кто может участвовать?',
  'Какие документы нужны?',
  'Кто может быть руководителем проекта?',
]

const mascotSrc = '/mascot/assistant-idle-v3.png'
const legacySeasonNotice = /^Важно:\s*материал относится к сезону 2025[–-]2026 и требует проверки актуальности\.\s*/i

function currentIsoTime() {
  return new Date().toISOString()
}

function createClientId() {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()

  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'))
  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex.slice(6, 8).join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10).join('')}`
}

function Mascot({ state }: { state: MascotState }) {
  return (
    <div className={`mascot mascot--${state}`} aria-hidden="true">
      <span className="mascot__signal"><i /><i /><i /></span>
      <img src={mascotSrc} alt="" />
    </div>
  )
}

function FeedbackIcon({ direction }: { direction: 'up' | 'down' }) {
  return (
    <svg className={direction === 'down' ? 'feedback-icon feedback-icon--down' : 'feedback-icon'} viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7.5 10.5 11 3.8c.5-1 2-.6 2 .5v4.2h4.7c1.2 0 2.1 1.1 1.8 2.3l-1.4 6.5c-.2.9-1 1.5-1.9 1.5H7.5m0-8.3v8.3H4.8c-.8 0-1.4-.6-1.4-1.4v-5.5c0-.8.6-1.4 1.4-1.4h2.7Z" />
    </svg>
  )
}

function LoginDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: (username: string) => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function login(event: FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const result = await api<{ username: string; csrf_token: string }>('/api/v1/admin/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      sessionStorage.setItem('cm_csrf', result.csrf_token)
      onSuccess(result.username)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Не удалось войти')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div className="login-dialog" role="dialog" aria-modal="true" aria-labelledby="login-title">
        <button type="button" className="dialog-close" onClick={onClose} aria-label="Закрыть">×</button>
        <h2 id="login-title">Вход в админ-панель</h2>
        <form onSubmit={(event) => void login(event)}>
          <label>Логин<input autoFocus value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" /></label>
          <label>Пароль<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button type="submit" className="primary-button" disabled={loading}>{loading ? 'Проверяю…' : 'Войти'}</button>
        </form>
      </div>
    </div>
  )
}

function PublicApp({ onAdmin, adminUsername }: { onAdmin: (username: string) => void; adminUsername: string | null }) {
  const searchParams = new URLSearchParams(window.location.search)
  const embedded = window.location.pathname === '/embed' || searchParams.get('embed') === '1'
  const widgetMode = embedded && searchParams.get('widget') === '1'
  const [config, setConfig] = useState<PublicConfig | null>(null)
  const [faqs, setFaqs] = useState<FAQItem[]>([])
  const [conversationId, setConversationId] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(true)
  const [thinking, setThinking] = useState(false)
  const [mascotState, setMascotState] = useState<MascotState>('idle')
  const [openFaq, setOpenFaq] = useState<string | null>(null)
  const [showLogin, setShowLogin] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const messageListRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(false)

  async function loadMessages(id: string) {
    const data = await api<{ items: ChatMessage[] }>(`/api/v1/conversations/${id}/messages`)
    setMessages(data.items)
  }

  useEffect(() => {
    async function bootstrap() {
      try {
        const [publicConfig, session, faqData] = await Promise.all([
          api<PublicConfig>('/api/v1/public/config'),
          api<{ conversation_id: string }>(embedded ? '/api/v1/session?embed=1' : '/api/v1/session'),
          api<{ items: FAQItem[] }>('/api/v1/faqs'),
        ])
        setConfig(publicConfig)
        setConversationId(session.conversation_id)
        setFaqs(faqData.items.filter((item) => item.featured).slice(0, 3))
        await loadMessages(session.conversation_id)
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : 'Не удалось запустить приложение')
        setMascotState('error')
      } finally {
        setLoading(false)
      }
    }
    void bootstrap()
  }, [])

  useEffect(() => {
    if (!shouldAutoScrollRef.current) return
    shouldAutoScrollRef.current = false
    const frame = window.requestAnimationFrame(() => {
      messageListRef.current?.scrollTo({ top: messageListRef.current.scrollHeight, behavior: 'smooth' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [messages, thinking])

  async function pollRequest(requestId: string): Promise<ChatMessage> {
    for (let attempt = 0; attempt < 90; attempt += 1) {
      const result = await api<{
        status: string
        result: string | null
        assistant_message: ChatMessage | null
        error: { message: string } | null
      }>(`/api/v1/chat-requests/${requestId}`)
      if (result.status === 'completed' && result.assistant_message) return result.assistant_message
      if (result.status === 'failed' || result.status === 'blocked') throw new Error(result.error?.message ?? 'Не удалось получить ответ')
      await new Promise((resolve) => window.setTimeout(resolve, 500))
    }
    throw new Error('Ответ занимает больше времени, чем ожидалось. Попробуйте ещё раз.')
  }

  async function sendQuestion(value: string) {
    const text = value.trim()
    if (!text || thinking || !conversationId) return
    shouldAutoScrollRef.current = true
    setQuestion('')
    setThinking(true)
    setError('')
    setMascotState('thinking')
    const optimistic: ChatMessage = {
      id: `local-${createClientId()}`,
      sequence: Number.MAX_SAFE_INTEGER,
      role: 'user',
      text,
      created_at: currentIsoTime(),
      result_status: null,
      request_id: null,
      sources: [],
      feedback: null,
    }
    setMessages((current) => [...current, optimistic])
    try {
      const clientId = createClientId()
      const accepted = await api<{ request_id: string }>(`/api/v1/conversations/${conversationId}/messages`, {
        method: 'POST',
        headers: { 'Idempotency-Key': clientId },
        body: JSON.stringify({ text, client_message_id: clientId }),
      })
      const answer = await pollRequest(accepted.request_id)
      shouldAutoScrollRef.current = true
      setMessages((current) => [...current.filter((message) => message.id !== optimistic.id), { ...optimistic, id: accepted.request_id }, answer])
      const state = answer.result_status === 'clarification' ? 'clarification' : answer.result_status === 'no_answer' || answer.result_status === 'off_topic' ? 'no_answer' : 'answered'
      setMascotState(state)
      window.setTimeout(() => setMascotState('idle'), 1500)
    } catch (reason) {
      setMessages((current) => current.filter((message) => message.id !== optimistic.id))
      setError(reason instanceof Error ? reason.message : 'Не удалось отправить вопрос')
      setMascotState('error')
    } finally {
      setThinking(false)
      inputRef.current?.focus()
    }
  }

  async function newConversation() {
    const created = await api<{ id: string }>('/api/v1/conversations', { method: 'POST' })
    setConversationId(created.id)
    setMessages([])
    setMascotState('idle')
    setError('')
  }

  async function feedback(messageId: string, value: 'helpful' | 'unhelpful') {
    await api(`/api/v1/messages/${messageId}/feedback`, { method: 'PUT', body: JSON.stringify({ value }) })
    setMessages((current) => current.map((message) => message.id === messageId ? { ...message, feedback: value } : message))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void sendQuestion(question)
  }

  return (
    <div className={`page-shell${embedded ? ' page-shell--embed' : ''}${widgetMode ? ' page-shell--widget' : ''}`}>
      {!embedded && <header className="site-header">
        <a className="brand" href="#top">Креативный музей</a>
        <div className="header-actions">
          {messages.length > 0 && <button className="new-chat-button" type="button" onClick={() => void newConversation()}>Новый диалог</button>}
          <button className="profile-button" type="button" aria-label="Вход администратора" onClick={() => adminUsername ? onAdmin(adminUsername) : setShowLogin(true)}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.2" /><path d="M5.8 19c.7-3.1 3-4.7 6.2-4.7s5.5 1.6 6.2 4.7" /></svg>
          </button>
        </div>
      </header>}

      <main id="top" className="public-main">
        {!widgetMode && <section className="hero-section" aria-labelledby="hero-title">
          <div><h1 id="hero-title">Помощник конкурса</h1><p className="subtitle">Ответы на вопросы об участии и заявке</p></div>
          <Mascot state={mascotState} />
          {embedded && messages.length > 0 && <button className="new-chat-button embed-new-chat" type="button" onClick={() => void newConversation()}>Новый диалог</button>}
        </section>}

        <section className="chat-card" aria-label="Чат с помощником">
          <div ref={messageListRef} className="message-list" aria-live="polite">
            {loading && <div className="empty-state"><strong>Загружаю материалы…</strong></div>}
            {!loading && messages.length === 0 && (
              <div className="empty-state"><strong>Задайте первый вопрос</strong><span>Найду ответ в материалах конкурса и покажу источник.</span><div className="suggestions">{suggestions.map((item) => <button type="button" key={item} onClick={() => void sendQuestion(item)}>{item}</button>)}</div></div>
            )}
            {messages.map((message) => (
              <div className={`message-row message-row--${message.role}`} key={message.id}>
                {message.role === 'assistant' && <span className="mini-mascot" aria-hidden="true"><img src={mascotSrc} alt="" /></span>}
                <div className={`message message--${message.role}`}>
                  <p><RichText text={message.role === 'assistant' ? message.text.replace(legacySeasonNotice, '') : message.text} /></p>
                  {message.sources.length > 0 && <details className="sources"><summary>Источники</summary>{message.sources.map((source) => <div key={source.id}><strong>{source.url ? <a className="rich-link" href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a> : <RichText text={source.title} />}</strong><span>{source.section}</span></div>)}</details>}
                  {message.role === 'assistant' && <div className="feedback-actions" aria-label="Оценка ответа"><button className={message.feedback === 'helpful' ? 'active' : ''} type="button" onClick={() => void feedback(message.id, 'helpful')} aria-label="Ответ полезен"><FeedbackIcon direction="up" /></button><button className={message.feedback === 'unhelpful' ? 'active' : ''} type="button" onClick={() => void feedback(message.id, 'unhelpful')} aria-label="Ответ не помог"><FeedbackIcon direction="down" /></button></div>}
                </div>
              </div>
            ))}
            {thinking && <div className="message-row message-row--assistant"><span className="mini-mascot" aria-hidden="true"><img src={mascotSrc} alt="" /></span><div className="message message--assistant thinking-text"><p>Ищу ответ в материалах конкурса</p></div></div>}
          </div>

          {error && <div className="chat-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError('')}>Закрыть</button></div>}
          <form className="composer" onSubmit={submit}>
            <textarea ref={inputRef} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void sendQuestion(question) } }} rows={1} maxLength={4000} placeholder="Ваш вопрос о конкурсе" aria-label="Ваш вопрос о конкурсе" />
            <button type="submit" disabled={!question.trim() || thinking || loading} aria-label="Отправить вопрос"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 8-16 8 3-8-3-8Zm3 8h13" /></svg></button>
          </form>
        </section>

        {!embedded && <section className="faq-section" aria-labelledby="faq-title"><h2 id="faq-title">Частые вопросы</h2><div className="faq-list">{faqs.map((faq) => { const isOpen = openFaq === faq.id; return <div className="faq-item" key={faq.id}><button type="button" aria-expanded={isOpen} aria-controls={`faq-${faq.id}`} onClick={() => setOpenFaq(isOpen ? null : faq.id)}><span>{faq.question}</span><span className="chevron" aria-hidden="true">⌄</span></button>{isOpen && <p id={`faq-${faq.id}`}><RichText text={faq.answer} /></p>}</div> })}</div></section>}
      </main>
      {!embedded && config && <footer className="site-footer"><a href={`mailto:${config.support_email}`}>{config.support_email}</a><a href={`tel:${config.support_phone.replace(/[^+\d]/g, '')}`}>{config.support_phone}</a></footer>}
      {showLogin && <LoginDialog onClose={() => setShowLogin(false)} onSuccess={(username) => { setShowLogin(false); onAdmin(username) }} />}
    </div>
  )
}

function App() {
  const [admin, setAdmin] = useState<string | null>(null)
  const [showAdmin, setShowAdmin] = useState(false)
  const [checkingAdmin, setCheckingAdmin] = useState(true)

  useEffect(() => {
    api<{ username: string; csrf_token: string }>('/api/v1/admin/auth/me')
      .then((result) => {
        sessionStorage.setItem('cm_csrf', result.csrf_token)
        setAdmin(result.username)
      })
      .catch((reason) => { if (!(reason instanceof ApiError) || reason.status !== 401) console.error(reason) })
      .finally(() => setCheckingAdmin(false))
  }, [])

  async function logout() {
    await api('/api/v1/admin/auth/logout', { method: 'POST', headers: csrfHeaders() })
    sessionStorage.removeItem('cm_csrf')
    setAdmin(null)
    setShowAdmin(false)
  }

  if (checkingAdmin) return <div className="app-loading">Загружаю приложение…</div>
  if (admin && showAdmin) return <Admin username={admin} onLogout={() => void logout()} onBack={() => setShowAdmin(false)} />
  return <PublicApp adminUsername={admin} onAdmin={(username) => { setAdmin(username); setShowAdmin(true) }} />
}

export default App
