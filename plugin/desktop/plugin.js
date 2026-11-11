import { host, SandboxedFrame, TRANSCRIPT_DIRECTIVE_AREA, PALETTE_AREA, useValue, atom } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'mtg-deck-studio'
const PANE_ID = PLUGIN_ID + ':table'
const WINDOW_ID = Math.random().toString(36).slice(2) + Date.now().toString(36)

let CTX = null
const frame = { el: null, ready: false, queue: [] }
const loaded = { deckId: null, rev: 0 }
const viewer = { version: null, dev: false, reload: null }
const waiters = new Map()
const $ui = atom({ ready: false, deckId: null, title: '', rev: 0, cards: 0, selected: [], last: null, menu: false, decks: [], files: [], busy: '', error: '', camera: null, inner: null, frame: null, dev: false })

const ui = patch => $ui.set({ ...$ui.get(), ...patch })
const read = a => { try { return a?.get?.() ?? null } catch { return null } }
const focusedSession = () => read(host.state.focusedStoredSessionId) || read(host.state.activeSessionId)
const win = () => frame.el?.contentWindow || null
const rpc = (method, params = {}, timeoutMs = 30000) => CTX.rest('/rpc', { method: 'POST', body: { method, params }, timeoutMs })

function post(message) {
  if (win() && frame.ready) win().postMessage(message, '*')
  else frame.queue.push(message)
}

function localCommand(op, args = {}, kind = 'view') {
  post({ type: 'wb.cmd', command_id: 'local-' + Math.random().toString(36).slice(2), op, args, meta: { kind } })
}

function waitFor(type, deckId, ms) {
  return new Promise(resolve => {
    const key = type + ':' + deckId
    const timer = setTimeout(() => { waiters.delete(key); resolve(false) }, ms)
    waiters.set(key, () => { clearTimeout(timer); waiters.delete(key); resolve(true) })
  })
}

async function showDeck(deckId) {
  const data = deckId ? await rpc('load_deck', { deck_id: deckId }) : null
  if (!data || !data.document) return false
  const done = waitFor('wb.loaded', data.deck_id, 15000)
  post({ type: 'wb.load', deck_id: data.deck_id, rev: data.rev, document: data.document, links: data.links })
  const ok = await done
  if (ok) await sendGuide(data.deck_id, false)
  return ok
}

// Closing the deck is a real state, not a reload: the studio shows "No deck open" until another one
// is opened. The frame is told to load an empty bundle so nothing from the old deck stays on screen.
async function closeDeck() {
  const sid = focusedSession()
  await rpc('close_deck', { session_id: sid }).catch(() => null)
  loaded.deckId = null
  lastSession = sid
  post({ type: 'wb.load', deck_id: null, rev: 0, links: { cards: {}, stats: {} },
         document: { app: 'mtg-table-viewer', cards: [], deckTitle: '', strategyNotes: '' } })
  ui({ deckId: null, title: '', rev: 0, cards: 0, selected: [], last: null, error: '' })
}

async function sendGuide(deckId, reveal) {
  const state = await rpc('guide', { deck_id: deckId }).catch(() => ({ guide: null }))
  post({ type: 'wb.guide', deck_id: deckId, guide: state.guide || null, reveal })
}

function onGuide(event) {
  const p = event?.payload || {}
  if (p.deck_id && p.deck_id === loaded.deckId) sendGuide(p.deck_id, !!p.reveal)
}

let lastSession = null
async function syncSession() {
  const sid = focusedSession()
  if (!sid || sid === lastSession || !frame.ready) return
  lastSession = sid
  const bound = await rpc('session_deck', { session_id: sid })
  if (bound.deck_id && bound.deck_id !== loaded.deckId) {
    // A failed load must be retried by the next poll, not remembered as done.
    if (!(await showDeck(bound.deck_id))) lastSession = null
  } else if (!bound.deck_id && loaded.deckId) {
    await rpc('bind', { session_id: sid, deck_id: loaded.deckId })
  }
}

