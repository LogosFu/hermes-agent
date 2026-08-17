import { useCallback, useEffect, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Textarea } from '@/components/ui/textarea'
import { getMemoryFiles, type MemoryFileEntry, saveMemoryFile } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notify, notifyError } from '@/store/notifications'

import { PAGE_INSET_X } from '../layout-constants'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

/** The character count the backend enforces: content is split on the section
 *  separator `\n§\n`, each segment trimmed, empties dropped, and the survivors
 *  re-joined before measuring. The card's live usage must match that exactly
 *  or the Save button would enable for content the backend then rejects. */
function memoryFileChars(content: string): number {
  if (!content) {
    return 0
  }

  const sections = content
    .split('\n§\n')
    .map(section => section.trim())
    .filter(Boolean)

  return sections.length === 0 ? 0 : sections.join('\n§\n').length
}

const usageLabel = (chars: number, limit: number) => `${chars.toLocaleString()} / ${limit.toLocaleString()}`

interface MemoryViewProps extends React.ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

export function MemoryView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: MemoryViewProps) {
  const { t } = useI18n()
  const m = t.memory

  const [files, setFiles] = useState<MemoryFileEntry[] | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    let cancelled = false

    setFailed(false)
    getMemoryFiles()
      .then(result => {
        if (!cancelled) {
          setFiles(result.files)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setFailed(true)
        }
      })

    return () => void (cancelled = true)
  }, [])

  useEffect(() => load(), [load])

  const common = (files ?? []).filter(entry => entry.scope !== 'project')
  const projects = (files ?? []).filter(entry => entry.scope === 'project')

  return (
    <section {...props} className="h-full min-h-0 overflow-y-auto bg-(--ui-chat-surface-background)">
      <div className={cn('mx-auto w-full max-w-3xl pb-20', PAGE_INSET_X)}>
        <div className="pt-[calc(var(--titlebar-height)+0.75rem)]">
          <h1 className="text-[length:var(--conversation-text-font-size)] font-semibold">{m.title}</h1>
          <p className="mt-1 text-[length:var(--conversation-caption-font-size)] text-(--ui-text-tertiary)">
            {m.frozenHint}
          </p>
        </div>

        {failed ? (
          <ErrorState className="py-16" description={m.loadFailed} title={m.title}>
            <Button className="mx-auto" onClick={() => load()} size="xs" variant="secondary">
              {m.retry}
            </Button>
          </ErrorState>
        ) : !files ? (
          <PageLoader className="min-h-48" label={m.title} />
        ) : (
          <>
            <section className="mt-6">
              <SectionLabel>{m.groupCommon}</SectionLabel>
              <div className="grid gap-3">
                {common.map(entry => (
                  <MemoryFileCard entry={entry} key={entry.scope} />
                ))}
              </div>
            </section>

            <section className="mt-6">
              <SectionLabel>{m.groupProjects}</SectionLabel>
              <div className="grid gap-3">
                {projects.map(entry => (
                  <MemoryFileCard entry={entry} key={entry.project_id ?? entry.project_name} />
                ))}
              </div>
            </section>
          </>
        )}
      </div>
    </section>
  )
}

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="mb-1.5 text-[0.625rem] font-medium uppercase tracking-[0.08em] text-(--ui-text-tertiary)">
      {children}
    </div>
  )
}

function MemoryFileCard({ entry }: { entry: MemoryFileEntry }) {
  const { t } = useI18n()
  const m = t.memory

  const title =
    entry.scope === 'memory' ? m.agentMemory : entry.scope === 'user' ? m.userProfile : (entry.project_name ?? '')

  const [draft, setDraft] = useState(entry.content)
  // Baseline = last saved (or loaded) content. Save unlocks only when the
  // draft diverges from it AND fits the limit; a successful save re-baselines.
  const [baseline, setBaseline] = useState(entry.content)
  const [saving, setSaving] = useState(false)

  // Real-time usage, counted with the backend's exact rule — on a clean draft
  // this equals the backend's `chars`, so the number stays truthful across saves.
  const liveChars = memoryFileChars(draft)
  const overLimit = liveChars > entry.limit
  const dirty = draft !== baseline

  const save = useCallback(async () => {
    setSaving(true)

    try {
      await saveMemoryFile({ scope: entry.scope, project_id: entry.project_id, content: draft })

      setBaseline(draft)
      notify({ kind: 'success', message: m.saved })
    } catch (err) {
      notifyError(err, m.saveFailed)
    } finally {
      setSaving(false)
    }
  }, [draft, entry.project_id, entry.scope, m])

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quinary) p-3">
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-[length:var(--conversation-text-font-size)] font-medium">{title}</span>
        <span
          className={cn(
            'shrink-0 font-mono text-[length:var(--conversation-caption-font-size)]',
            overLimit ? 'text-destructive' : 'text-(--ui-text-tertiary)'
          )}
        >
          {usageLabel(liveChars, entry.limit)}
        </span>
      </div>

      <Textarea className="min-h-32 font-mono" onChange={event => setDraft(event.target.value)} value={draft} />

      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-[length:var(--conversation-caption-font-size)] text-destructive">
          {overLimit ? m.overLimit : ''}
        </span>
        <Button disabled={!dirty || overLimit || saving} onClick={() => void save()} size="xs" variant="secondary">
          {m.save}
        </Button>
      </div>
    </div>
  )
}
