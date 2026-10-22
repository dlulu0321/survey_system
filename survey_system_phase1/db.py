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