async function onCommand(event) {
  const env = event?.payload || {}
  if (!env.command_id || env.session_id !== focusedSession()) return
  const reply = { command_id: env.command_id, window_id: WINDOW_ID, deck_id: env.deck_id }
  if (!win() || !frame.ready) {
    await rpc('ack', { ...reply, outcome: 'pane_closed', error: 'The MTG Deck Studio pane is closed in this window.', rev: 0 })
    return
  }
  if (env.op === 'close_deck') {
    await closeDeck()
    await rpc('ack', { ...reply, outcome: 'applied', rev: 0, result: { deck_id: null } })
    return
  }
  if (env.op === 'open_deck') {
    const ok = await showDeck(env.deck_id)
    lastSession = env.session_id
    await rpc('ack', { ...reply, outcome: ok ? 'applied' : 'failed', rev: loaded.rev, result: { deck_id: env.deck_id } })
    return
  }
  if (env.deck_id !== loaded.deckId) {
    await rpc('ack', { ...reply, outcome: 'deck_mismatch', error: 'The studio shows a different deck than this chat is bound to.', rev: loaded.rev })
    return
  }
  ui({ busy: env.op })
  post({ type: 'wb.cmd', command_id: env.command_id, deck_id: env.deck_id, op: env.op, args: env.args || {}, expected_rev: env.expected_rev, meta: env.meta || {} })
}

async function refreshSideboard() {
  if (!loaded.deckId) return
  try {
    const data = await rpc('sideboard_list', { deck_id: loaded.deckId })
    post({ type: 'wb.sideboard', deck_id: loaded.deckId, cards: data.cards || [] })
  } catch (e) {
    /* the backend is unreachable; the tab shows its empty state */
  }
}

let linksRefresh = 0

// The graph is derived from the stored cards and their facts, so it is rebuilt whenever the deck
// actually changes: after a persist, after a sideboard swap in/out, and when a candidate is
// classified. Read-only navigation never touches it.
async function refreshLinks() {
  if (!loaded.deckId) return
  const token = ++linksRefresh
  try {
    const links = await rpc('links_for', { deck_id: loaded.deckId })
    if (token !== linksRefresh) return
    if (links && links.cards) post({ type: 'wb.graph', deck_id: loaded.deckId, links })
  } catch (e) {
    /* keep the graph we already have rather than blanking the view */
  }
}

async function persistResult(response, rev) {
  if (!response) return
  const status = response.saved || response.status
  if (status) post({ type: 'wb.saved', status, rev })
  if (status === 'saved') refreshLinks()
  if (status === 'conflict') {
    ui({ error: 'This deck changed elsewhere; reloaded the stored version.' })
    await showDeck(loaded.deckId)
  }
}

