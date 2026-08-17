import type { MemoryFilesResponse } from '@/types/hermes'

import { profileScoped } from './client'

// Project-memory file layer (parity with `hermes memory files`): the shared
// MEMORY.md plus one per registered project, editable from the Memory page.
export function getMemoryFiles(): Promise<MemoryFilesResponse> {
  return window.hermesDesktop.api<MemoryFilesResponse>({
    ...profileScoped(),
    path: '/api/memory/files'
  })
}

export function saveMemoryFile(input: {
  scope: string
  project_id?: string
  content: string
}): Promise<{ ok: boolean; scope: string; chars: number; limit: number }> {
  return window.hermesDesktop.api<{ ok: boolean; scope: string; chars: number; limit: number }>({
    ...profileScoped(),
    path: '/api/memory/files',
    method: 'PUT',
    body: input
  })
}
