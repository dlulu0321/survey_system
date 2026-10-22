import unittest
import mongomock
from app import create_app


class AppFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.app = create_app(self.db)
        self.app.config.update(TESTING=True, SECRET_KEY='test-secret')
        self.client = self.app.test_client()

    def register_login(self, username='tester'):
        self.client.post('/register', data={'username':username, 'password':'password123'})
        self.client.post('/login', data={'username':username, 'password':'password123'})

    def test_create_question_publish_submit_and_stats(self):
        self.register_login()
        r = self.client.post('/api/surveys', json={'title':'测试问卷','allow_anonymous':False})
        self.assertEqual(r.status_code, 201)
        survey_id, public_id = r.json['id'], r.json['public_id']
        questions = [
            {'id':'q1','title':'选择颜色','type':'single','required':True,'options':['红','蓝']},
            {'id':'q2','title':'年龄','type':'number','required':True,'min_value':0,'max_value':120,'integer_only':True},
            {'id':'q3','title':'建议','type':'text','required':False,'min_length':0,'max_length':100},
        ]
        r = self.client.put(f'/api/surveys/{survey_id}/questions', json={'questions':questions})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['question_count'], 3)
        r = self.client.post(f'/api/surveys/{survey_id}/publish', json={})
        self.assertEqual(r.status_code, 200)
        self.client.post('/logout')
        r = self.client.post(f'/survey/{public_id}/submit', json={'answers':{'q1':'红','q2':20,'q3':'不错'}})
        self.assertEqual(r.status_code, 401)  # login is required when anonymous filling is disabled
        self.register_login('respondent')
        r = self.client.post(f'/survey/{public_id}/submit', json={'answers':{'q1':'红','q2':20,'q3':'不错'}})
        self.assertEqual(r.status_code, 201)
        r = self.client.get(f'/api/surveys/{survey_id}/stats')
        self.assertEqual(r.status_code, 404)  # another user cannot inspect owner statistics
        self.client.post('/logout')
        self.register_login()
        r = self.client.get(f'/api/surveys/{survey_id}/stats')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['response_count'], 1)
        self.assertEqual(r.json['questions'][0]['option_counts']['红'], 1)

    def test_answer_validation_rejects_invalid_option(self):
        self.register_login()
        r = self.client.post('/api/surveys', json={'title':'校验问卷'})
        sid = r.json['id']
        q = [{'id':'q1','title':'选择','type':'single','required':True,'options':['A','B']}]
        self.client.put(f'/api/surveys/{sid}/questions', json={'questions':q})
        self.client.post(f'/api/surveys/{sid}/publish', json={})
        public_id = self.db.surveys.find_one({'_id':__import__('bson').ObjectId(sid)})['public_id']
        r = self.client.post(f'/survey/{public_id}/submit', json={'answers':{'q1':'C'}})
        self.assertEqual(r.status_code, 400)
        self.assertIn('有效选项', r.json['error'])

    def test_anonymous_submission_when_enabled(self):
        self.register_login()
        r = self.client.post('/api/surveys', json={'title':'匿名问卷','allow_anonymous':True})
        sid = r.json['id']
        self.client.put(f'/api/surveys/{sid}/questions', json={'questions':[{'id':'q','title':'反馈','type':'text','required':True}]})
        self.client.post(f'/api/surveys/{sid}/publish', json={})
        public_id = self.db.surveys.find_one({'_id':__import__('bson').ObjectId(sid)})['public_id']
        self.client.post('/logout')
        r = self.client.post(f'/survey/{public_id}/submit', json={'answers':{'q':'匿名意见'}})
        self.assertEqual(r.status_code, 201)
        response = self.db.responses.find_one({'public_id':public_id})
        self.assertIsNone(response['respondent_id'])


if __name__ == '__main__': unittest.main()
