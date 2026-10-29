import unittest
from logic import ValidationError, calculate_statistics, next_question_id, validate_answers, validate_questions


class LogicTests(unittest.TestCase):
    def setUp(self):
        self.questions = [
            {"id":"q1","title":"选择","type":"single","required":True,"options":["A","B"],"jump_rules":[{"operator":"eq","value":"A","target_question_id":"q3"}]},
            {"id":"q2","title":"年龄","type":"number","required":True,"min_value":0,"max_value":120,"integer_only":True},
            {"id":"q3","title":"介绍","type":"text","required":False,"min_length":2,"max_length":10},
        ]

    def test_question_schema_and_jump_target(self):
        self.assertEqual(len(validate_questions(self.questions)), 3)
        self.assertEqual(next_question_id(self.questions[0], "A"), "q3")
        self.assertIsNone(next_question_id(self.questions[0], "B"))

    def test_required_and_numeric_validation(self):
        with self.assertRaises(ValidationError):
            validate_answers(self.questions, {"q2": 20})
        with self.assertRaises(ValidationError):
            validate_answers(self.questions, {"q1":"B", "q2":20.5})
        self.assertEqual(validate_answers(self.questions, {"q1":"B", "q2":20}), {"q1":"B", "q2":20})

    def test_multiple_selection_validation(self):
        q = [{"id":"m","title":"多选","type":"multiple","required":True,"options":["x","y","z"],"min_select":2,"max_select":2}]
        with self.assertRaises(ValidationError): validate_answers(q, {"m":["x"]})
        with self.assertRaises(ValidationError): validate_answers(q, {"m":["x","x"]})
        self.assertTrue(validate_answers(q, {"m":["x","y"]}))

    def test_skipped_required_question_is_not_required(self):
        questions = [
            {"id":"q1","title":"分流","type":"single","required":True,"options":["跳过","继续"],
             "jump_rules":[{"operator":"eq","value":"跳过","target_question_id":"q3"}]},
            {"id":"q2","title":"应被跳过","type":"text","required":True},
            {"id":"q3","title":"后续题","type":"text","required":True}
        ]
        answers = {"q1":"跳过", "q3":"已回答"}
        self.assertEqual(validate_answers(questions, answers), answers)

    def test_numeric_jump_equality_with_html_string(self):
        from logic import next_question_id
        question = {"id":"age","title":"年龄","type":"number","jump_rules":[{"operator":"eq","value":18,"target_question_id":"adult"}]}
        self.assertEqual(next_question_id(question, "18"), "adult")

    def test_statistics(self):
        survey = {"_id":"s1", "title":"demo", "questions":[{"id":"s","title":"pick","type":"single","options":["A","B"]},{"id":"n","title":"age","type":"number"}]}
        stats = calculate_statistics(survey, [{"answers":{"s":"A","n":20}},{"answers":{"s":"A","n":40}},{"answers":{"s":"B","n":None}}])
        self.assertEqual(stats["response_count"], 3)
        self.assertEqual(stats["questions"][0]["option_counts"], {"A":2,"B":1})
        self.assertEqual(stats["questions"][1]["average"], 30)


if __name__ == '__main__': unittest.main()
