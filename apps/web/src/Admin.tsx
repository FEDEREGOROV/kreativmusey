import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api, csrfHeaders } from './api'
import type { ChatMessage, ConversationSummary, EscalationSetting, FAQItem, KnowledgeItem } from './api'

type AdminTab = 'dialogs' | 'usage' | 'knowledge' | 'faqs'

type UsageSummary = {
  questions: number
  successful_answers: number
  api_calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  cached_tokens: number
  known_cost: string
  currency: string
  unknown_count: number
  model: string
  demo: boolean
}

type ConversationDetail = { id: string; channel: string; created_at: string; messages: ChatMessage[] }

function dateTime(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value))
}

function DialogsTab() {
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [selected, setSelected] = useState<ConversationDetail | null>(null)
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [exportLimit, setExportLimit] = useState('100')

  async function load() {
    setLoading(true)
    const data = await api<{ items: ConversationSummary[] }>(`/api/v1/admin/conversations?q=${encodeURIComponent(query)}`)
    setItems(data.items)
    setLoading(false)
  }

  // Initial remote data synchronization is intentionally effect-driven.
  // oxlint-disable-next-line react/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { void load() }, [])

  async function openConversation(id: string) {
    setSelected(await api<ConversationDetail>(`/api/v1/admin/conversations/${id}`))
  }

  async function createExport(format: 'csv' | 'json', limit: number | null) {
    const job = await api<{ id: string }>('/api/v1/admin/exports', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ format, mode: limit ? 'last' : 'all', ...(limit ? { limit } : {}) }),
    })
    window.location.href = `/api/v1/admin/exports/${job.id}/download`
  }

  return (
    <div className="admin-grid">
      <section className="panel dialog-list-panel">
        <div className="panel-heading">
          <div><p className="overline">История</p><h2>Диалоги</h2></div>
          <div className="export-menu">
            <select aria-label="Объём выгрузки" value={exportLimit} onChange={(event) => setExportLimit(event.target.value)}>
              <option value="100">Последние 100</option>
              <option value="1000">Последние 1 000</option>
              <option value="10000">Последние 10 000</option>
              <option value="all">Вся история</option>
            </select>
            <button type="button" className="secondary-button" onClick={() => void createExport('csv', exportLimit === 'all' ? null : Number(exportLimit))}>CSV</button>
            <button type="button" className="secondary-button" onClick={() => void createExport('json', exportLimit === 'all' ? null : Number(exportLimit))}>JSON</button>
          </div>
        </div>
        <form className="search-row" onSubmit={(event) => { event.preventDefault(); void load() }}>
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по сообщениям" />
          <button type="submit" className="primary-button">Найти</button>
        </form>
        <div className="admin-table" role="table">
          <div className="admin-table__head" role="row"><span>Время</span><span>Последний вопрос</span><span>Сообщения</span><span>Результат</span></div>
          {loading && <p className="muted">Загрузка…</p>}
          {!loading && items.length === 0 && <p className="muted empty-table">Диалогов пока нет.</p>}
          {items.map((item) => (
            <button key={item.id} type="button" className="admin-table__row" onClick={() => void openConversation(item.id)}>
              <span>{dateTime(item.last_message_at)}</span>
              <strong>{item.last_question || 'Без вопроса'}</strong>
              <span>{item.message_count}</span>
              <span className={`status-pill status-pill--${item.result ?? 'new'}`}>{item.result ?? 'новый'}</span>
            </button>
          ))}
        </div>
      </section>

      <aside className="panel conversation-detail">
        {!selected && <div className="detail-empty"><span>↗</span><strong>Выберите диалог</strong><p>Здесь появится полная история и источники ответов.</p></div>}
        {selected && <>
          <div className="panel-heading"><div><p className="overline">{selected.channel}</p><h3>История беседы</h3></div></div>
          <div className="detail-messages">
            {selected.messages.map((message) => (
              <article key={message.id} className={`detail-message detail-message--${message.role}`}>
                <span>{message.role === 'user' ? 'Посетитель' : 'Помощник'} · {dateTime(message.created_at)}</span>
                <p>{message.text}</p>
                {message.sources.length > 0 && <small>Источники: {message.sources.map((source) => source.title).join(', ')}</small>}
              </article>
            ))}
          </div>
        </>}
      </aside>
    </div>
  )
}