async function onFrameMessage(event) {
  const m = event.data || {}
  if (typeof m.type !== 'string' || !m.type.startsWith('wb.')) return
  if (event.source && frame.el && event.source !== win() && m.type !== 'wb.ready') return
  if (m.type === 'wb.ready') {
    frame.ready = true
    frame.queue.splice(0).forEach(x => win().postMessage(x, '*'))
    ui({ ready: true })
    lastSession = null
    syncSession()
  } else if (m.type === 'wb.loaded') {
    loaded.deckId = m.deck_id
    loaded.rev = m.rev
    ui({ deckId: m.deck_id, rev: m.rev, error: '' })
    refreshSideboard()
    waiters.get('wb.loaded:' + m.deck_id)?.()
  } else if (m.type === 'wb.ack') {
    if (typeof m.rev === 'number') loaded.rev = m.rev
    ui({ busy: '', rev: loaded.rev, last: { op: m.op || m.result?.op, outcome: m.outcome, error: m.error, rev: m.rev, at: Date.now() } })
    const response = await rpc('ack', { ...m, window_id: WINDOW_ID }).catch(e => ({ error: String(e) }))
    if (m.outcome === 'applied' && m.op !== 'flush') refreshSideboard()
    if (m.snapshot) await persistResult(response, m.rev)
  } else if (m.type === 'wb.changed') {
    loaded.rev = m.rev
    ui({ rev: m.rev })
    const response = await rpc('persist', { deck_id: m.deck_id, base_rev: m.base_rev, rev: m.rev, snapshot: m.snapshot }).catch(e => ({ status: 'error', error: String(e) }))
    await persistResult(response, m.rev)
  } else if (m.type === 'wb.file.save') {
    // The studio folder always holds the file; "copy" additionally mirrors it where the
    // reader points. Overwriting is the default: no more "(2)", "(3)" copies.
    let copyTo = null
    let cancelled = false
    if (m.target === 'copy') {
      const picked = await CTX.files.requestFolder().catch(e => ({ ok: false, error: String(e) }))
      if (!picked || !picked.ok || !picked.folder) cancelled = true
      else copyTo = picked.folder.path
    }
    if (cancelled) {
      post({ type: 'wb.file.saved', request_id: m.request_id, ok: false, cancelled: true })
    } else {
      const saved = await rpc('export_file', { name: m.name, text: m.text, copy_to: copyTo, overwrite: true }, 120000)
        .catch(e => ({ ok: false, error: String(e) }))
      post({ type: 'wb.file.saved', request_id: m.request_id, ok: !!saved.ok,
             path: saved.copied_to ? `${saved.path} · copy: ${saved.copied_to}` : saved.path, error: saved.error })
    }
  } else if (m.type === 'wb.frame') {
    ui({ frame: m })
  } else if (m.type === 'wb.sideboardAction') {
    const sid = focusedSession()
    ui({ busy: 'sideboard' })
    try {
      if (m.action === 'remove') {
        await rpc('sideboard_remove', { deck_id: loaded.deckId, scryfall_ids: [m.scryfall_id] })
      } else if (m.action === 'promote') {
        await rpc('sideboard_promote', { deck_id: loaded.deckId, scryfall_id: m.scryfall_id, session_id: sid }, 60000)
      } else if (m.action === 'replace') {
        await rpc('sideboard_promote', { deck_id: loaded.deckId, scryfall_id: m.scryfall_id, replace_id: m.replace_id, session_id: sid }, 60000)
      }
    } catch (e) {
      ui({ error: String(e) })
    }
    ui({ busy: '' })
    await refreshSideboard()
    refreshLinks()
  } else if (m.type === 'wb.candidateFacts') {
    try {
      const out = await rpc('sideboard_classify', { deck_id: loaded.deckId, cards: m.cards || {} })
      if (out && out.links) post({ type: 'wb.graph', deck_id: loaded.deckId, links: out.links })
    } catch (e) {
      ui({ error: String(e) })
    }
  } else if (m.type === 'wb.camera.open') {
    // The viewer is a sandboxed data: URL frame: it cannot open the camera itself, so the
    // request crosses into the pane host, which runs on the app's own origin.
    if (!loaded.deckId) { ui({ error: 'Open a deck before scanning a card.' }); return }
    ui({ camera: { target: m.target === 'sideboard' ? 'sideboard' : 'deck' } })
  } else if (m.type === 'wb.sideboardAdd') {
    ui({ busy: 'sideboard' })
    try {
      const out = await rpc('sideboard_add', { deck_id: loaded.deckId, refs: m.refs || [], reason: m.reason })
      const added = (out.added || []).join(', ')
      post({ type: 'wb.sideboardNotice', ok: true, message: added ? `Added ${added} to the sideboard.` : 'That card is already in the sideboard.' })
    } catch (e) {
      post({ type: 'wb.sideboardNotice', ok: false, message: String(e?.message || e) })
    }
    ui({ busy: '' })
    await refreshSideboard()
    refreshLinks()
  } else if (m.type === 'wb.context') {
    ui({ title: m.title, cards: m.card_count, selected: m.selected || [] })
    const sid = focusedSession()
    if (sid) {
      rpc('context', { ...m, session_id: sid, window_id: WINDOW_ID })
        .then(r => { if (r?.viewer_version && viewer.version && r.viewer_version !== viewer.version) viewer.reload?.() })
        .catch(() => {})
    }
  }
}

function toDataUrl(html) {
  const bytes = new TextEncoder().encode(html)
  let bin = ''
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
  return 'data:text/html;base64,' + btoa(bin)
}

