"""Document-shape helpers for the Phase 1 MongoDB model.

These are application-level schema definitions. MongoDB remains the sole data store.
"""
from datetime import datetime, timezone

SCHEMA_VERSION = 2


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


def question_family_document(owner_id, title):
    now = utcnow()
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_id": owner_id,
        "title": title,
        "share_with_ids": [],
        "root_version_id": None,
        "created_at": now,
        "updated_at": now,
    }


def question_version_document(family_id, version, parent_version_id, owner_id, definition, change_note="初始版本"):
    return {
        "schema_version": SCHEMA_VERSION,
        "family_id": family_id,
        "version": version,
        "parent_version_id": parent_version_id,
        "owner_id": owner_id,
        "definition": definition,
        "change_note": change_note,
        "created_at": utcnow(),
    }


def question_bank_document(owner_id, name, description=""):
    return {
        "schema_version": SCHEMA_VERSION,
        "owner_id": owner_id,
        "name": name,
        "description": description,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
