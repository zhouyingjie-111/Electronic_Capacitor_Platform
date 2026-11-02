import io
from pathlib import Path
from urllib.parse import urlsplit

from flask import (
    Response,
    jsonify,
    render_template,
    request,
    send_file,
    stream_with_context,
)

from ai_providers import AIError
from ai_service import AIService
from ai_history import LazyLocalHistory

COOKIE = "pe_ai_sid"


def register_ai_routes(app, portal, simulations, serial_service):
    history = app.config.get("AI_HISTORY_STORE") or LazyLocalHistory(
        app.config.get(
            "AI_HISTORY_ROOT", Path(__file__).resolve().parent / "runtime/ai_history"
        )
    )
    service = AIService(simulations, serial_service, store=history)
    app.extensions["project_ai"] = service

    @portal.errorhandler(AIError)
    def ai_error(error):
        return jsonify({"error": str(error)}), error.status

    @portal.before_request
    def guard_ai():
        if not request.path.startswith("/api/project/ai/"):
            return
        if request.remote_addr not in ("127.0.0.1", "::1"):
            raise AIError("智能对话仅支持本机访问。", 403)
        origin = request.headers.get("Origin")
        try:
            if origin and (
                urlsplit(origin).netloc != request.host
                or urlsplit(origin).scheme not in ("http", "https")
            ):
                raise AIError("不允许跨站访问智能对话。", 403)
        except ValueError:
            raise AIError("请求来源不正确。", 403) from None
        limit = (
            64 * 1024**2
            if request.path.endswith("/import-legacy")
            else 9 * 1024**2 if request.path.endswith("/upload") else 1024**2
        )
        if request.content_length and request.content_length > limit:
            raise AIError("请求过大，请拆分附件或内容。", 413)
        if (
            request.method in ("POST", "DELETE")
            and not request.path.endswith("/upload")
            and not request.is_json
        ):
            raise AIError("请使用有效的对话请求。", 415)

    def sid():
        return request.cookies.get(COOKIE, "")

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise AIError("请求格式不正确。")
        return value

    def response(value):
        result = jsonify(value)
        result.headers["Cache-Control"] = "no-store"
        return result

    @portal.get("/project/chat")
    def ai_page():
        return render_template("project/chat.html", page="chat", ai_scope="general")

    @portal.get("/api/project/ai/status")
    def ai_status():
        return response(service.status(sid(), request.args.get("scope", "general")))

    @portal.post("/api/project/ai/connect")
    def ai_connect():
        value = data()
        new_sid, result = service.connect(
            sid(), value.get("provider"), value.get("key"), value.get("connection")
        )
        result = response(result)
        result.set_cookie(
            COOKIE,
            new_sid,
            httponly=True,
            samesite="Strict",
            secure=request.is_secure,
            path="/api/project/ai",
        )
        return result

    @portal.post("/api/project/ai/select")
    def ai_select():
        value = data()
        return response(
            service.select(sid(), value.get("model"), value.get("capabilities"))
        )

    @portal.post("/api/project/ai/models/refresh")
    def ai_refresh_models():
        data()
        return response(service.refresh_models(sid()))

    @portal.post("/api/project/ai/disconnect")
    def ai_disconnect():
        data()
        service.disconnect(sid())
        result = response({"ok": True})
        result.delete_cookie(COOKIE, path="/api/project/ai")
        return result

    @portal.post("/api/project/ai/threads")
    def ai_new_thread():
        return response(service.new_thread(sid(), data().get("scope")))

    @portal.get("/api/project/ai/threads/<thread_id>")
    def ai_get_thread(thread_id):
        return response(service.thread(sid(), thread_id))

    @portal.delete("/api/project/ai/threads/<thread_id>")
    def ai_clear_thread(thread_id):
        data()
        service.clear_thread(sid(), thread_id)
        return response({"ok": True})

    @portal.post("/api/project/ai/upload")
    def ai_upload():
        return response(service.upload(sid(), request.files.get("file")))

    @portal.get("/api/project/ai/attachments/<item_id>")
    def ai_attachment(item_id):
        item = service.attachment(sid(), item_id)
        result = send_file(
            io.BytesIO(item["raw"]),
            download_name=item["name"],
            mimetype=item.get("mime", "application/octet-stream"),
            as_attachment=item["kind"] != "image",
        )
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return result

    @portal.delete("/api/project/ai/attachments/<item_id>")
    def ai_delete_attachment(item_id):
        data()
        service.remove_attachment(sid(), item_id)
        return response({"ok": True})

    @portal.post("/api/project/ai/messages")
    def ai_message():
        return response(service.send(sid(), data()))

    @portal.post("/api/project/ai/import-legacy")
    def ai_import_legacy():
        return response(service.import_legacy(sid(), data()))

    @portal.get("/api/project/ai/files/<item_id>/download")
    def ai_download(item_id):
        item = service.generated_file(sid(), item_id)
        result = send_file(
            io.BytesIO(item["raw"]),
            download_name=item["name"],
            mimetype=item["mime"],
            as_attachment=True,
        )
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        result.headers["X-Content-SHA256"] = item["sha256"]
        return result

    @portal.post("/api/project/ai/threads/<thread_id>/messages/<message_id>/file")
    def ai_export_answer(thread_id, message_id):
        return response(
            service.export_answer(sid(), thread_id, message_id, data().get("format"))
        )

    @portal.get("/api/project/ai/jobs/<job_id>/events")
    def ai_events(job_id):
        job = service.job(sid(), job_id)
        after = request.args.get("after", 0, type=int)
        result = Response(
            stream_with_context(service.events(job, after)),
            mimetype="text/event-stream",
        )
        result.headers["Cache-Control"] = "no-store"
        result.headers["X-Accel-Buffering"] = "no"
        return result

    @portal.post("/api/project/ai/jobs/<job_id>/stop")
    def ai_stop(job_id):
        data()
        service.stop(sid(), job_id)
        return response({"ok": True})

    @portal.post("/api/project/ai/drafts/<draft_id>/run")
    def ai_run(draft_id):
        data()
        return response(service.execute_draft(sid(), draft_id))

    @portal.get("/api/project/ai/simulations/<job_id>")
    def ai_simulation(job_id):
        return response(service.software_task(sid(), job_id))

    return service