function UsageTab() {
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  useEffect(() => { void api<UsageSummary>('/api/v1/admin/usage/summary').then(setUsage) }, [])
  if (!usage) return <section className="panel"><p className="muted">Загрузка расходов…</p></section>
  const cards = [
    ['Вопросы', usage.questions],
    ['Успешные ответы', usage.successful_answers],
    ['API-вызовы', usage.api_calls],
    ['Ошибки', usage.errors],
    ['Входные токены', usage.input_tokens],
    ['Выходные токены', usage.output_tokens],
  ]
  return (
    <section className="panel usage-panel">
      <div className="panel-heading"><div><p className="overline">Учёт провайдера</p><h2>Расход токенов</h2></div><span className="mode-badge">{usage.demo ? 'Без расходов' : usage.model}</span></div>
      <div className="metric-grid">
        {cards.map(([label, value]) => <div className="metric" key={label}><span>{label}</span><strong>{Number(value).toLocaleString('ru-RU')}</strong></div>)}
        <div className="metric metric--accent"><span>Известная стоимость</span><strong>${usage.known_cost}</strong></div>
        <div className="metric"><span>Неизвестный расход</span><strong>{usage.unknown_count}</strong></div>
      </div>
      <div className="usage-note">
        <strong>{usage.demo ? 'Вызовы модели не выполняются' : 'Данные получены из usage API'}</strong>
        <p>Поиск по базе знаний учитывается как нулевой расход. После подключения модели каждый вызов будет записан отдельно.</p>
      </div>
    </section>
  )
}

const emptyDraft: FAQItem = { id: '', question: '', answer: '', category: 'Общее', season: '2025-2026', featured: false, status: 'draft', revision: 0 }

