"""MongoDB connection and collection indexes."""
import os
from functools import lru_cache
from pymongo import MongoClient

_client = None


def get_database():
    """Return the configured MongoDB database; connection is lazy."""
    global _client
    uri = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
    name = os.getenv("MONGODB_DATABASE", "survey_system")
    if _client is None:
        _client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    return _client[name]


def init_indexes(db):
    """Create indexes used by account lookup, ownership and response queries."""
    db.users.create_index("username", unique=True, name="uniq_username")
    db.surveys.create_index([("owner_id", 1), ("created_at", -1)], name="owner_created")
    db.surveys.create_index("public_id", unique=True, name="uniq_public_id")
    db.responses.create_index([("survey_id", 1), ("submitted_at", -1)], name="survey_submitted")
    db.responses.create_index([("survey_id", 1), ("respondent_id", 1)], name="survey_respondent")
    db.question_families.create_index([("owner_id", 1), ("updated_at", -1)], name="question_family_owner_updated")
    db.question_families.create_index("share_with_ids", name="question_family_shared_with")
    db.question_versions.create_index([("family_id", 1), ("version", 1)], unique=True, name="uniq_question_version")
    db.question_versions.create_index("parent_version_id", name="question_version_parent")
    db.question_banks.create_index([("owner_id", 1), ("name", 1)], unique=True, name="uniq_question_bank_name")
    db.question_bank_items.create_index([("bank_id", 1), ("version_id", 1)], unique=True, name="uniq_bank_version")
