"""Phase 1 questionnaire system: Flask + MongoDB."""
import json
import os
import secrets
from datetime import datetime, timezone
from functools import wraps

from bson import ObjectId
from dotenv import load_dotenv
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_database, init_indexes
from logic import ValidationError, calculate_statistics, survey_is_open, validate_answers, validate_questions
from schema import response_document, survey_document, user_document, utcnow

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
        allow_anonymous = request.form.get("allow_anonymous") == "on"
        try:
            questions = json.loads(request.form.get("questions_json", "[]"))
            validate_questions(questions)
        except (json.JSONDecodeError, ValidationError) as exc:
            flash(f"题目配置不合法：{exc}", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        if not title:
            flash("问卷标题不能为空", "error")
            return redirect(url_for("survey_edit", survey_id=survey_id))
        db.surveys.update_one({"_id": survey["_id"]}, {"$set": {
            "title": title, "description": description, "allow_anonymous": allow_anonymous,
            "questions": questions, "updated_at": utcnow()
        }})
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
            question_map = {
                q["id"]: q for q in survey.get("questions", [])
            }

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
        payload = request.get_json(silent=True) or {}
        try: questions = validate_questions(payload.get("questions"))
        except ValidationError as exc: return json_error(str(exc))
        db.surveys.update_one({"_id":survey["_id"]}, {"$set":{"questions":questions,"updated_at":utcnow()}})
        return jsonify(message="题目保存成功", question_count=len(questions))

    @app.post("/api/surveys/<survey_id>/publish")
    @login_required
    def api_publish(survey_id):
        survey = owner_survey(survey_id)
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

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"): return jsonify(error="资源不存在或无权访问"), 404
        return render_template("error.html", code=404, message="页面不存在，或你没有权限访问。"), 404

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