function FaqsTab() {
  const [items, setItems] = useState<FAQItem[]>([])
  const [editing, setEditing] = useState<FAQItem>(emptyDraft)
  const [notice, setNotice] = useState('')

  async function load() {
    const data = await api<{ items: FAQItem[] }>('/api/v1/admin/faqs')
    setItems(data.items)
  }
  // Initial remote data synchronization is intentionally effect-driven.
  // oxlint-disable-next-line react/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { void load() }, [])

  async function save(event: FormEvent) {
    event.preventDefault()
    const body = {
      question: editing.question,
      answer: editing.answer,
      category: editing.category,
      season: editing.season,
      featured: editing.featured,
      expected_revision: editing.id ? editing.revision : null,
    }
    const result = editing.id
      ? await api<{ id: string; revision: number; status: string }>(`/api/v1/admin/faqs/${editing.id}`, { method: 'PATCH', headers: csrfHeaders(), body: JSON.stringify(body) })
      : await api<{ id: string; revision: number; status: string }>('/api/v1/admin/faqs', { method: 'POST', headers: csrfHeaders(), body: JSON.stringify(body) })
    setNotice('Черновик сохранён')
    setEditing({ ...editing, ...result })
    await load()
  }

  async function changeStatus(action: 'publish' | 'hide') {
    if (!editing.id) return
    const result = await api<{ revision: number; status: string }>(`/api/v1/admin/faqs/${editing.id}/${action}`, { method: 'POST', headers: csrfHeaders() })
    setEditing({ ...editing, ...result })
    setNotice(action === 'publish' ? 'FAQ опубликован' : 'FAQ скрыт')
    await load()
  }

  async function moveFaq(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= items.length) return
    const reordered = [...items]
    const [moved] = reordered.splice(index, 1)
    reordered.splice(target, 0, moved)
    setItems(reordered)
    await api('/api/v1/admin/faqs/order', {
      method: 'PUT',
      headers: csrfHeaders(),
      body: JSON.stringify({ ids: reordered.map((item) => item.id) }),
    })
  }

  return (
    <div className="admin-grid admin-grid--faq">
      <section className="panel faq-admin-list">
        <div className="panel-heading"><div><p className="overline">Публичная база</p><h2>Частые вопросы</h2></div><button className="primary-button" type="button" onClick={() => setEditing(emptyDraft)}>Новый FAQ</button></div>
        <div className="faq-admin-items">
          {items.map((item, index) => (
            <div className={`faq-admin-row ${editing.id === item.id ? 'selected' : ''}`} key={item.id}>
              <button type="button" className="faq-select" onClick={() => { setEditing(item); setNotice('') }}>
                <span><strong>{item.question}</strong><small>{item.category} · ред. {item.revision}</small></span>
                <em className={`status-pill status-pill--${item.status}`}>{item.status}</em>
              </button>
              <div className="order-buttons"><button type="button" disabled={index === 0} onClick={() => void moveFaq(index, -1)} aria-label="Поднять выше">↑</button><button type="button" disabled={index === items.length - 1} onClick={() => void moveFaq(index, 1)} aria-label="Опустить ниже">↓</button></div>
            </div>
          ))}
        </div>
      </section>
      <form className="panel faq-editor" onSubmit={(event) => void save(event)}>
        <div className="panel-heading"><div><p className="overline">Редактор</p><h3>{editing.id ? 'Изменить FAQ' : 'Новый FAQ'}</h3></div>{notice && <span className="save-notice">{notice}</span>}</div>
        <label>Вопрос<input value={editing.question} onChange={(event) => setEditing({ ...editing, question: event.target.value })} required /></label>
        <label>Ответ<textarea value={editing.answer} onChange={(event) => setEditing({ ...editing, answer: event.target.value })} rows={8} required /></label>
        <div className="field-row"><label>Категория<input value={editing.category} onChange={(event) => setEditing({ ...editing, category: event.target.value })} /></label><label>Сезон<input value={editing.season} onChange={(event) => setEditing({ ...editing, season: event.target.value })} /></label></div>
        <label className="checkbox-row"><input type="checkbox" checked={editing.featured} onChange={(event) => setEditing({ ...editing, featured: event.target.checked })} />Показывать на главной</label>
        <div className="editor-actions"><button className="primary-button" type="submit">Сохранить черновик</button>{editing.id && <><button className="secondary-button" type="button" onClick={() => void changeStatus('publish')}>Опубликовать</button><button className="text-button" type="button" onClick={() => void changeStatus('hide')}>Скрыть</button></>}</div>
      </form>
    </div>
  )
}

const newQaDraft = { title: '', content: '' }

function QaSheetRow({ item, onSaved }: { item: KnowledgeItem; onSaved: (item: KnowledgeItem) => void }) {
  const [question, setQuestion] = useState(item.title)
  const [answer, setAnswer] = useState(item.content)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const changed = question !== item.title || answer !== item.content

  async function save() {
    if (!question.trim() || !answer.trim() || saving) return
    setSaving(true)
    const result = await api<KnowledgeItem>(`/api/v1/admin/knowledge/${item.id}`, {
      method: 'PATCH',
      headers: csrfHeaders(),
      body: JSON.stringify({ title: question, content: answer, layer: 'qa', section: item.section, season: item.season }),
    })
    onSaved(result)
    setSaving(false)
    setSaved(true)
    window.setTimeout(() => setSaved(false), 1400)
  }

  async function toggleStatus() {
    const action = item.status === 'hidden' ? 'activate' : 'hide'
    const result = await api<KnowledgeItem>(`/api/v1/admin/knowledge/${item.id}/${action}`, { method: 'POST', headers: csrfHeaders() })
    onSaved(result)
  }

  return (
    <div className={`qa-sheet-row ${item.status === 'hidden' ? 'is-hidden' : ''}`}>
      <textarea aria-label="Вопрос" rows={3} value={question} onChange={(event) => setQuestion(event.target.value)} />
      <div className="qa-answer-cell">
        <textarea aria-label="Ответ" rows={5} value={answer} onChange={(event) => setAnswer(event.target.value)} />
        <div className="qa-row-actions">
          <span className={`status-pill status-pill--${item.status}`}>{item.status === 'hidden' ? 'скрыт' : 'активен'}</span>
          {saved && <span className="inline-saved">Сохранено</span>}
          <button type="button" className="text-button" disabled={!changed || saving} onClick={() => void save()}>{saving ? 'Сохраняю…' : 'Сохранить'}</button>
          <button type="button" className="text-button text-button--muted" onClick={() => void toggleStatus()}>{item.status === 'hidden' ? 'Вернуть' : 'Скрыть'}</button>
        </div>
      </div>
    </div>
  )
}

