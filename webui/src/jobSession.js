export const ACTIVE_JOB_STATUSES = new Set(['queued', 'running', 'cancelling'])

export function chooseActiveVideoJob(jobs = [], session = {}) {
  const active = jobs.filter((job) => job.kind === 'video' && ACTIVE_JOB_STATUSES.has(job.status))
  return active.find((job) => {
    const context = job.context || {}
    return (!session.projectId || context.projectId === session.projectId)
      && (!session.videoId || context.videoId === session.videoId)
  }) || active[0] || null
}

export function resolveResumeWorkspace(session = {}, job = null, search = '') {
  const params = new URLSearchParams(search)
  const context = job?.context || {}
  return {
    projectId: params.get('projectId') || context.projectId || session.projectId || null,
    videoId: params.get('videoId') || context.videoId || session.videoId || null,
  }
}
