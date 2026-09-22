import test from 'node:test'
import assert from 'node:assert/strict'
import {normalizeAttempt, questionStatus, attemptSummary, finishAttempt, restartAttempt} from '../src/examState.mjs'
const exam={questions:[{id:'1',type:'mcq',options:['A','B'],correctOption:0},{id:'2',type:'mcq',options:['A','B'],correctOption:1},{id:'3',type:'classic'}]}
test('old attempts migrate while invalid answer indices are ignored',()=>{
  const attempt=normalizeAttempt(exam,{answers:{1:0,2:8,3:'Cevap'},finished:true})
  assert.deepEqual(attempt.answers,{'1':0,'3':'Cevap'})
  assert.deepEqual(attempt.flags,[])
  assert.deepEqual(attempt.history,[])
  assert.deepEqual(normalizeAttempt(exam,null).answers,{})
})
test('zero-index answer counts as answered; empty classical answer does not',()=>{
  const attempt=normalizeAttempt(exam,{answers:{1:0,2:0,3:'   '},finished:true})
  assert.equal(questionStatus(exam.questions[0],attempt),'correct')
  assert.equal(questionStatus(exam.questions[1],attempt),'wrong')
  assert.equal(questionStatus(exam.questions[2],attempt),'unanswered')
  assert.deepEqual(attemptSummary(exam,attempt),{answered:2,correct:1,wrong:1,unanswered:1,totalMcq:2})
})
test('answers are not marked right or wrong before finishing',()=>{
  const attempt=normalizeAttempt(exam,{answers:{2:0}})
  assert.equal(questionStatus(exam.questions[1],attempt),'answered')
})
test('finishing twice is idempotent and restarting keeps history',()=>{
  const attempt=normalizeAttempt(exam,{answers:{1:0,3:'Cevap'},flags:['2']})
  const result=finishAttempt(exam,attempt,1000)
  assert.equal(result.history.length,1)
  assert.equal(result.history[0].correct,1)
  assert.equal(finishAttempt(exam,result,2000),result)
  const restarted=restartAttempt(result)
  assert.deepEqual(restarted.answers,{})
  assert.deepEqual(restarted.flags,[])
  assert.equal(restarted.history.length,1)
  assert.equal(result.answers['1'],0)
})
test('only ten recent attempts retained; orphaned flags ignored',()=>{
  let attempt=normalizeAttempt(exam,{flags:['1','deleted']})
  assert.deepEqual(attempt.flags,['1'])
  for(let i=0;i<12;i++)attempt=restartAttempt(finishAttempt(exam,attempt,i))
  assert.equal(attempt.history.length,10)
  assert.equal(attempt.history[0].at,2)
})
