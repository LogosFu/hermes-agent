import type {
  ProcessBriefResponse,
  ProcessesResponse,
  ProcessTitlesResponse
} from '@/types/hermes'

import { profileScoped } from './client'

// Background-process monitoring for the Tasks page: running terminal children
// with AI-generated Chinese titles, plus a per-process context brief.
export function getProcesses(): Promise<ProcessesResponse> {
  return window.hermesDesktop.api<ProcessesResponse>({
    ...profileScoped(),
    path: '/api/processes'
  })
}

export function getProcessTitles(items: { id: string; command: string }[]): Promise<ProcessTitlesResponse> {
  return window.hermesDesktop.api<ProcessTitlesResponse>({
    ...profileScoped(),
    path: '/api/processes/titles',
    method: 'POST',
    body: { items }
  })
}

export function getProcessBrief(processId: string): Promise<ProcessBriefResponse> {
  return window.hermesDesktop.api<ProcessBriefResponse>({
    ...profileScoped(),
    path: `/api/processes/${encodeURIComponent(processId)}/brief`
  })
}
