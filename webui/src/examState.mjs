export function normalizeAttempt(exam, raw) {
  const value = raw && typeof raw === 'object' ? raw : {}
  const answers = {}
  for (const q of exam.questions) {
    const answer = value.answers?.[q.id]
    if (q.type === 'mcq' ? Number.isInteger(answer) && answer >= 0 && answer < q.options.length : typeof answer === 'string') answers[q.id] = answer
  }
  return { answers, finished: value.finished === true,
    flags: Array.isArray(value.flags) ? value.flags.filter(id => exam.questions.some(q => q.id === id)) : [],
    history: Array.isArray(value.history) ? value.history.filter(item => item && typeof item.at === 'number' && Number.isInteger(item.correct) && Number.isInteger(item.answered) && Number.isInteger(item.totalMcq)).slice(-10) : [] }
}
export function questionStatus(question, attempt) {
  const answer = attempt.answers[question.id]
  if (answer === undefined || (typeof answer === 'string' && !answer.trim())) return 'unanswered'
  if (!attempt.finished || question.type !== 'mcq') return 'answered'
  return answer === question.correctOption ? 'correct' : 'wrong'
}
export function attemptSummary(exam, attempt) {
  const statuses = exam.questions.map(q => questionStatus(q,attempt))
  return { answered: statuses.filter(s=>s!=='unanswered').length,
    correct: exam.questions.filter(q=>q.type==='mcq' && attempt.answers[q.id]===q.correctOption).length,
    wrong: statuses.filter(s=>s==='wrong').length,
    unanswered: statuses.filter(s=>s==='unanswered').length,
    totalMcq: exam.questions.filter(q=>q.type==='mcq').length }
}
export function finishAttempt(exam, attempt, at=Date.now()) {
  if (attempt.finished) return attempt
  return {...attempt,finished:true,history:[...attempt.history,{at,...attemptSummary(exam,{...attempt,finished:true})}].slice(-10)}
}
export function restartAttempt(attempt) {
  return {answers:{},flags:[],finished:false,history:attempt.history}
}
