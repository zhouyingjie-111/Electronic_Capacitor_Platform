"""CSV maintenance routes; manager enforces the InputData/OutputData boundary."""

# 维护 API 只负责校验请求及组织响应，CSV 读写和路径授权交给 CSVDataManager；管理器通过 app.extensions 注入。

import os
from flask import jsonify, request, render_template, send_file, current_app, abort


def json_object():
    """写操作只接受 JSON 对象，防止数组/文本导致服务器内部异常。

    这里只校验请求结构；文件路径授权仍由 CSVDataManager 统一执行。
    """
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        abort(400, description="请提交 JSON 对象。")
    return value


def register_database_routes(app, csv_data_manager):
    app.extensions["csv_data_manager"] = csv_data_manager

    @app.route("/database_manager", methods=["GET"])
    def database_manager_page():
        """数据库维护管理页面"""
        return render_template("database_manager.html")

    # ==================== 数据库维护管理 API ====================

    @app.route("/api/db/files", methods=["GET"])
    def db_get_files():
        """获取所有CSV文件"""
        files = current_app.extensions["csv_data_manager"].get_all_csv_files()
        return jsonify({"success": True, "files": files})

    @app.route("/api/db/file/info", methods=["GET"])
    def db_get_file_structure():
        """获取文件结构"""
        display_name = request.args.get("file")
        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        info = current_app.extensions["csv_data_manager"].get_csv_info(file_path)
        if "error" in info:
            return jsonify({"success": False, "error": info["error"]})

        return jsonify({"success": True, **info})

    @app.route("/api/db/file/data", methods=["GET"])
    def db_get_file_records():
        """获取文件数据"""
        display_name = request.args.get("file")
        page = max(1, request.args.get("page", 1, type=int))
        per_page = max(1, min(500, request.args.get("per_page", 50, type=int)))
        search = request.args.get("search", None)

        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        result = current_app.extensions["csv_data_manager"].get_csv_data(
            file_path, page, per_page, search
        )

        if "error" in result:
            return jsonify({"success": False, "error": result["error"]})

        return jsonify({"success": True, **result})

    @app.route("/api/db/file/record", methods=["POST"])
    def db_create_record():
        """创建记录"""
        data = json_object()
        display_name = data.get("file")
        record_data = data.get("data")

        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        result = current_app.extensions["csv_data_manager"].add_record(
            file_path, record_data
        )
        return jsonify(result)

    @app.route("/api/db/file/record/<int:record_index>", methods=["PUT"])
    def db_update_record_route(record_index):
        """更新记录"""
        data = json_object()
        display_name = data.get("file")
        record_data = data.get("data")

        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        result = current_app.extensions["csv_data_manager"].update_record(
            file_path, record_index, record_data
        )
        return jsonify(result)

    @app.route("/api/db/file/record/<int:record_index>", methods=["DELETE"])
    def db_delete_record_route(record_index):
        """删除记录"""
        display_name = request.args.get("file")
        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        result = current_app.extensions["csv_data_manager"].delete_record(
            file_path, record_index
        )
        return jsonify(result)

    @app.route("/api/db/file/import", methods=["POST"])
    def db_import_csv():
        """导入CSV文件"""
        display_name = request.form.get("file")

        if "file" not in request.files:
            return jsonify({"success": False, "error": "没有文件上传"})

        uploaded_file = request.files["file"]
        if uploaded_file.filename == "":
            return jsonify({"success": False, "error": "没有选择文件"})

        if not uploaded_file.filename.endswith(".csv"):
            return jsonify({"success": False, "error": "只支持CSV文件"})

        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path:
            return jsonify({"success": False, "error": "文件路径无效"})

        result = current_app.extensions["csv_data_manager"].import_csv(
            file_path, uploaded_file
        )
        return jsonify(result)

    @app.route("/api/db/file/export", methods=["GET"])
    def db_export_csv():
        """导出CSV文件"""
        display_name = request.args.get("file")
        file_path = current_app.extensions["csv_data_manager"].get_csv_file_path(
            display_name
        )

        if not file_path or not os.path.exists(file_path):
            return jsonify({"success": False, "error": "文件不存在"})

        return send_file(
            file_path, as_attachment=True, download_name=display_name.replace("/", "_")
        )

    @app.route("/api/db/stats", methods=["GET"])
    def db_get_stats():
        """获取统计信息"""
        stats = current_app.extensions["csv_data_manager"].get_file_stats()
        return jsonify({"success": True, "stats": stats})

    @app.route("/api/db/cleanup/scan", methods=["POST"])
    def db_scan_old_files():
        """扫描旧文件"""
        data = json_object()
        period = data.get("period", "month")

        days_map = {"week": 7, "month": 30, "half_year": 180, "year": 365}

        days = days_map.get(period, 30)
        old_files = current_app.extensions["csv_data_manager"].scan_old_files(days)

        return jsonify({"success": True, "files": old_files, "count": len(old_files)})

    @app.route("/api/db/cleanup/delete", methods=["POST"])
    def db_delete_old_files():
        """删除旧文件"""
        data = json_object()
        file_paths = data.get("files", [])

        results = current_app.extensions["csv_data_manager"].delete_files(file_paths)

        return jsonify(
            {
                "success": True,
                "deleted_count": len(results["success"]),
                "failed_count": len(results["failed"]),
                "details": results,
            }
        )

    @app.route("/api/db/cleanup/check_corrupted", methods=["GET"])
    def db_check_corrupted():
        """检查损坏的文件"""
        corrupted = current_app.extensions["csv_data_manager"].check_corrupted_files()
        return jsonify({"success": True, "files": corrupted, "count": len(corrupted)})