function KnowledgeTab() {
  const [layer, setLayer] = useState<'qa' | 'general'>('qa')
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [query, setQuery] = useState('')
  const [notice, setNotice] = useState('')
  const [newQa, setNewQa] = useState(newQaDraft)
  const [showNewQa, setShowNewQa] = useState(false)
  const [escalation, setEscalation] = useState<EscalationSetting>({ value: '', revision: 0 })
  const [generalText, setGeneralText] = useState<EscalationSetting>({ value: '', revision: 0 })

  async function loadQa(search = query) {
    const data = await api<{ items: KnowledgeItem[]; count: number }>(`/api/v1/admin/knowledge?layer=qa&q=${encodeURIComponent(search)}`)
    setItems(data.items)
  }

  async function loadEscalation() {
    setEscalation(await api<EscalationSetting>('/api/v1/admin/knowledge/settings/escalation'))
  }

  async function loadGeneralText() {
    setGeneralText(await api<EscalationSetting>('/api/v1/admin/knowledge/general-text'))
  }

  // Initial remote data synchronization is intentionally effect-driven.
  // oxlint-disable-next-line react/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { void Promise.all([loadQa(''), loadEscalation(), loadGeneralText()]) }, [])

  function switchLayer(nextLayer: 'qa' | 'general') {
    setLayer(nextLayer)
    setQuery('')
    setNotice('')
  }

  async function createQa(event: FormEvent) {
    event.preventDefault()
    const result = await api<KnowledgeItem>('/api/v1/admin/knowledge', {
      method: 'POST',
      headers: csrfHeaders(),
      body: JSON.stringify({ title: newQa.title, content: newQa.content, layer: 'qa', section: 'Вопросы и ответы', season: '2025-2026' }),
    })
    setItems((current) => [...current, result])
    setNewQa(newQaDraft)
    setShowNewQa(false)
    setNotice('Новая пара добавлена')
  }

  function updateQa(updated: KnowledgeItem) {
    setItems((current) => current.map((item) => item.id === updated.id ? updated : item))
  }

  async function saveGeneralText(event: FormEvent) {
    event.preventDefault()
    const result = await api<EscalationSetting>('/api/v1/admin/knowledge/general-text', {
      method: 'PUT',
      headers: csrfHeaders(),
      body: JSON.stringify({ value: generalText.value, expected_revision: generalText.revision }),
    })
    setGeneralText(result)
    setNotice('Общая база сохранена и переиндексирована')
  }

  async function saveEscalation(event: FormEvent) {
    event.preventDefault()
    const result = await api<EscalationSetting>('/api/v1/admin/knowledge/settings/escalation', {
      method: 'PUT',
      headers: csrfHeaders(),
      body: JSON.stringify({ value: escalation.value, expected_revision: escalation.revision }),
    })
    setEscalation(result)
    setNotice('Ответ оператора сохранён')
  }

  return (
    <div className="knowledge-layout">
      <section className="panel knowledge-workspace">
        <div className="panel-heading">
          <div><p className="overline">Приоритет поиска</p><h2>База знаний</h2></div>
          {notice && <span className="save-notice">{notice}</span>}
        </div>
        <div className="knowledge-layer-tabs" role="tablist" aria-label="Слои базы знаний">
          <button type="button" className={layer === 'qa' ? 'active' : ''} onClick={() => switchLayer('qa')}>1. Вопрос → ответ</button>
          <button type="button" className={layer === 'general' ? 'active' : ''} onClick={() => switchLayer('general')}>2. Общая база</button>
        </div>
        {layer === 'qa' && <>
          <div className="knowledge-toolbar">
            <form className="search-row" onSubmit={(event) => { event.preventDefault(); void loadQa() }}>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Поиск по вопросам и ответам" />
              <button className="secondary-button" type="submit">Найти</button>
            </form>
            <div className="qa-toolbar-actions"><span>{items.length.toLocaleString('ru-RU')} строк</span><button className="secondary-button" type="button" onClick={() => setShowNewQa((current) => !current)}>{showNewQa ? 'Отмена' : '+ Добавить строку'}</button></div>
          </div>
          <div className="qa-sheet" role="table" aria-label="Вопросы и ответы">
            <div className="qa-sheet-head" role="row"><strong>Вопрос</strong><strong>Ответ</strong></div>
            {showNewQa && <form className="qa-sheet-row qa-sheet-row--new" onSubmit={(event) => void createQa(event)}>
              <textarea aria-label="Новый вопрос" rows={3} placeholder="Новый вопрос" value={newQa.title} onChange={(event) => setNewQa({ ...newQa, title: event.target.value })} required />
              <div className="qa-answer-cell"><textarea aria-label="Новый ответ" rows={5} placeholder="Готовый ответ" value={newQa.content} onChange={(event) => setNewQa({ ...newQa, content: event.target.value })} required /><div className="qa-row-actions"><button className="primary-button" type="submit">Сохранить строку</button></div></div>
            </form>}
            {items.map((item) => <QaSheetRow item={item} key={item.id} onSaved={updateQa} />)}
          </div>
        </>}

        {layer === 'general' && <form className="general-text-editor" onSubmit={(event) => void saveGeneralText(event)}>
          <div><h3>Общая база одним текстом</h3><p className="muted">Редактируйте исходный текст целиком. После сохранения система сама заново подготовит поисковые фрагменты.</p></div>
          <textarea aria-label="Общая база знаний" value={generalText.value} onChange={(event) => setGeneralText({ ...generalText, value: event.target.value })} required />
          <div className="editor-actions"><button className="primary-button" type="submit">Сохранить общую базу</button><span className="muted">Редакция {generalText.revision}</span></div>
        </form>}
      </section>

      <form className="panel escalation-editor" onSubmit={(event) => void saveEscalation(event)}>
        <div className="panel-heading"><div><p className="overline">Если ответа нет или вопрос не по теме</p><h3>Передача вопроса оператору</h3></div></div>
        <p className="muted">Этот текст бот отправит дословно, когда не найдёт уверенного ответа ни в одном из двух уровней базы.</p>
        <label>Ответ пользователю<textarea value={escalation.value} onChange={(event) => setEscalation({ ...escalation, value: event.target.value })} rows={5} required /></label>
        <div className="editor-actions"><button className="primary-button" type="submit">Сохранить текст</button><span className="muted">Редакция {escalation.revision}</span></div>
      </form>
    </div>
  )
}

