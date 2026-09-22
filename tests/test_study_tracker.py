import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.study_tracker import StudyStore, Conflict, next_day
from studio_web.study_routes import register_study_routes


class StudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1789549200.0
        self.store=StudyStore(Path(self.tmp.name)/'study.sqlite3',clock=lambda:self.now)
        self.call('project.save',{'name':'Biyoloji'})
        self.pid=self.state()['projects'][0]['id']
        self.call('task.save',{'projectId':self.pid,'title':'Hücre çalış'})
        self.tid=self.state()['tasks'][0]['id']
    def state(self):return self.store.snapshot()
    def call(self,action,data={}):return self.store.command(action,data,self.state()['revision'])
    def timer(self,action):return self.call('timer.'+action,{'timerId':self.state()['timer']['id']})
    def start(self,mode='pomodoro'):return self.call('timer.start',{'taskId':self.tid,'mode':mode})

    def test_timer_survives_restart_and_only_one_segment_is_saved(self):
        self.start('stopwatch');self.now+=123
        reopened=StudyStore(self.store.path,clock=lambda:self.now)
        state=reopened.snapshot();self.assertEqual(state['timer']['status'],'running')
        self.timer('pause');self.timer('pause')
        state=self.state();self.assertEqual(len(state['entries']),1);self.assertEqual(state['entries'][0]['seconds'],123)
        self.now+=90;self.timer('resume');self.now+=7;self.timer('stop')
        self.assertEqual(sum(e['seconds'] for e in self.state()['entries']),130)

    def test_pomodoro_is_capped_and_break_time_is_excluded(self):
        self.start();self.now+=5000
        state=self.state();self.assertEqual(state['timer']['status'],'finished')
        self.assertEqual(state['timer']['completedFocus'],1)
        self.assertEqual(state['entries'][0]['seconds'],1500)
        self.state();self.assertEqual(len(self.state()['entries']),1)
        self.timer('next');self.assertEqual(self.state()['timer']['phase'],'shortBreak')
        self.now+=1000;self.state();self.assertEqual(len(self.state()['entries']),1)
        self.timer('next');self.assertEqual(self.state()['timer']['phase'],'focus')

    def test_paused_countdown_uses_remaining_duration(self):
        self.call('timer.start',{'taskId':self.tid,'mode':'countdown','minutes':2})
        self.now+=40;self.timer('pause');self.now+=500;self.timer('resume');self.now+=100
        state=self.state();self.assertEqual(state['timer']['status'],'finished')
        self.assertEqual(sum(e['seconds'] for e in state['entries']),120)

    def test_long_break_cycle_and_skip(self):
        self.call('settings',{'focusMinutes':1,'shortBreakMinutes':1,'longBreakEvery':2})
        self.start();self.now+=60;self.timer('next');self.now+=60;self.timer('next');self.now+=60
        self.timer('next');self.assertEqual(self.state()['timer']['phase'],'longBreak')
        self.timer('skip');self.timer('next');self.assertEqual(self.state()['timer']['completedFocus'],2)

    def test_switching_tasks_stops_old_timer(self):
        self.start('stopwatch');self.now+=30
        state=self.call('task.save',{'projectId':self.pid,'title':'Enzimler'})
        other=state['tasks'][-1]['id'];self.call('timer.start',{'taskId':other})
        state=self.state();self.assertEqual(state['entries'][0]['taskId'],self.tid)
        self.assertEqual(state['entries'][0]['seconds'],30);self.assertEqual(state['timer']['taskId'],other)

    def test_completed_or_archived_tasks_cannot_run(self):
        self.start('stopwatch');self.now+=4;self.call('task.status',{'id':self.tid,'status':'done'})
        self.assertIsNone(self.state()['timer'])
        with self.assertRaises(ValueError):self.start()
        self.call('task.status',{'id':self.tid,'status':'todo'});self.call('project.archive',{'id':self.pid,'archived':True})
        with self.assertRaises(ValueError):self.start()
        self.assertEqual(self.state()['entries'][0]['seconds'],4)

    def test_subtasks_cycle_and_completion_rules(self):
        state=self.call('task.save',{'projectId':self.pid,'title':'Alt görev','parentId':self.tid})
        child=state['tasks'][-1]['id']
        with self.assertRaises(ValueError):self.call('task.save',{'id':self.tid,'parentId':child})
        with self.assertRaises(ValueError):self.call('task.status',{'id':self.tid,'status':'done'})
        self.call('task.status',{'id':child,'status':'done'});self.call('task.status',{'id':self.tid,'status':'done'})
        with self.assertRaises(ValueError):self.call('task.status',{'id':child,'status':'todo'})

    def test_nested_archive_and_restore(self):
        child=self.call('task.save',{'projectId':self.pid,'title':'Alt','parentId':self.tid})['tasks'][-1]['id']
        self.call('task.archive',{'id':self.tid,'archived':True})
        self.assertTrue(all(t['archived'] for t in self.state()['tasks']))
        with self.assertRaises(ValueError):self.call('task.archive',{'id':child,'archived':False})
        self.call('task.archive',{'id':self.tid,'archived':False})
        self.assertFalse(any(t['archived'] for t in self.state()['tasks']))

    def test_folder_delete_preserves_children_and_tasks(self):
        folder=self.call('folder.save',{'projectId':self.pid,'name':'Hafta 1'})['folders'][-1]['id']
        child=self.call('folder.save',{'projectId':self.pid,'name':'Konu','parentId':folder})['folders'][-1]['id']
        self.call('task.save',{'id':self.tid,'folderId':folder})
        with self.assertRaises(ValueError):self.call('folder.save',{'id':folder,'parentId':child})
        self.call('folder.delete',{'id':folder})
        self.assertIsNone(self.state()['tasks'][0]['folderId']);self.assertIsNone(self.state()['folders'][0]['parentId'])

    def test_repeating_completion_creates_one_next_occurrence(self):
        self.call('task.save',{'id':self.tid,'repeat':'daily','plannedDate':'2026-01-01'})
        self.call('task.status',{'id':self.tid,'status':'done'});self.call('task.status',{'id':self.tid,'status':'done'})
        self.assertEqual(len(self.state()['tasks']),2)
        self.assertGreater(self.state()['tasks'][1]['plannedDate'],'2026-09-15')
        self.call('task.status',{'id':self.tid,'status':'todo'});self.call('task.status',{'id':self.tid,'status':'done'})
        self.assertEqual(len(self.state()['tasks']),2)
        self.assertEqual(next_day('2026-01-31','monthly','2026-01-31'),'2026-02-28')
        self.assertEqual(next_day('2026-01-31','monthly','2026-03-01'),'2026-03-31')

    def test_manual_entries_cannot_overlap_or_be_in_future(self):
        self.call('entry.save',{'taskId':self.tid,'startedAt':self.now-120,'seconds':60})
        for start,seconds in [(self.now-90,60),(self.now-10,60)]:
            with self.assertRaises(ValueError):self.call('entry.save',{'taskId':self.tid,'startedAt':start,'seconds':seconds})
        self.start('stopwatch');self.now+=60
        with self.assertRaises(ValueError):self.call('entry.save',{'taskId':self.tid,'startedAt':self.now-30,'seconds':10})
        self.timer('pause');entry=self.state()['entries'][0]
        self.call('entry.save',{**entry,'seconds':20,'note':'Düzeltildi'})
        self.call('entry.delete',{'id':entry['id']});self.assertEqual(len(self.state()['entries']),1)

    def test_revision_conflicts_prevent_lost_updates_and_double_time(self):
        rev=self.state()['revision']
        with ThreadPoolExecutor(2) as pool:
            def mutate(name):
                try:return self.store.command('project.save',{'name':name},rev)
                except Conflict:return None
            result=list(pool.map(mutate,['A','B']))
        self.assertEqual(sum(r is not None for r in result),1)
        self.assertEqual(len(self.state()['projects']),2)

    def test_failed_validation_is_atomic(self):
        before=self.state()
        with self.assertRaises(ValueError):self.call('task.save',{'id':self.tid,'title':'Yeni','estimateMinutes':-1})
        self.assertEqual(before,self.state())
        for data in [{'title':''},{'plannedDate':'bad'},{'plannedTime':'99:10'},{'tags':['']},{'priority':'bad'}]:
            with self.assertRaises(ValueError):self.call('task.save',{'id':self.tid,**data})

    def test_timer_id_rejects_stale_controls(self):
        self.start()
        with self.assertRaises(Conflict):self.call('timer.stop',{'timerId':'old'})
        self.assertEqual(self.state()['timer']['status'],'running')

    def test_backup_roundtrip_keeps_active_work_without_running_timer(self):
        self.start('stopwatch');self.now+=60
        backup=self.store.backup()
        self.assertIsNone(backup['timer']);self.assertEqual(backup['entries'][0]['seconds'],60)
        self.assertEqual(self.state()['timer']['status'],'running')
        result=self.store.restore(backup,self.state()['revision'])
        self.assertIsNone(result['timer']);self.assertEqual(result['entries'],backup['entries'])

    def test_malformed_backup_cannot_overwrite_data(self):
        before=self.state();backup=self.store.backup();backup['tasks'][0]['parentId']='bad'
        with self.assertRaises(ValueError):self.store.restore(backup,before['revision'])
        self.assertEqual(before,self.state())
        backup=self.store.backup();backup['tasks'].append(copy.deepcopy(backup['tasks'][0]))
        with self.assertRaises(ValueError):self.store.restore(backup,before['revision'])

    def test_restore_completed_subtasks_and_archived_projects(self):
        child=self.call('task.save',{'projectId':self.pid,'title':'Alt','parentId':self.tid})['tasks'][-1]['id']
        self.call('task.status',{'id':child,'status':'done'});self.call('task.status',{'id':self.tid,'status':'done'})
        self.call('project.archive',{'id':self.pid,'archived':True})
        result=self.store.restore(self.store.backup(),self.state()['revision'])
        self.assertTrue(result['projects'][0]['archived']);self.assertTrue(all(t['status']=='done' for t in result['tasks']))

    def test_csv_escapes_spreadsheet_formulas(self):
        self.call('task.save',{'id':self.tid,'title':'=1+1'})
        self.call('entry.save',{'taskId':self.tid,'startedAt':self.now-60,'seconds':30,'note':'@bad'})
        output=self.store.export_csv();self.assertIn("'=1+1",output);self.assertIn("'@bad",output)

    def test_api_rejects_bad_links_payload_and_formats(self):
        app=FastAPI();register_study_routes(app,lambda:self.store,lambda:[{'id':'course','name':'Ders'}])
        client=TestClient(app)
        self.assertEqual(client.get('/api/study').status_code,200)
        self.assertEqual(client.post('/api/study/commands',json={'action':'project.save','data':{'name':'X','courseId':'unknown'},'revision':self.state()['revision']}).status_code,400)
        self.assertEqual(client.post('/api/study/commands',json=[]).status_code,400)
        self.assertEqual(client.post('/api/study/commands',json={'action':'settings','data':{},'revision':-1}).status_code,409)
        self.assertEqual(client.get('/api/study/export?format=xml').status_code,400)
        self.assertEqual(client.get('/api/study/export?format=json').json()['version'],1)
        self.assertIn('text/csv',client.get('/api/study/export?format=csv').headers['content-type'])
