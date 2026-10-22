"""Document-shape helpers for the Phase 1 MongoDB model.

These are application-level schema definitions. MongoDB remains the sole data store.
"""
from datetime import datetime, timezone

SCHEMA_VERSION = 1


def utcnow():
    return datetime.now(timezone.utc)


def user_document(username, password_hash):
    return {
        "username": username,
        "password_hash": password_hash,
        "created_at": utcnow(),
        "schema_version": SCHEMA_VERSION,
    }


def survey_document(owner_id, public_id, title, description="", allow_anonymous=False):
    now = utcnow()
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_id": owner_id,
        "public_id": public_id,
        "title": title,
        "description": description,
        "allow_anonymous": bool(allow_anonymous),
        "status": "draft",  # draft -> published -> closed; reopening is explicit
        "deadline": None,
        "questions": [],
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "closed_at": None,
    }


def response_document(survey_id, public_id, respondent_id, answers, submitted_at=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "survey_id": survey_id,
        "public_id": public_id,
        "respondent_id": respondent_id,
        "answers": answers,
        "submitted_at": submitted_at or utcnow(),
    }