const BTN = 'rounded border border-(--ui-stroke-secondary) px-2 py-0.5 text-xs hover:bg-(--ui-bg-tertiary) disabled:opacity-50'

async function openDeckFromLibrary(deckId) {
  ui({ menu: false })
  const sid = focusedSession()
  if (sid) await rpc('bind', { session_id: sid, deck_id: deckId })
  await showDeck(deckId)
}

function slugOf(text) {
  return String(text || '')
    .replace(/(\.mtg-deck-studio|\.mtg-viewer)?\.json$/i, '')
    .replace(/\s*\(\d+\)$/, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
}

// Everything the app knows about lives in the studio folder, so the menu shows one list:
// each deck, and any JSON in the folder that no deck claims yet.
async function libraryRows() {
  const [listed, exported] = await Promise.all([
    rpc('list_decks').catch(() => ({ decks: [] })),
    rpc('list_exports').catch(() => ({ files: [] }))
  ])
  const files = (exported.files || []).map(file => ({ ...file, slug: slugOf(file.name) }))
  const rows = (listed.decks || []).map(deck => {
    const file = files.find(entry => entry.slug === slugOf(deck.title))
    return { key: deck.deck_id, deck_id: deck.deck_id, title: deck.title, cards: deck.card_count, file: file?.name || null }
  })
  const claimed = new Set(rows.map(row => row.file).filter(Boolean))
  for (const file of files) {
    if (claimed.has(file.name) || rows.some(row => slugOf(row.title) === file.slug)) continue
    rows.push({ key: file.path, path: file.path, file: file.name, title: file.name.replace(/\.(mtg-viewer|mtg-deck-studio)?json$/i, '') })
  }
  return rows
}

async function importFile(file) {
  ui({ menu: false, busy: 'importing ' + file.name })
  const path = file.path
  const sid = focusedSession()
  const result = await rpc('import_file', { path, session_id: sid }, 120000)
  if (result.error) { ui({ busy: '', error: result.error }); return }
  if (result.kind === 'save') {
    await showDeck(result.deck_id)
  } else {
    const created = await rpc('import_bundle', { bundle: emptyBundle(result.name), session_id: sid, title: result.name })
    await showDeck(created.deck_id)
    localCommand(result.kind === 'csv' ? 'import_mythictools_csv' : 'load_decklist', { text: result.text, name: result.name }, 'document')
  }
  ui({ busy: '' })
}

function emptyBundle(title) {
  return { app: 'mtg-table-viewer', version: 4, deckTitle: title || '', strategyNotes: '', decklist: '', customBuckets: [], customTableSections: [], customStatsCategories: [], layout: {}, cards: [] }
}

async function newDeck() {
  ui({ menu: false })
  const created = await rpc('import_bundle', { bundle: emptyBundle('New deck'), session_id: focusedSession(), title: 'New deck' })
  await showDeck(created.deck_id)
}

function DeckMenu() {
  const st = useValue($ui)
  if (!st.menu) return null
  return jsxs('div', {
    className: 'rounded border border-(--ui-stroke-secondary) bg-(--ui-bg-secondary) p-1 text-xs shadow-lg',
    style: { position: 'absolute', right: 8, top: '100%', marginTop: 4, zIndex: 50, width: 320, maxHeight: 420, overflow: 'auto',
      // The theme's secondary surface is translucent; a menu over the table needs to be opaque.
      background: 'linear-gradient(0deg, rgba(14,19,27,0.995), rgba(14,19,27,0.995)), var(--ui-bg-secondary, #12171f)',
      borderColor: 'rgba(255,255,255,0.16)', boxShadow: '0 18px 40px rgba(0,0,0,0.6)' },
    children: [
      // Actions first: with twenty decks the list pushes anything below it out of sight, and these
      // two are what the player reaches for, not the twentieth deck.
      jsxs('div', {
        // Sticky: the list scrolls underneath, these actions never leave the top of the menu.
        style: { position: 'sticky', top: -4, zIndex: 2, display: 'flex', gap: 4, padding: '2px 4px 6px',
                 background: 'rgba(14,19,27,0.995)' },
        children: [
        jsx('button', { type: 'button', className: BTN, onClick: () => { ui({ menu: false }); post({ type: 'wb.save' }) }, children: 'Save deck…' }),
        jsx('button', { type: 'button', className: BTN, onClick: newDeck, children: 'New deck' }),
        jsx('button', {
          type: 'button', className: BTN, disabled: !st.deckId,
          title: st.deckId ? 'Close this deck and show an empty studio' : 'No deck is open',
          onClick: () => { ui({ menu: false }); closeDeck() },
          style: st.deckId ? undefined : { opacity: 0.45 },
          children: 'Close deck'
        })
      ] }),
      jsx('div', { className: 'px-2 py-1 text-(--ui-text-tertiary)', children: (st.rows || []).length ? 'Decks' : 'No decks yet' }),
      ...(st.rows || []).map(row => jsx('button', {
        key: row.key, type: 'button', className: 'block w-full truncate rounded px-2 py-1 text-left hover:bg-(--ui-bg-tertiary)',
        title: row.file || row.title,
        onClick: () => (row.deck_id ? openDeckFromLibrary(row.deck_id) : importFile({ name: row.file, path: row.path })),
        children: `${row.title} · ${row.cards != null ? `${row.cards} cards` : 'file'}${row.deck_id === st.deckId ? ' · open' : ''}`
      })),
      jsx('div', {
        style: { padding: '6px 8px 2px', fontSize: 11, lineHeight: 1.45, color: 'var(--ui-text-tertiary, #8b93a7)' },
        children: 'Decks and their JSON files both live in the studio folder.'
      })
    ]
  })
}

function Header() {
  const st = useValue($ui)
  const toggleMenu = async () => {
    if (st.menu) { ui({ menu: false }); return }
    ui({ menu: true, rows: await libraryRows() })
  }
  const ask = () => {
    const names = st.selected.map(s => s.name)
    host.composer.insertText(null, names.length ? `About the selected cards (${names.join(', ')}): ` : 'About this deck: ', { mode: 'block' })
  }
  const last = st.last
  const statusText = st.error || (st.busy ? `${st.busy}…` : last ? `${last.op || 'change'}: ${last.outcome}${last.error ? ' (' + last.error + ')' : ''}` : '')
  return jsxs('div', {
    style: {
      position: 'relative', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 10px',
      borderBottom: '1px solid var(--ui-stroke-secondary, rgba(255,255,255,0.08))'
    },
    children: [
      jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, minWidth: 0 } , children: [
        jsx('span', { style: { fontSize: 12.5, fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 220 }, children: st.title || (st.deckId ? 'Untitled deck' : 'No deck open') }),
        jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-tertiary, #8b93a7)', whiteSpace: 'nowrap' }, children: st.deckId ? `${st.cards} cards · rev ${st.rev}${st.selected.length ? ` · ${st.selected.length} selected` : ''}` : (st.ready ? 'no deck' : 'loading…') })
      ] }),
      jsx('span', { style: { flex: 1, minWidth: 0, fontSize: 11, color: 'var(--ui-text-quaternary, #6b7280)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: last?.error || statusText, children: statusText }),
      jsxs('div', {
        style: { display: 'flex', alignItems: 'center', gap: 2, padding: 2, borderRadius: 8, background: 'var(--ui-bg-tertiary, rgba(255,255,255,0.05))', border: '1px solid var(--ui-stroke-secondary, rgba(255,255,255,0.1))' },
        children: [
          jsx(SegButton, { label: 'Undo', title: 'Undo the last change', onClick: () => localCommand('undo', {}, 'document'), disabled: !st.deckId, path: 'M4 9h8a4 4 0 1 1 0 8H8M7 5 4 9l3 4' }),
          jsx(SegButton, { label: 'Redo', title: 'Redo the last undone change', onClick: () => localCommand('redo', {}, 'document'), disabled: !st.deckId, path: 'M14 9H6a4 4 0 1 0 0 8h4M11 5l3 4-3 4' }),
          jsx(SegButton, { label: 'Ask', title: st.selected.length ? `Ask about ${st.selected.length} selected card${st.selected.length > 1 ? 's' : ''}` : 'Ask about this deck', onClick: ask, disabled: !st.deckId, path: 'M3 4h12v8H8l-3 3v-3H3z' }),
          jsx(SegButton, { label: 'Decks', title: 'Switch, import or create a deck', onClick: toggleMenu, path: 'M3 5h12M3 9h12M3 13h8', menu: true })
        ]
      }),
      jsx(DeckMenu, {})
    ]
  })
}

function SegButton({ label, title, onClick, disabled, path, menu }) {
  // Icon plus word: visible at a glance, still one cohesive control strip.
  const [hot, setHot] = useState(false)
  return jsx('button', {
    type: 'button', title, 'aria-label': title || label, disabled: !!disabled, onClick,
    onMouseEnter: () => setHot(true), onMouseLeave: () => setHot(false),
    style: {
      display: 'inline-flex', alignItems: 'center', gap: 5, height: 26, padding: menu ? '0 8px 0 7px' : '0 9px 0 8px',
      borderRadius: 6, border: 0, font: 'inherit', fontSize: 11.5, fontWeight: 550,
      background: hot && !disabled ? 'var(--ui-bg-hover, rgba(255,255,255,0.1))' : 'transparent',
      color: disabled ? 'var(--ui-text-quaternary, #6b7280)' : 'var(--ui-text-secondary, #c3cad8)',
      cursor: disabled ? 'default' : 'pointer', opacity: disabled ? 0.5 : 1
    },
    children: [
      jsx('svg', {
        width: 13, height: 13, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor',
        'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
        children: jsx('path', { d: path })
      }, label),
      jsx('span', { children: label }),
      menu ? jsx('svg', { width: 8, height: 8, viewBox: '0 0 10 10', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', children: jsx('path', { d: 'M2 4l3 3 3-3' }) }) : null
    ]
  })
}

function cameraReason(error) {
  const name = error?.name || ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'The camera is blocked. Allow camera access for Hermes in Windows Settings › Privacy & security › Camera, then try again.'
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') return 'No camera was found on this device.'
  if (name === 'NotReadableError') return 'The camera is already in use by another application.'
  return 'The camera could not be opened (' + (error?.message || name || 'unknown error') + ').'
}

// The photo has to reach the agent: the composer takes text only, so the capture is
// saved beside the deck and the message carries its path. The agent opens that file
// with its own eyes, reads the printing off the card, and adds it.
function scanPrompt(path, target, title) {
  const add = target === 'sideboard'
    ? 'mtg_deck_studio_sideboard {"action":"add","refs":["Name (SET) 123"],"reason":"Scanned from a photo"}'
    : 'mtg_deck_studio_apply {"op":"add_cards","args":{"name":"Name (SET) 123","quantity":1}}'
  return [
    // The first line says where the card goes, because that is the decision the player made
    // when they pressed Scan (table or sideboard).
    target === 'sideboard' ? 'Card scan — read this photo and add the card to the sideboard.' : 'Card scan — read this photo and add the card to the table.',
    '',
    `Photo: ${path}`,
    `Deck: "${title || loaded.deckId}" (${loaded.deckId}). Target: ${target === 'sideboard' ? 'its sideboard' : 'the deck itself'}.`,
    '',
    'Read the set code and the collector number printed at the bottom-left of the card, plus the language of that printing, and resolve that exact printing. Then run:',
    `  ${add}`,
    '',
    'If the bottom-left printing is unreadable, say what is uncertain and ask me instead of guessing. If the photo is not a Magic card, add nothing.'
  ].join('\n')
}

function CameraScan({ target, title, full, onClose }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const [phase, setPhase] = useState('starting')
  const [shot, setShot] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let dead = false
    const media = typeof navigator !== 'undefined' ? navigator.mediaDevices : null
    if (!media || !media.getUserMedia) {
      setError('This window has no camera access. Open the studio in the desktop app.')
      setPhase('error')
      return undefined
    }
    media.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false })
      .then(stream => {
        if (dead) { stream.getTracks().forEach(track => track.stop()); return }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          const played = videoRef.current.play?.()
          if (played && played.catch) played.catch(() => {})
        }
        setPhase('live')
      })
      .catch(err => { setError(cameraReason(err)); setPhase('error') })
    return () => {
      dead = true
      if (streamRef.current) streamRef.current.getTracks().forEach(track => track.stop())
      streamRef.current = null
    }
  }, [])

  const grab = () => {
    const video = videoRef.current
    if (!video || !video.videoWidth) {
      setError('The camera has not produced a picture yet — give it a second.')
      return
    }
    const scale = Math.min(1, 1600 / Math.max(video.videoWidth, video.videoHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
    canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height)
    setShot(canvas.toDataURL('image/jpeg', 0.9))
    setError('')
    setPhase('shot')
  }

  const send = async () => {
    setPhase('sending')
    setError('')
    try {
      const saved = await rpc('save_scan', { data_url: shot }, 60000)
      const prompt = scanPrompt(saved.path, target, title)
      const sent = host.composer.submit(null, prompt)
      if (!sent) await host.composer.insertText(null, prompt, { mode: 'block' })
      ui({ camera: null, last: { op: 'card scan', outcome: sent ? 'sent to the chat' : 'written to the chat box', rev: loaded.rev, at: Date.now() } })
    } catch (err) {
      setError(String(err?.message || err))
      setPhase('shot')
    }
  }

  const retake = () => { setShot(''); setError(''); setPhase('live') }

  return jsx('div', {
    style: { position: 'absolute', inset: 0, zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(6,9,14,0.82)' },
    children: jsxs('div', {
      style: {
        width: 'min(640px, 94%)', padding: 12, borderRadius: 10, display: 'flex', flexDirection: 'column', gap: 10,
        background: 'linear-gradient(0deg, rgba(14,19,27,0.995), rgba(14,19,27,0.995)), var(--ui-bg-secondary, #12171f)',
        border: '1px solid rgba(255,255,255,0.16)', boxShadow: '0 24px 60px rgba(0,0,0,0.6)'
      },
      children: [
        jsxs('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8 }, children: [
          jsx('strong', { style: { fontSize: 13 }, children: 'Scan a card' }),
          jsx('span', { style: { fontSize: 11, color: 'var(--ui-text-tertiary, #8b93a7)' }, children: target === 'sideboard' ? 'goes to the sideboard' : (full ? 'the deck already has 100 cards — this makes 101' : 'goes into the deck') })
        ] }),
        jsx('span', { style: { fontSize: 11.5, color: 'var(--ui-text-tertiary, #8b93a7)' },
          children: 'Hold the card up to the camera, flat and filling the frame. The chat reads the set code and collector number from the bottom-left, so keep that corner sharp.' }),
        error ? jsx('p', { style: { margin: 0, fontSize: 12, color: '#ffb4a8' }, children: error }) : null,
        jsxs('div', {
          style: { position: 'relative', borderRadius: 8, overflow: 'hidden', background: '#05070a', aspectRatio: '4 / 3' },
          children: [
            jsx('video', {
              ref: videoRef, muted: true, playsInline: true, autoPlay: true,
              style: { width: '100%', height: '100%', objectFit: 'cover', display: !shot && phase !== 'starting' ? 'block' : 'none' }
            }),
            shot ? jsx('img', { src: shot, alt: 'The card you just photographed', style: { width: '100%', height: '100%', objectFit: 'contain' } }) : null,
            phase === 'starting' ? jsx('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, color: 'var(--ui-text-tertiary, #8b93a7)' }, children: 'Opening the camera…' }) : null
          ]
        }),
        jsxs('div', { style: { display: 'flex', gap: 6, justifyContent: 'flex-end' }, children: [
          jsx('button', { type: 'button', className: BTN, onClick: onClose, children: 'Cancel' }),
          shot ? jsx('button', { type: 'button', className: BTN, onClick: retake, children: 'Retake' }) : null,
          shot
            ? jsx('button', { type: 'button', className: BTN, disabled: phase === 'sending', onClick: send,
                children: phase === 'sending' ? 'Sending…' : (target === 'sideboard' ? 'Send to the sideboard' : 'Send into the deck') })
            : jsx('button', { type: 'button', className: BTN, disabled: phase !== 'live', onClick: grab, children: 'Take photo' })
        ] })
      ]
    })
  })
}

function CameraLayer() {
  const st = useValue($ui)
  if (!st.camera) return null
  return jsx(CameraScan, { target: st.camera.target || 'deck', title: st.title, full: (st.cards || 0) >= 100,
    onClose: () => ui({ camera: null }) })
}

function StudioPane() {
  const [src, setSrc] = useState(null)
  const [err, setErr] = useState(null)
  const [build, setBuild] = useState(0)
  useEffect(() => {
    let dead = false
    frame.ready = false
    loaded.deckId = null
    lastSession = null
    viewer.reload = () => setBuild(n => n + 1)
    CTX.rest('/viewer', { timeoutMs: 60000 })
      .then(r => {
        if (dead) return
        viewer.version = r.version
        viewer.dev = !!r.dev
        ui({ dev: !!r.dev })
        viewer.dev = !!r.dev
        setSrc(toDataUrl(r.html))
      })
      .catch(e => setErr(String(e)))
    return () => {
      dead = true
      frame.el = null
      frame.ready = false
      loaded.deckId = null
      ui({ ready: false, deckId: null })
    }
  }, [build])
  return jsxs('div', {
    style: { display: 'flex', flexDirection: 'column', height: '100%', width: '100%', position: 'relative' },
    children: [
      jsx(Header, {}),
      err ? jsx('div', { className: 'p-3 text-xs', children: `The studio backend is not reachable (${err}). Enable the plugin's agent half in Capabilities > Plugins.` }) : null,
      src
        ? jsx(SandboxedFrame, { key: build, src, title: 'MTG Deck Studio', sandbox: 'allow-scripts allow-forms allow-downloads', ref: el => { frame.el = el }, style: { width: '100%', flex: 1, border: 0 } })
        : jsx('div', { className: 'p-3 text-xs text-(--ui-text-tertiary)', children: 'Loading the table…' }),
      jsx(CameraLayer, {})
    ]
  })
}

function CardLinks({ attrs }) {
  const st = useValue($ui)
  const names = String(attrs?.names || attrs?.name || '').split('|').map(s => s.trim()).filter(Boolean).slice(0, 12)
  if (!names.length) return null
  return jsx('span', {
    className: 'inline-flex flex-wrap gap-1',
    children: names.map(name => jsx('button', {
      key: name, type: 'button', className: BTN, disabled: !st.deckId,
      title: st.deckId ? 'Select and focus on the table' : 'Open MTG Deck Studio to focus this card',
      onClick: () => localCommand('select_cards', { names: [name] }),
      children: name
    }))
  })
}

function revealStudio() {
  host.undismissPane?.(PANE_ID)
  host.revealPane?.(PANE_ID)
}

export default {
  id: PLUGIN_ID,
  name: 'MTG Deck Studio',
  register(ctx) {
    CTX = ctx
    ctx.register({
      id: 'table', area: 'panes', title: 'MTG Deck Studio',
      data: { placement: 'right', dock: { pane: 'workspace', pos: 'right' }, width: '62%' },
      render: () => jsx(StudioPane, {})
    })
    ctx.register({ id: 'card-links', area: TRANSCRIPT_DIRECTIVE_AREA, data: { name: 'mtg-card', render: props => jsx(CardLinks, props) } })
    ctx.register({ id: 'open', area: PALETTE_AREA, data: { id: 'mtg-deck-studio.show', label: 'MTG Deck Studio: Show table', keywords: ['mtg', 'magic', 'deck'], run: revealStudio } })
    ctx.onEvent(`plugin.${PLUGIN_ID}.command`, onCommand)
    ctx.onEvent(`plugin.${PLUGIN_ID}.guide`, onGuide)
    ctx.onEvent(`plugin.${PLUGIN_ID}.sideboard`, () => refreshSideboard())
    ctx.addEventListener(window, 'message', onFrameMessage)
    ctx.setInterval(syncSession, 300)
  }
}
