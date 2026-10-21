import type { ReactNode } from 'react'


const LINK_PATTERN = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)|(https?:\/\/[^\s<>"']+|www\.[^\s<>"']+|[\w.+-]+@[\w.-]+\.[a-zа-яё]{2,}|(?:[a-zа-яё0-9](?:[a-zа-яё0-9-]{0,61}[a-zа-яё0-9])?\.)+(?:ru|рф|com|org|net|io|museum)(?:\/[^\s<>"']*)?|(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2})/giu

function trimTrailingPunctuation(value: string) {
  let link = value
  let trailing = ''
  while (/[.,!?;:»]$/.test(link)) {
    trailing = link.slice(-1) + trailing
    link = link.slice(0, -1)
  }
  while (link.endsWith(')') && (link.match(/\)/g)?.length ?? 0) > (link.match(/\(/g)?.length ?? 0)) {
    trailing = `)${trailing}`
    link = link.slice(0, -1)
  }
  return { link, trailing }
}

function linkTarget(value: string) {
  if (/^[\w.+-]+@/iu.test(value)) return `mailto:${value}`
  if (/^(?:\+7|8)[\s(.-]*\d/iu.test(value)) return `tel:${value.replace(/[^+\d]/g, '')}`
  if (/^https?:\/\//iu.test(value)) return value
  return `https://${value}`
}

export function RichText({ text }: { text: string }) {
  const parts: ReactNode[] = []
  let cursor = 0
  let index = 0

  for (const match of text.matchAll(LINK_PATTERN)) {
    const start = match.index
    if (start > cursor) parts.push(text.slice(cursor, start))

    const markdownLabel = match[1]
    const markdownUrl = match[2]
    const plainValue = match[3]
    const rawValue = markdownUrl ?? plainValue
    const { link, trailing } = trimTrailingPunctuation(rawValue)
    const href = linkTarget(link)
    const external = href.startsWith('http://') || href.startsWith('https://')
    parts.push(
      <a
        className="rich-link"
        href={href}
        key={`${start}-${index}`}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
      >
        {markdownLabel ?? link}
      </a>,
    )
    if (trailing) parts.push(trailing)
    cursor = start + match[0].length
    index += 1
  }

  if (cursor < text.length) parts.push(text.slice(cursor))
  return <>{parts}</>
}
