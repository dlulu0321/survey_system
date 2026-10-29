"""Phase 1 questionnaire system: Flask + MongoDB."""
import json
import os
import secrets
import copy
from datetime import datetime, timezone
from functools import wraps

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_database, init_indexes
from logic import ValidationError, calculate_statistics, survey_is_open, validate_answers, validate_questions
from schema import (response_document, survey_document, user_document, utcnow,
                    question_family_document, question_version_document, question_bank_document)

load_dotenv()


def create_app(test_db=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "dev-only-change-this-secret"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        JSON_SORT_KEYS=False,
    )
    db = test_db if test_db is not None else get_database()
    app.extensions["survey_db"] = db
    if test_db is not None:
        init_indexes(db)
    else:
        # Defer network I/O until the first request so importing app.py and
        # displaying the startup command does not require MongoDB to be online.
        index_state = {"ready": False}
        @app.before_request
        def ensure_indexes():
            if not index_state["ready"]:
                init_indexes(db)
                index_state["ready"] = True

    def current_user():
        uid = session.get("user_id")
        if not uid:
            return None
        try:
            return db.users.find_one({"_id": ObjectId(uid)})
        except Exception:
            session.pop("user_id", None)
            return None

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not current_user():
                if request.path.startswith("/api/"):
                    return jsonify(error="请先登录"), 401
                flash("请先登录后再操作", "error")
                return redirect(url_for("login", next=request.path))
            return fn(*args, **kwargs)
        return wrapped

    def owner_survey(survey_id):
        try:
            oid = ObjectId(survey_id)
        except Exception:
            abort(404)
        survey = db.surveys.find_one({"_id": oid, "owner_id": current_user()["_id"]})
        if not survey:
            abort(404)
        return survey

    def json_error(message, status=400):
        return jsonify(error=message), status

    def ensure_default_bank(user_id):
        bank = db.question_banks.find_one({"owner_id": user_id, "name": "我的题库"})
        if bank:
            return bank
        try:
            result = db.question_banks.insert_one(question_bank_document(user_id, "我的题库", "个人常用题目"))
            return db.question_banks.find_one({"_id": result.inserted_id})
        except DuplicateKeyError:
            return db.question_banks.find_one({"owner_id": user_id, "name": "我的题库"})

    def normalize_library_definition(raw):
        if not isinstance(raw, dict):
            raise ValidationError("题目内容必须是 JSON 对象")
        definition = {k: copy.deepcopy(v) for k, v in raw.items()
                      if k not in {"id", "jump_rules", "source_question_family_id", "source_question_version_id", "source_question_version"}}
        # Jump targets depend on the surrounding survey; they are configured after a template is inserted.
        try:
            validate_questions([{**definition, "id": "library_question_validation", "jump_rules": []}])
        except ValidationError as exc:
            raise ValidationError(str(exc)) from exc
        return definition

    def preserve_valid_question_provenance(questions):
        """Keep source links only when the embedded question still matches its immutable library version."""
        provenance_keys = {"source_question_family_id", "source_question_version_id", "source_question_version"}
        for question in questions:
            version_id = question.get("source_question_version_id")
            family_id = question.get("source_question_family_id")
            if not version_id or not family_id:
                for key in provenance_keys:
                    question.pop(key, None)
                continue
            try:
                version = db.question_versions.find_one({"_id": ObjectId(version_id), "family_id": ObjectId(family_id)})
            except Exception:
                version = None
            candidate = {k: copy.deepcopy(v) for k, v in question.items() if k not in ({"id", "jump_rules"} | provenance_keys)}
            if not version or candidate != version.get("definition"):
                # A manually edited copy is no longer attributed to the original template.
                for key in provenance_keys:
                    question.pop(key, None)
            else:
                question["source_question_version"] = version.get("version")
        return questions

    def question_accessible(family, user):
        return bool(family and user and (family.get("owner_id") == user["_id"] or user["_id"] in family.get("share_with_ids", [])))

    def question_family_or_404(family_id):
        try:
            family = db.question_families.find_one({"_id": ObjectId(family_id)})
        except Exception:
            family = None
        if not question_accessible(family, current_user()):
            abort(404)
        return family

    def get_accessible_question_versions(user):
        if not user:
            return []
        families = list(db.question_families.find({"$or": [{"owner_id": user["_id"]}, {"share_with_ids": user["_id"]}]}))
        output = []
        for family in families:
            latest = db.question_versions.find_one({"family_id": family["_id"]}, sort=[("version", -1)])
            if latest:
                output.append({"family": family, "version": latest, "is_owner": family.get("owner_id") == user["_id"]})
        return sorted(output, key=lambda x: x["family"].get("updated_at", x["family"].get("created_at")), reverse=True)

    def question_family_usage(family_id, version_id=None, survey_owner_id=None):
        usage = []
        survey_query = {"owner_id": survey_owner_id} if survey_owner_id is not None else {}
        for survey in db.surveys.find(survey_query):
            for question in survey.get("questions", []):
                if question.get("source_question_family_id") == str(family_id) and (version_id is None or question.get("source_question_version_id") == str(version_id)):
                    usage.append({"survey": survey, "question": question, "version_id": question.get("source_question_version_id")})
        return usage

    def calculate_cross_survey_question_stats(family_id, survey_owner_id=None):
        all_answers, version_stats, usage = [], {}, []
        survey_query = {"owner_id": survey_owner_id} if survey_owner_id is not None else {}
        for survey in db.surveys.find(survey_query):
            for question in survey.get("questions", []):
                if question.get("source_question_family_id") != str(family_id):
                    continue
                version_key = question.get("source_question_version", "未知")
                usage.append({"survey": survey, "question": question, "version": version_key})
                stats = version_stats.setdefault(str(version_key), {"version": version_key, "title": question.get("title", ""), "type": question.get("type"), "response_count": 0, "option_counts": {}, "numeric_values": [], "text_answers": []})
                responses = db.responses.find({"survey_id": survey["_id"]})
                for response in responses:
                    answers = response.get("answers", {})
                    if question.get("id") not in answers:
                        continue
                    value = answers[question["id"]]
                    if value is None or value == "" or value == []:
                        continue
                    stats["response_count"] += 1
                    all_answers.append((question.get("type"), value, question.get("options", [])))
                    if question.get("type") in {"single", "multiple"}:
                        selected = [value] if question.get("type") == "single" else value if isinstance(value, list) else []
                        for option in selected:
                            stats["option_counts"][option] = stats["option_counts"].get(option, 0) + 1
                    elif question.get("type") == "number":
                        try:
                            number = float(value)
                            if number == number and abs(number) != float("inf"):
                                stats["numeric_values"].append(number)
                        except (TypeError, ValueError):
                            pass
                    elif question.get("type") == "text":
                        stats["text_answers"].append(str(value))
        numeric = [value for qtype, value, _ in all_answers if qtype == "number"]
        numeric_values = []
        for value in numeric:
            try:
                number = float(value)
                if number == number and abs(number) != float("inf"):
                    numeric_values.append(number)
            except (TypeError, ValueError):
                pass
        overall = {"response_count": len(all_answers), "type_counts": {}, "option_counts": {}, "numeric_count": len(numeric_values), "numeric_average": (sum(numeric_values) / len(numeric_values) if numeric_values else None), "numeric_min": (min(numeric_values) if numeric_values else None), "numeric_max": (max(numeric_values) if numeric_values else None)}
        for qtype, value, _ in all_answers:
            overall["type_counts"][qtype] = overall["type_counts"].get(qtype, 0) + 1
            if qtype == "single":
                overall["option_counts"][value] = overall["option_counts"].get(value, 0) + 1
            elif qtype == "multiple" and isinstance(value, list):
                for option in value:
                    overall["option_counts"][option] = overall["option_counts"].get(option, 0) + 1
        for item in version_stats.values():
            vals = item.pop("numeric_values")
            item["numeric_count"] = len(vals)
            item["numeric_average"] = sum(vals) / len(vals) if vals else None
            item["numeric_min"] = min(vals) if vals else None
            item["numeric_max"] = max(vals) if vals else None
            item["text_sample"] = item.pop("text_answers")[:20]
        return overall, sorted(version_stats.values(), key=lambda x: x["version"]), usage

    @app.get("/")
    def index():
        return render_template("index.html", user=current_user())

    @app.get("/register")
    def register():
        return render_template("register.html")

    @app.post("/register")
    def register_post():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(username) > 32:
            flash("用户名长度必须为 3～32 个字符", "error")
            return redirect(url_for("register"))
        if len(password) < 8:
            flash("密码至少需要 8 个字符", "error")
            return redirect(url_for("register"))
        try:
            db.users.insert_one(user_document(username, generate_password_hash(password)))
        except DuplicateKeyError:
            flash("用户名已存在", "error")
            return redirect(url_for("register"))
        flash("注册成功，请登录", "success")
        return redirect(url_for("login"))

    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.users.find_one({"username": username})
        if not user or not check_password_hash(user["password_hash"], password):
            flash("用户名或密码错误", "error")
            return redirect(url_for("login"))
        session.clear()
        session["user_id"] = str(user["_id"])
        target = request.args.get("next", "")
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("dashboard")
        return redirect(target)

    @app.post("/logout")
    def logout():
        session.clear()
        flash("已退出登录", "success")
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        surveys = list(db.surveys.find({"owner_id": current_user()["_id"]}).sort("created_at", -1))
        return render_template("dashboard.html", user=current_user(), surveys=surveys)

    @app.get("/surveys/new")
    @login_required
    def survey_new():
        return render_template("survey_edit.html", survey=None, sample_questions=json.dumps([
            {"id":"q1","title":"你最喜欢哪种水果？","type":"single","required":True,"options":["苹果","香蕉","西瓜"]},
            {"id":"q2","title":"你平时喜欢哪些运动？","type":"multiple","required":False,"options":["跑步","游泳","羽毛球"],"min_select":0,"max_select":2},
            {"id":"q3","title":"请简单介绍一下自己","type":"text","required":False,"min_length":0,"max_length":200},
            {"id":"q4","title":"你的年龄？","type":"number","required":False,"min_value":0,"max_value":120,"integer_only":True}
        ], ensure_ascii=False, indent=2))

    @app.post("/surveys/new")
    @login_required
    def survey_create():
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        allow_anonymous = request.form.get("allow_anonymous") == "on"
        if not title:
            flash("问卷标题不能为空", "error")
            return redirect(url_for("survey_new"))
        doc = survey_document(current_user()["_id"], secrets.token_urlsafe(9), title, description, allow_anonymous)
        result = db.surveys.insert_one(doc)
        flash("问卷已创建，请继续编辑题目", "success")
        return redirect(url_for("survey_edit", survey_id=str(result.inserted_id)))

    @app.get("/surveys/<survey_id>/edit")
    @login_required
    def survey_edit(survey_id):
        survey = owner_survey(survey_id)
        return render_template("survey_edit.html", survey=survey, sample_questions=json.dumps(survey.get("questions", []), ensure_ascii=False, indent=2))

    @app.post("/surveys/<survey_id>/edit")
    @login_required
    def survey_edit_post(survey_id):
        survey = owner_survey(survey_id)
        if survey.get("status") == "closed":
            flash("已关闭的问卷不能编辑", "error")
            return redirect(url_for("dashboard"))
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        allow_anonymous = (request.form.get("allow_anonymous") == "on") if survey.get("status") == "draft" else bool(survey.get("allow_anonymous", False))
        try:
            questions = json.loads(request.form.get("questions_json", "[]"))
            validate_questions(questions)
            preserve_valid_question_provenance(questions)
        except (json.JSONDecodeError, ValidationError) as exc:
            flash(f"题目配置不合法：{exc}", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        if not title:
            flash("问卷标题不能为空", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        if survey.get("status") == "published" and questions != survey.get("questions", []):
            flash("已发布问卷的题目内容已冻结。请复制为草稿后再修改题目。", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        update_fields = {"title": title, "description": description, "allow_anonymous": allow_anonymous, "updated_at": utcnow()}
        if survey.get("status") != "published":
            update_fields["questions"] = questions
        db.surveys.update_one({"_id": survey["_id"]}, {"$set": update_fields})
        flash("问卷和题目已保存", "success")
        return redirect(url_for("survey_edit", survey_id=survey_id))

    @app.post("/surveys/<survey_id>/publish")
    @login_required
    def survey_publish(survey_id):
        survey = owner_survey(survey_id)
        if survey.get("status") == "closed":
            flash("已关闭的问卷不能直接重新发布", "error")
            return redirect(url_for("dashboard"))
        try:
            validate_questions(survey.get("questions", []))
        except ValidationError as exc:
            flash(f"发布失败：{exc}", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        if not survey.get("questions"):
            flash("至少添加一道题目后才能发布", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        deadline_text = request.form.get("deadline", "").strip()
        deadline = None
        if deadline_text:
            try:
                deadline = datetime.fromisoformat(deadline_text)
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if deadline <= utcnow(): raise ValueError()
            except ValueError:
                flash("截止时间必须是未来时间", "error")
                return redirect(url_for("survey_edit", survey_id=survey_id))
        db.surveys.update_one({"_id": survey["_id"]}, {"$set": {"status":"published", "deadline":deadline, "published_at":utcnow(), "updated_at":utcnow()}})
        flash("问卷已发布", "success")
        return redirect(url_for("dashboard"))

    @app.post("/surveys/<survey_id>/close")
    @login_required
    def survey_close(survey_id):
        survey = owner_survey(survey_id)
        db.surveys.update_one({"_id": survey["_id"]}, {"$set":{"status":"closed", "closed_at":utcnow(), "updated_at":utcnow()}})
        flash("问卷已关闭", "success")
        return redirect(url_for("dashboard"))

    @app.post("/surveys/<survey_id>/copy-draft")
    @login_required
    def survey_copy_draft(survey_id):
        source = owner_survey(survey_id)
        new_doc = survey_document(current_user()["_id"], secrets.token_urlsafe(9), f"{source.get('title', '问卷')}（副本）", source.get("description", ""), source.get("allow_anonymous", False))
        new_doc["questions"] = copy.deepcopy(source.get("questions", []))
        new_doc["copied_from_survey_id"] = source["_id"]
        result = db.surveys.insert_one(new_doc)
        flash("已复制为草稿；新草稿可以独立修改题目。", "success")
        return redirect(url_for("survey_edit", survey_id=str(result.inserted_id)))

    @app.get("/survey/<public_id>")
    def survey_public(public_id):
        survey = db.surveys.find_one({"public_id": public_id})
        if not survey:
            abort(404)
        if not survey_is_open(survey):
            return render_template("survey_closed.html", survey=survey), 410
        user = current_user()
        if not user and not survey.get("allow_anonymous", False):
            flash("此问卷要求登录后填写", "error")
            return redirect(url_for("login", next=request.path))
        return render_template("survey_fill.html", survey=survey, user=user)

    @app.post("/survey/<public_id>/submit")
    def survey_submit(public_id):
        survey = db.surveys.find_one({"public_id": public_id})
        if not survey:
            abort(404)
        if not survey_is_open(survey):
            return json_error("问卷未开放或已截止", 410) if request.is_json else (render_template("survey_closed.html", survey=survey), 410)
        user = current_user()
        if not user and not survey.get("allow_anonymous", False):
            return json_error("请先登录", 401) if request.is_json else redirect(url_for("login", next=url_for("survey_public", public_id=public_id)))
        payload = request.get_json(silent=True) if request.is_json else request.form.to_dict(flat=False)
        if not isinstance(payload, dict):
            return json_error("提交内容格式不正确")
        answers = payload.get("answers", payload)
        if not isinstance(answers, dict):
            return json_error("answers 必须是对象")
        # HTML forms use answer.<question_id> names; normalize them into the API structure.
        if not request.is_json:
            normalized = {}
            question_map = {q["id"]: q for q in survey.get("questions", [])}
            for key, value in answers.items():
                if key.startswith("answer."):
                    qid = key[len("answer."):]
                    question = question_map.get(qid, {})
                    if question.get("type") == "multiple":
                        normalized[qid] = value
                    else:
                        normalized[qid] = value[0] if value else ""
            answers = normalized
        try:
            validate_answers(survey.get("questions", []), answers)
        except ValidationError as exc:
            if request.is_json:
                return json_error(str(exc))
            flash(str(exc), "error")
            return redirect(url_for("survey_public", public_id=public_id))
        response = response_document(survey["_id"], public_id, user["_id"] if user and not survey.get("allow_anonymous") else (user["_id"] if user else None), answers)
        inserted = db.responses.insert_one(response)
        if request.is_json:
            return jsonify(message="提交成功", response_id=str(inserted.inserted_id)), 201
        return render_template("submit_success.html", survey=survey)

    @app.get("/surveys/<survey_id>/stats")
    @login_required
    def survey_stats(survey_id):
        survey = owner_survey(survey_id)
        responses = list(db.responses.find({"survey_id": survey["_id"]}).sort("submitted_at", -1))
        stats = calculate_statistics(survey, responses)
        return render_template("stats.html", survey=survey, stats=stats, responses=responses)

    # JSON API for automated testing and future extensions.
    @app.post("/api/surveys")
    @login_required
    def api_create_survey():
        payload = request.get_json(silent=True) or {}
        title = str(payload.get("title", "")).strip()
        if not title: return json_error("title 不能为空")
        doc = survey_document(current_user()["_id"], secrets.token_urlsafe(9), title, str(payload.get("description", "")), bool(payload.get("allow_anonymous", False)))
        result = db.surveys.insert_one(doc)
        return jsonify(id=str(result.inserted_id), public_id=doc["public_id"], status=doc["status"]), 201

    @app.put("/api/surveys/<survey_id>/questions")
    @login_required
    def api_save_questions(survey_id):
        survey = owner_survey(survey_id)
        if survey.get("status") != "draft": return json_error("已发布或已关闭问卷的题目已冻结；请复制为草稿再修改", 409)
        payload = request.get_json(silent=True) or {}
        try:
            questions = validate_questions(payload.get("questions"))
            preserve_valid_question_provenance(questions)
        except ValidationError as exc: return json_error(str(exc))
        db.surveys.update_one({"_id":survey["_id"]}, {"$set":{"questions":questions,"updated_at":utcnow()}})
        return jsonify(message="题目保存成功", question_count=len(questions))

    @app.post("/api/surveys/<survey_id>/publish")
    @login_required
    def api_publish(survey_id):
        survey = owner_survey(survey_id)
        if survey.get("status") == "closed": return json_error("已关闭问卷不能重新发布", 409)
        if survey.get("status") == "published":
            return jsonify(status="published", public_url=url_for("survey_public", public_id=survey["public_id"], _external=True))
        try: validate_questions(survey.get("questions", []))
        except ValidationError as exc: return json_error(str(exc))
        if not survey.get("questions"): return json_error("至少需要一道题目")
        db.surveys.update_one({"_id":survey["_id"]}, {"$set":{"status":"published","published_at":utcnow(),"updated_at":utcnow()}})
        return jsonify(status="published", public_url=url_for("survey_public", public_id=survey["public_id"], _external=True))

    @app.get("/api/surveys/<survey_id>/stats")
    @login_required
    def api_stats(survey_id):
        survey = owner_survey(survey_id)
        responses = list(db.responses.find({"survey_id":survey["_id"]}))
        stats = calculate_statistics(survey, responses)
        return jsonify(stats)

    # ---------------- Phase 2: reusable question bank and immutable versions ----------------
    @app.get("/question-bank")
    @login_required
    def question_bank_home():
        user = current_user()
        ensure_default_bank(user["_id"])
        banks = list(db.question_banks.find({"owner_id": user["_id"]}).sort("created_at", 1))
        for bank in banks:
            bank["item_count"] = db.question_bank_items.count_documents({"bank_id": bank["_id"]})
            bank["items"] = []
            for link in db.question_bank_items.find({"bank_id": bank["_id"]}):
                version_doc = db.question_versions.find_one({"_id": link["version_id"]})
                family_doc = db.question_families.find_one({"_id": link["family_id"]})
                if version_doc and family_doc and question_accessible(family_doc, user):
                    bank["items"].append({"version": version_doc, "family": family_doc})
        survey_id = request.args.get("survey_id", "")
        target_survey = None
        if survey_id:
            target_survey = owner_survey(survey_id)
            if target_survey.get("status") != "draft":
                flash("只能向草稿问卷添加题目；已发布问卷题目不可修改。", "error")
                target_survey = None
                survey_id = ""
        return render_template("question_bank.html", user=user, banks=banks,
                               entries=get_accessible_question_versions(user), survey_id=survey_id,
                               target_survey=target_survey)

    @app.post("/question-bank/new-bank")
    @login_required
    def question_bank_create_bank():
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        if not name or len(name) > 80:
            flash("题库名称不能为空且不能超过 80 个字符", "error")
            return redirect(url_for("question_bank_home"))
        try:
            db.question_banks.insert_one(question_bank_document(current_user()["_id"], name, description))
            flash("题库创建成功", "success")
        except DuplicateKeyError:
            flash("你已经有同名题库", "error")
        return redirect(url_for("question_bank_home"))

    @app.post("/question-bank/new-question")
    @login_required
    def question_bank_create_question():
        try:
            raw = json.loads(request.form.get("question_json", "{}"))
            definition = normalize_library_definition(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            flash(f"题目 JSON 不合法：{exc}", "error")
            return redirect(url_for("question_bank_home"))
        user_id = current_user()["_id"]
        family = question_family_document(user_id, definition["title"])
        family_result = db.question_families.insert_one(family)
        family_id = family_result.inserted_id
        version_doc = question_version_document(family_id, 1, None, user_id, definition, "初始版本")
        version_result = db.question_versions.insert_one(version_doc)
        db.question_families.update_one({"_id": family_id}, {"$set": {"root_version_id": version_result.inserted_id, "updated_at": utcnow()}})
        bank = ensure_default_bank(user_id)
        db.question_bank_items.insert_one({"bank_id": bank["_id"], "family_id": family_id, "version_id": version_result.inserted_id, "added_at": utcnow(), "added_by": user_id})
        flash("题目已保存到题库，版本 v1 已创建", "success")
        return redirect(url_for("question_family_detail", family_id=str(family_id)))

    @app.post("/question-bank/add-to-bank/<version_id>")
    @login_required
    def question_bank_add_item(version_id):
        try:
            version = db.question_versions.find_one({"_id": ObjectId(version_id)})
            bank_id = ObjectId(request.form.get("bank_id", ""))
        except Exception:
            version, bank_id = None, None
        if not version:
            abort(404)
        family = db.question_families.find_one({"_id": version["family_id"]})
        if not question_accessible(family, current_user()):
            abort(404)
        bank = db.question_banks.find_one({"_id": bank_id, "owner_id": current_user()["_id"]}) if bank_id else None
        if not bank:
            flash("请选择你自己管理的题库", "error")
            return redirect(url_for("question_bank_home"))
        try:
            db.question_bank_items.insert_one({"bank_id": bank["_id"], "family_id": family["_id"], "version_id": version["_id"], "added_at": utcnow(), "added_by": current_user()["_id"]})
            flash("题目版本已加入题库", "success")
        except DuplicateKeyError:
            flash("该版本已经在这个题库中", "info")
        return redirect(url_for("question_bank_home"))

    @app.post("/question-bank/banks/<bank_id>/remove/<version_id>")
    @login_required
    def question_bank_remove_item(bank_id, version_id):
        try:
            bank_oid, version_oid = ObjectId(bank_id), ObjectId(version_id)
        except Exception:
            abort(404)
        bank = db.question_banks.find_one({"_id": bank_oid, "owner_id": current_user()["_id"]})
        if not bank:
            abort(404)
        db.question_bank_items.delete_one({"bank_id": bank_oid, "version_id": version_oid})
        flash("已从该题库移除这个版本；不会删除题目本身或历史版本。", "success")
        return redirect(url_for("question_bank_home"))

    @app.post("/question-bank/banks/<bank_id>/delete")
    @login_required
    def question_bank_delete_bank(bank_id):
        try:
            bank_oid = ObjectId(bank_id)
        except Exception:
            abort(404)
        bank = db.question_banks.find_one({"_id": bank_oid, "owner_id": current_user()["_id"]})
        if not bank:
            abort(404)
        if bank.get("name") == "我的题库":
            flash("默认题库不能删除", "error")
            return redirect(url_for("question_bank_home"))
        db.question_bank_items.delete_many({"bank_id": bank_oid})
        db.question_banks.delete_one({"_id": bank_oid})
        flash("题库已删除；题目系列和其他题库中的条目不会删除。", "success")
        return redirect(url_for("question_bank_home"))

    @app.post("/question-bank/<family_id>/share")
    @login_required
    def question_family_share(family_id):
        family = question_family_or_404(family_id)
        if family.get("owner_id") != current_user()["_id"]:
            abort(404)
        username = request.form.get("username", "").strip()
        other = db.users.find_one({"username": username})
        if not other:
            flash("没有找到该用户名对应的用户", "error")
        elif other["_id"] == current_user()["_id"]:
            flash("不需要分享给自己", "error")
        else:
            db.question_families.update_one({"_id": family["_id"]}, {"$addToSet": {"share_with_ids": other["_id"]}, "$set": {"updated_at": utcnow()}})
            flash(f"题目已共享给用户 {username}；对方可以使用题目版本，但不能修改你的版本历史。", "success")
        return redirect(url_for("question_family_detail", family_id=family_id))

    @app.post("/question-bank/<family_id>/versions/<version_id>/new")
    @login_required
    def question_family_new_version(family_id, version_id):
        family = question_family_or_404(family_id)
        if family.get("owner_id") != current_user()["_id"]:
            abort(404)
        try:
            base = db.question_versions.find_one({"_id": ObjectId(version_id), "family_id": family["_id"]})
        except Exception:
            base = None
        if not base:
            abort(404)
        try:
            raw = json.loads(request.form.get("question_json", "{}"))
            definition = normalize_library_definition(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            flash(f"新版本内容不合法：{exc}", "error")
            return redirect(url_for("question_family_detail", family_id=family_id))
        latest = db.question_versions.find_one({"family_id": family["_id"]}, sort=[("version", -1)])
        number = (latest.get("version", 0) if latest else 0) + 1
        note = request.form.get("change_note", "修改题目")[:200]
        new_version = question_version_document(family["_id"], number, base["_id"], current_user()["_id"], definition, note)
        result = db.question_versions.insert_one(new_version)
        db.question_families.update_one({"_id": family["_id"]}, {"$set": {"title": definition["title"], "updated_at": utcnow()}})
        # Banks that contained an older version also receive the new version as a separate selectable item.
        prior_links = list(db.question_bank_items.find({"family_id": family["_id"]}))
        for link in prior_links:
            try:
                db.question_bank_items.insert_one({"bank_id": link["bank_id"], "family_id": family["_id"], "version_id": result.inserted_id, "added_at": utcnow(), "added_by": current_user()["_id"]})
            except DuplicateKeyError:
                pass
        flash(f"已创建不可变新版本 v{number}；旧版本仍保留。", "success")
        return redirect(url_for("question_family_detail", family_id=family_id))

    @app.post("/question-bank/<family_id>/restore/<version_id>")
    @login_required
    def question_family_restore(family_id, version_id):
        family = question_family_or_404(family_id)
        if family.get("owner_id") != current_user()["_id"]:
            abort(404)
        try:
            old = db.question_versions.find_one({"_id": ObjectId(version_id), "family_id": family["_id"]})
        except Exception:
            old = None
        if not old:
            abort(404)
        latest = db.question_versions.find_one({"family_id": family["_id"]}, sort=[("version", -1)])
        number = (latest.get("version", 0) if latest else 0) + 1
        doc = question_version_document(family["_id"], number, latest["_id"] if latest else None, current_user()["_id"], copy.deepcopy(old["definition"]), f"从 v{old['version']} 恢复")
        inserted = db.question_versions.insert_one(doc)
        db.question_families.update_one({"_id": family["_id"]}, {"$set": {"title": old["definition"]["title"], "updated_at": utcnow()}})
        for link in list(db.question_bank_items.find({"family_id": family["_id"]})):
            try:
                db.question_bank_items.insert_one({"bank_id": link["bank_id"], "family_id": family["_id"], "version_id": inserted.inserted_id, "added_at": utcnow(), "added_by": current_user()["_id"]})
            except DuplicateKeyError:
                pass
        flash(f"已将 v{old['version']} 的内容恢复为新版本 v{number}；历史版本没有被覆盖。", "success")
        return redirect(url_for("question_family_detail", family_id=family_id))

    @app.post("/surveys/<survey_id>/add-library-question/<version_id>")
    @login_required
    def survey_add_library_question(survey_id, version_id):
        survey = owner_survey(survey_id)
        if survey.get("status") != "draft":
            flash("已发布或已关闭问卷的题目不能修改", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        try:
            version = db.question_versions.find_one({"_id": ObjectId(version_id)})
        except Exception:
            version = None
        if not version:
            abort(404)
        family = db.question_families.find_one({"_id": version["family_id"]})
        if not question_accessible(family, current_user()):
            abort(404)
        definition = copy.deepcopy(version["definition"])
        question = {"id": "q_" + secrets.token_hex(5), **definition,
                    "jump_rules": [], "source_question_family_id": str(family["_id"]),
                    "source_question_version_id": str(version["_id"]),
                    "source_question_version": version["version"]}
        questions = copy.deepcopy(survey.get("questions", []))
        questions.append(question)
        try:
            validate_questions(questions)
        except ValidationError as exc:
            flash(f"题目无法加入当前问卷：{exc}", "error")
            return redirect(url_for("question_bank_home", survey_id=survey_id))
        db.surveys.update_one({"_id": survey["_id"]}, {"$set": {"questions": questions, "updated_at": utcnow()}})
        flash(f"已将题目 v{version['version']} 作为独立快照加入问卷。后续题库新版本不会修改该问卷中的题目。", "success")
        return redirect(url_for("survey_edit", survey_id=survey_id))

    @app.get("/question-bank/families/<family_id>")
    @login_required
    def question_family_detail(family_id):
        family = question_family_or_404(family_id)
        versions = list(db.question_versions.find({"family_id": family["_id"]}).sort("version", -1))
        for version_doc in versions:
            parent_doc = db.question_versions.find_one({"_id": version_doc.get("parent_version_id")}) if version_doc.get("parent_version_id") else None
            version_doc["parent_version_number"] = parent_doc.get("version") if parent_doc else None
        owner = family.get("owner_id") == current_user()["_id"]
        # A shared recipient can use the question, but sees statistics and usage only for their own surveys.
        stats_scope_owner = None if owner else current_user()["_id"]
        usage = question_family_usage(family["_id"], survey_owner_id=stats_scope_owner)
        stats, version_stats, _ = calculate_cross_survey_question_stats(family["_id"], survey_owner_id=stats_scope_owner)
        latest = versions[0] if versions else None
        survey_id = request.args.get("survey_id", "")
        if survey_id:
            target = owner_survey(survey_id)
            if target.get("status") != "draft":
                survey_id = ""
        latest_definition_json = json.dumps(latest.get("definition", {}) if latest else {}, ensure_ascii=False, indent=2)
        return render_template("question_family.html", family=family, versions=versions, owner=owner,
                               usage=usage, stats=stats, version_stats=version_stats, latest=latest,
                               latest_definition_json=latest_definition_json, survey_id=survey_id)

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"): return jsonify(error="资源不存在或无权访问"), 404
        return render_template("error.html", code=404, message="页面不存在，或你没有权限访问。"), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
