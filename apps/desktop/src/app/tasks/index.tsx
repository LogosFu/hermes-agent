import { useCallback, useEffect, useRef, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorState } from '@/components/ui/error-state'
import { LogView } from '@/components/ui/log-view'
import { getProcessBrief, getProcesses, getProcessTitles, type ProcessEntry } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { PAGE_INSET_X } from '../layout-constants'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

// The list polls while the page is visible; durations tick locally between
// polls so a running task's clock never looks frozen.
const POLL_INTERVAL_MS = 5000
const TICK_INTERVAL_MS = 1000

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60

  if (mins < 1) {
    return `${secs}s`
  }

  const hours = Math.floor(mins / 60)

  return hours < 1 ? `${mins}m ${secs}s` : `${hours}h ${mins % 60}m`
}

interface TasksViewProps extends React.ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

export function TasksView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: TasksViewProps) {
  const { t } = useI18n()
  const m = t.tasks

  const [processes, setProcesses] = useState<ProcessEntry[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [titles, setTitles] = useState<Record<string, string>>({})
  const [now, setNow] = useState(() => Date.now())

  // Titles accumulate across polls — a process's command never changes, so a
  // fetched title stays valid for the page's life. In-flight ids guard
  // against a slow titles call being re-issued by every poll.
  const titlesRef = useRef<Record<string, string>>({})
  const titlesInflightRef = useRef<Set<string>>(new Set())

  const fetchTitles = useCallback((entries: ProcessEntry[]) => {
    const missing = entries.filter(
      entry => titlesRef.current[entry.id] === undefined && !titlesInflightRef.current.has(entry.id)
    )

    if (missing.length === 0) {
      return
    }

    missing.forEach(entry => titlesInflightRef.current.add(entry.id))

    getProcessTitles(missing.map(entry => ({ id: entry.id, command: entry.command })))
      .then(result => {
        missing.forEach(entry => titlesInflightRef.current.delete(entry.id))
        titlesRef.current = { ...titlesRef.current, ...result.titles }
        setTitles(titlesRef.current)
      })
      .catch(() => {
        // Leave the ids uncached and un-inflight so the next poll retries.
        missing.forEach(entry => titlesInflightRef.current.delete(entry.id))
      })
  }, [])

  const load = useCallback(() => {
    let cancelled = false

    setFailed(false)
    getProcesses()
      .then(result => {
        if (cancelled) {
          return
        }

        setProcesses(result.processes)
        fetchTitles(result.processes)
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true)
        }
      })

    return () => void (cancelled = true)
  }, [fetchTitles])

  useEffect(() => load(), [load])

  // Poll while visible; a hidden page holds its last snapshot and refreshes
  // the moment it becomes visible again.
  useEffect(() => {
    const poll = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        load()
      }
    }, POLL_INTERVAL_MS)
    const tick = window.setInterval(() => setNow(Date.now()), TICK_INTERVAL_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        load()
      }
    }

    document.addEventListener('visibilitychange', onVisible)

    return () => {
      window.clearInterval(poll)
      window.clearInterval(tick)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  return (
    <section {...props} className="h-full min-h-0 overflow-y-auto bg-(--ui-chat-surface-background)">
      <div className={cn('mx-auto w-full max-w-3xl pb-20', PAGE_INSET_X)}>
        <div className="pt-[calc(var(--titlebar-height)+0.75rem)]">
          <h1 className="text-[length:var(--conversation-text-font-size)] font-semibold">{m.title}</h1>
        </div>

        {failed ? (
          <ErrorState className="py-16" description={m.loadFailed} title={m.title}>
            <Button className="mx-auto" onClick={() => load()} size="xs" variant="secondary">
              {m.retry}
            </Button>
          </ErrorState>
        ) : !processes ? (
          <PageLoader className="min-h-48" label={m.title} />
        ) : processes.length === 0 ? (
          <EmptyState title={m.empty} />
        ) : (
          <div className="mt-6 grid gap-3">
            {processes.map(entry => (
              <TaskCard entry={entry} key={entry.id} now={now} title={titles[entry.id]} />
            ))}
          </div>
        )}
      </div>
    </section>
  )
}

function TaskCard({ entry, title, now }: { entry: ProcessEntry; title: string | undefined; now: number }) {
  const { t } = useI18n()
  const m = t.tasks

  const [commandExpanded, setCommandExpanded] = useState(false)
  const [briefOpen, setBriefOpen] = useState(false)
  const [brief, setBrief] = useState<string | null>(null)
  const [briefFailed, setBriefFailed] = useState(false)

  const duration = formatDuration(now / 1000 - entry.started_at)

  const toggleBrief = useCallback(() => {
    const opening = !briefOpen
    setBriefOpen(opening)

    // Lazily fetched on first open; a failure re-arms so the next open retries.
    if (opening && brief === null && !briefFailed) {
      getProcessBrief(entry.id)
        .then(result => setBrief(result.content))
        .catch(() => setBriefFailed(true))
    }
  }, [brief, briefFailed, briefOpen, entry.id])

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-3">
      <div className="flex items-center gap-2">
        <span
          aria-label={entry.exited ? m.exited : m.running}
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            entry.exited ? 'bg-(--ui-text-tertiary)' : 'animate-pulse bg-(--ui-accent)'
          )}
        />
        {title === undefined ? (
          <span className="h-4 w-40 animate-pulse rounded bg-(--ui-bg-quaternary)" />
        ) : (
          <span className="min-w-0 truncate text-[length:var(--conversation-text-font-size)] font-medium">
            {title}
          </span>
        )}
        <span className="ml-auto shrink-0 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
          {entry.exited ? m.exitCode(entry.exit_code) : m.running}
        </span>
      </div>

      <div className="mt-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
        {entry.project_name ?? m.noProject} · {duration}
      </div>

      <button
        aria-expanded={commandExpanded}
        className={cn(
          'mt-2 block w-full text-left font-mono text-[0.6875rem] leading-[1.5] break-all text-(--ui-text-secondary)',
          !commandExpanded && 'truncate'
        )}
        onClick={() => setCommandExpanded(expanded => !expanded)}
        type="button"
      >
        {entry.command}
      </button>

      {entry.brief_path && (
        <div className="mt-2">
          <Button onClick={toggleBrief} size="xs" variant="secondary">
            {briefOpen ? m.hideBrief : m.viewBrief}
          </Button>
          {briefOpen && (
            <LogView className="mt-2 max-h-64">
              {briefFailed ? m.briefFailed : (brief ?? m.briefLoading)}
            </LogView>
          )}
        </div>
      )}

      {entry.output_tail && <LogView className="mt-2 max-h-32">{entry.output_tail}</LogView>}
    </div>
  )
}