export function Admin({ username, onLogout, onBack }: { username: string; onLogout: () => void; onBack: () => void }) {
  const [tab, setTab] = useState<AdminTab>('dialogs')
  return (
    <div className="admin-shell">
      <header className="admin-header">
        <a href="/" className="brand">Креативный музей</a>
        <div><span className="admin-user">{username}</span><button type="button" className="text-button" onClick={onLogout}>Выйти</button></div>
      </header>
      <main className="admin-main">
        <div className="admin-title"><h1>Админ-панель</h1><button type="button" className="secondary-button" onClick={onBack}>← На сайт</button></div>
        <nav className="admin-tabs" aria-label="Разделы администратора">
          <button type="button" className={tab === 'dialogs' ? 'active' : ''} onClick={() => setTab('dialogs')}>Диалоги</button>
          <button type="button" className={tab === 'usage' ? 'active' : ''} onClick={() => setTab('usage')}>Расход токенов</button>
          <button type="button" className={tab === 'knowledge' ? 'active' : ''} onClick={() => setTab('knowledge')}>База знаний</button>
          <button type="button" className={tab === 'faqs' ? 'active' : ''} onClick={() => setTab('faqs')}>Частые вопросы</button>
        </nav>
        {tab === 'dialogs' && <DialogsTab />}
        {tab === 'usage' && <UsageTab />}
        {tab === 'knowledge' && <KnowledgeTab />}
        {tab === 'faqs' && <FaqsTab />}
      </main>
    </div>
  )
}
