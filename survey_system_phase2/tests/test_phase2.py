import json
import unittest

import mongomock
from bson import ObjectId

from app import create_app


class PhaseTwoQuestionBankTests(unittest.TestCase):
    def setUp(self):
        self.db = mongomock.MongoClient().db
        self.app = create_app(self.db)
        self.app.config.update(TESTING=True, SECRET_KEY="phase2-test-secret")
        self.client = self.app.test_client()
        self.register_login(self.client, "owner")

    @staticmethod
    def register_login(client, username, password="password123"):
        client.post("/register", data={"username": username, "password": password})
        client.post("/login", data={"username": username, "password": password})

    def create_library_question(self, definition=None):
        definition = definition or {
            "title": "你的年龄？", "type": "number", "required": True,
            "min_value": 0, "max_value": 120, "integer_only": True,
        }
        response = self.client.post("/question-bank/new-question", data={"question_json": json.dumps(definition, ensure_ascii=False)})
        self.assertEqual(response.status_code, 302)
        family = self.db.question_families.find_one({"owner_id": self.db.users.find_one({"username": "owner"})["_id"]})
        version = self.db.question_versions.find_one({"family_id": family["_id"], "version": 1})
        return family, version

    def create_draft(self, title):
        response = self.client.post("/api/surveys", json={"title": title, "allow_anonymous": True})
        self.assertEqual(response.status_code, 201)
        return response.json["id"], response.json["public_id"]

    def add_version(self, survey_id, version):
        response = self.client.post(f"/surveys/{survey_id}/add-library-question/{version['_id']}")
        self.assertEqual(response.status_code, 302)
        survey = self.db.surveys.find_one({"_id": ObjectId(survey_id)})
        q = survey["questions"][-1]
        self.assertEqual(q["source_question_version_id"], str(version["_id"]))
        return q

    def publish(self, survey_id):
        response = self.client.post(f"/api/surveys/{survey_id}/publish", json={})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_reusable_question_version_snapshot_and_cross_survey_stats(self):
        family, version1 = self.create_library_question()
        sid1, public1 = self.create_draft("年龄调查 A")
        q1 = self.add_version(sid1, version1)
        self.publish(sid1)
        submit1 = self.client.post(f"/survey/{public1}/submit", json={"answers": {q1["id"]: 20}})
        self.assertEqual(submit1.status_code, 201)

        # Creating a new library version never updates the saved survey snapshot.
        edited = {"title": "你的年龄（新版本）？", "type": "number", "required": True,
                  "min_value": 0, "max_value": 150, "integer_only": True}
        self.client.post(f"/question-bank/{family['_id']}/versions/{version1['_id']}/new",
                         data={"question_json": json.dumps(edited, ensure_ascii=False), "change_note": "修改年龄上限"})
        version2 = self.db.question_versions.find_one({"family_id": family["_id"], "version": 2})
        self.assertIsNotNone(version2)
        unchanged = self.db.surveys.find_one({"_id": ObjectId(sid1)})["questions"][0]
        self.assertEqual(unchanged["title"], "你的年龄？")
        self.assertEqual(unchanged["source_question_version_id"], str(version1["_id"]))

        # Published question configuration cannot be changed through the JSON API.
        rejected = self.client.put(f"/api/surveys/{sid1}/questions", json={"questions": [{"id": "bad", "title": "改写", "type": "text"}]})
        self.assertEqual(rejected.status_code, 409)

        sid2, public2 = self.create_draft("年龄调查 B")
        q2 = self.add_version(sid2, version2)
        self.publish(sid2)
        submit2 = self.client.post(f"/survey/{public2}/submit", json={"answers": {q2["id"]: 30}})
        self.assertEqual(submit2.status_code, 201)

        detail = self.client.get(f"/question-bank/families/{family['_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("跨问卷统计", detail.get_data(as_text=True))
        self.assertIn("25.00", detail.get_data(as_text=True))
        self.assertEqual(self.db.surveys.count_documents({"questions.source_question_family_id": str(family["_id"]) }), 2)
        self.assertEqual(self.db.question_versions.count_documents({"family_id": family["_id"]}), 2)

    def test_sharing_question_with_another_user(self):
        family, version = self.create_library_question({"title": "满意度", "type": "single", "required": True, "options": ["满意", "不满意"]})
        other = self.app.test_client()
        self.register_login(other, "colleague")
        response = self.client.post(f"/question-bank/{family['_id']}/share", data={"username": "colleague"})
        self.assertEqual(response.status_code, 302)
        library = other.get("/question-bank")
        self.assertEqual(library.status_code, 200)
        self.assertIn("满意度", library.get_data(as_text=True))
        survey_response = other.post("/api/surveys", json={"title": "共享题测试", "allow_anonymous": True})
        sid = survey_response.json["id"]
        added = other.post(f"/surveys/{sid}/add-library-question/{version['_id']}")
        self.assertEqual(added.status_code, 302)
        used = self.db.surveys.find_one({"_id": ObjectId(sid)})["questions"][0]
        self.assertEqual(used["source_question_family_id"], str(family["_id"]))

    def test_published_survey_copy_creates_editable_draft(self):
        family, version = self.create_library_question()
        sid, _ = self.create_draft("原问卷")
        original_q = self.add_version(sid, version)
        self.publish(sid)
        response = self.client.post(f"/surveys/{sid}/copy-draft")
        self.assertEqual(response.status_code, 302)
        copied_id = response.location.rstrip("/").split("/")[-2]
        copied = self.db.surveys.find_one({"_id": ObjectId(copied_id)})
        original = self.db.surveys.find_one({"_id": ObjectId(sid)})
        self.assertEqual(copied["status"], "draft")
        self.assertEqual(copied["questions"][0]["title"], original_q["title"])
        self.assertEqual(original["status"], "published")
        self.assertEqual(original["questions"][0]["title"], original_q["title"])


    def test_restore_creates_a_new_version_and_custom_bank_can_store_version(self):
        family, version1 = self.create_library_question()
        edited = {"title": "年龄范围扩展", "type": "number", "required": True,
                  "min_value": 0, "max_value": 150, "integer_only": True}
        self.client.post(f"/question-bank/{family['_id']}/versions/{version1['_id']}/new",
                         data={"question_json": json.dumps(edited, ensure_ascii=False), "change_note": "扩展范围"})
        version2 = self.db.question_versions.find_one({"family_id": family["_id"], "version": 2})
        restored = self.client.post(f"/question-bank/{family['_id']}/restore/{version1['_id']}")
        self.assertEqual(restored.status_code, 302)
        version3 = self.db.question_versions.find_one({"family_id": family["_id"], "version": 3})
        self.assertEqual(version3["definition"], version1["definition"])
        self.assertEqual(version3["parent_version_id"], version2["_id"])
        self.assertEqual(self.db.question_versions.count_documents({"family_id": family["_id"]}), 3)

        self.client.post("/question-bank/new-bank", data={"name": "课程题库", "description": "测试用题库"})
        bank = self.db.question_banks.find_one({"owner_id": self.db.users.find_one({"username": "owner"})["_id"], "name": "课程题库"})
        response = self.client.post(f"/question-bank/add-to-bank/{version3['_id']}", data={"bank_id": str(bank["_id"])})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.db.question_bank_items.find_one({"bank_id": bank["_id"], "version_id": version3["_id"]}))


if __name__ == "__main__":
    unittest.main()
