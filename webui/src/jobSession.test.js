import test from 'node:test'
import assert from 'node:assert/strict'
import { ACTIVE_JOB_STATUSES, chooseActiveVideoJob, resolveResumeWorkspace } from './jobSession.js'

test('cancelling render remains attachable after refresh', () => {
  assert.equal(ACTIVE_JOB_STATUSES.has('cancelling'), true)
})

test('active render matching the saved video is preferred', () => {
  const jobs = [
    { id: 'other', kind: 'video', status: 'running', context: { projectId: 'p2', videoId: 'v2' } },
    { id: 'wanted', kind: 'video', status: 'running', context: { projectId: 'p1', videoId: 'v1' } },
  ]
  assert.equal(chooseActiveVideoJob(jobs, { projectId: 'p1', videoId: 'v1' }).id, 'wanted')
})

test('terminal job is skipped when a newer render is active', () => {
  const jobs = [
    { id: 'old', kind: 'video', status: 'cancelled', context: { projectId: 'p1', videoId: 'v1' } },
    { id: 'new', kind: 'video', status: 'running', context: { projectId: 'p1', videoId: 'v1' } },
  ]
  assert.equal(chooseActiveVideoJob(jobs, { projectId: 'p1', videoId: 'v1' }).id, 'new')
})

test('resume URL context overrides stale local storage and job context', () => {
  const session = { projectId: 'old-project', videoId: 'old-video' }
  const job = { context: { projectId: 'job-project', videoId: 'job-video' } }
  assert.deepEqual(
    resolveResumeWorkspace(session, job, '?projectId=url-project&videoId=url-video'),
    { projectId: 'url-project', videoId: 'url-video' },
  )
})

test('active job context replaces a stale saved workspace', () => {
  const session = { projectId: 'old-project', videoId: 'old-video' }
  const job = { context: { projectId: 'active-project', videoId: 'active-video' } }
  assert.deepEqual(
    resolveResumeWorkspace(session, job),
    { projectId: 'active-project', videoId: 'active-video' },
  )
})

test('job context restores workspace when local storage is empty', () => {
  const job = { context: { projectId: 'course', videoId: 'lesson' } }
  assert.deepEqual(resolveResumeWorkspace({}, job), { projectId: 'course', videoId: 'lesson' })
})
