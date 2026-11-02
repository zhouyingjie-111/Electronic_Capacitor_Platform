"""InputData / OutputData 维护；CSV 读写不依赖数值计算库。"""

# 数据边界：只能操作 InputData/OutputData 下的文件；写入保持原编码与 CSV 结构。维护接口统一复用此类的路径检查。

import csv
import datetime
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from project_portal import DELTA_ROOT


class CSVDataManager:
    def __init__(self, base_dir=DELTA_ROOT):
        self.base_dir = str(Path(base_dir).resolve())
        self.data_dirs = {
            name: str(Path(self.base_dir) / name)
            for name in ("InputData", "OutputData")
        }

    def _managed_path(self, path):
        candidate = Path(path).resolve()
        if not any(
            candidate.is_relative_to(Path(root).resolve())
            and candidate != Path(root).resolve()
            for root in self.data_dirs.values()
        ):
            raise ValueError("仅可维护 InputData 和 OutputData 中的文件")
        return candidate

    def _iter_files(self):
        for name, directory in self.data_dirs.items():
            root = Path(directory)
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        try:
                            self._managed_path(path)
                        except ValueError:
                            continue
                        yield name, path

    def get_all_csv_files(self):
        return [
            {
                "name": path.name,
                "display_name": path.relative_to(self.base_dir).as_posix(),
                "path": str(path),
                "directory": name,
            }
            for name, path in self._iter_files()
            if path.suffix.lower() == ".csv"
        ]

    def get_csv_file_path(self, display_name):
        try:
            name = str(display_name).replace("\\", "/")
            if name.split("/", 1)[0] not in self.data_dirs:
                return None
            path = self._managed_path(Path(self.base_dir) / name)
            return str(path) if path.suffix.lower() == ".csv" else None
        except (ValueError, OSError):
            return None

    @staticmethod
    def _parse(text):
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, None)
        if not header or not any(value.strip() for value in header):
            raise ValueError("CSV文件无列")
        # 与原表格界面兼容，为空列名和重复列名生成唯一标识。
        columns, used = [], set()
        reserved = {value for value in header if value}
        for index, value in enumerate(header):
            base = value or f"Unnamed: {index}"
            column, suffix = base, 1
            while column in used or (column != value and column in reserved):
                column = f"{base}.{suffix}"
                suffix += 1
            columns.append(column)
            used.add(column)
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(
                    f"第 {reader.line_num} 行列数为 {len(row)}，表头列数为 {len(header)}"
                )
            rows.append(row)
        return header, columns, rows

    @classmethod
    def _read_bytes(cls, content):
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = content.decode(encoding)
            except UnicodeDecodeError:
                continue
            return (*cls._parse(text), encoding)
        raise ValueError("无法识别CSV编码，请使用 UTF-8 或 GBK")

    def _read(self, file_path):
        return self._read_bytes(self._managed_path(file_path).read_bytes())

    def _write(self, file_path, header, rows, encoding):
        path = self._managed_path(file_path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", newline="", encoding=encoding, dir=path.parent, delete=False
            ) as stream:
                temporary = stream.name
                writer = csv.writer(stream)
                writer.writerow(header)
                writer.writerows(rows)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _value(value):
        if value == "":
            return None
        try:
            number = float(value)
            if math.isfinite(number):
                return int(value) if value.lstrip("+-").isdigit() else number
            return None
        except ValueError:
            return value

    def get_csv_info(self, file_path):
        try:
            _, columns, rows, _ = self._read(file_path)
            dtypes = {}
            for index, column in enumerate(columns):
                values = [self._value(row[index]) for row in rows if row[index] != ""]
                dtypes[column] = (
                    "int64"
                    if values and all(type(v) is int for v in values)
                    else (
                        "float64"
                        if all(v is None or isinstance(v, (int, float)) for v in values)
                        else "object"
                    )
                )
            return {
                "columns": columns,
                "dtypes": dtypes,
                "shape": [len(rows), len(columns)],
            }
        except Exception as exc:
            return {"error": str(exc)}

    def get_csv_data(self, file_path, page=1, per_page=50, search_query=None):
        try:
            if page < 1 or per_page < 1:
                raise ValueError("页码和每页记录数必须大于0")
            _, columns, rows, _ = self._read(file_path)
            query = str(search_query or "").casefold()
            matches = [
                (i, row)
                for i, row in enumerate(rows)
                if not query or any(query in value.casefold() for value in row)
            ]
            total = len(matches)
            data = [
                {**dict(zip(columns, map(self._value, row))), "_index": i}
                for i, row in matches[(page - 1) * per_page : page * per_page]
            ]
            return {
                "data": data,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page,
            }
        except Exception as exc:
            return {"error": str(exc)}

    def _edit(self, file_path, action, index=None, data=None):
        try:
            header, columns, rows, encoding = self._read(file_path)
            if action != "add" and (index is None or index < 0 or index >= len(rows)):
                raise ValueError("记录不存在")
            if action == "delete":
                del rows[index]
            else:
                values = data or {}
                row = ["" for _ in columns] if action == "add" else rows[index]
                for col, key in enumerate(columns):
                    if key in values:
                        row[col] = "" if values[key] is None else str(values[key])
                if action == "add":
                    rows.append(row)
            self._write(file_path, header, rows, encoding)
            return {"success": True}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def update_record(self, file_path, index, data):
        return self._edit(file_path, "update", index, data)

    def delete_record(self, file_path, index):
        return self._edit(file_path, "delete", index)

    def add_record(self, file_path, data):
        return self._edit(file_path, "add", data=data)

    def import_csv(self, file_path, uploaded_file):
        try:
            header, columns, _, encoding = self._read(file_path)
            _, uploaded_columns, rows, _ = self._read_bytes(uploaded_file.read())
            if set(columns) != set(uploaded_columns):
                raise ValueError("数据格式不一致，请核对上传文件的列名")
            order = [uploaded_columns.index(column) for column in columns]
            reordered = [[row[index] for index in order] for row in rows]
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            shutil.copy2(file_path, str(file_path) + ".backup_" + stamp)
            self._write(file_path, header, reordered, encoding)
            return {"success": True, "rows_imported": len(rows)}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def get_file_stats(self):
        stats = {
            name: {
                "file_count": 0,
                "total_rows": 0,
                "total_size_kb": 0,
                "error_count": 0,
                "files": [],
            }
            for name in self.data_dirs
        }
        sizes = {name: 0 for name in self.data_dirs}
        for name, path in self._iter_files():
            if path.suffix.lower() != ".csv":
                continue
            entry = {
                "name": path.relative_to(self.data_dirs[name]).as_posix(),
                "rows": None,
                "size_kb": None,
                "modified": "—",
            }
            try:
                info = path.stat()
                sizes[name] += info.st_size
                entry.update(
                    size_kb=round(info.st_size / 1024, 2),
                    modified=datetime.datetime.fromtimestamp(info.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )
                _, _, rows, _ = self._read(path)
                entry["rows"] = len(rows)
                stats[name]["total_rows"] += len(rows)
            except Exception as exc:
                entry["error"] = str(exc)
                stats[name]["error_count"] += 1
            stats[name]["files"].append(entry)
            stats[name]["file_count"] += 1
        for name in stats:
            stats[name]["total_size_kb"] = round(sizes[name] / 1024, 2)
        return stats

    def scan_old_files(self, days):
        cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
        files = []
        for _, path in self._iter_files():
            info = path.stat()
            modified = datetime.datetime.fromtimestamp(info.st_mtime)
            if modified < cutoff:
                files.append(
                    {
                        "path": path.relative_to(self.base_dir).as_posix(),
                        "full_path": str(path),
                        "modified": modified.strftime("%Y-%m-%d %H:%M:%S"),
                        "size_kb": round(info.st_size / 1024, 2),
                        "size_mb": round(info.st_size / 1024**2, 2),
                    }
                )
        return sorted(files, key=lambda item: item["modified"])

    def delete_files(self, file_paths):
        results = {"success": [], "failed": []}
        for file_path in file_paths:
            try:
                self._managed_path(file_path).unlink()
                results["success"].append(file_path)
            except Exception as exc:
                results["failed"].append({"path": file_path, "error": str(exc)})
        return results

    def check_corrupted_files(self):
        problems = []
        for _, path in self._iter_files():
            try:
                if path.stat().st_size == 0:
                    raise ValueError("文件大小为0字节（空文件）")
                extension = path.suffix.lower()
                if extension == ".csv":
                    _, _, rows, _ = self._read(path)
                    if not rows:
                        raise ValueError("CSV文件无数据")
                elif extension == ".json":
                    json.loads(path.read_text(encoding="utf-8-sig"))
                elif extension in {".zip", ".npz"}:
                    with zipfile.ZipFile(path) as archive:
                        bad = archive.testzip()
                        if bad:
                            raise ValueError(f"压缩文件损坏: {bad}")
                elif extension in {".txt", ".md", ".py", ".m", ".html", ".bat"}:
                    try:
                        path.read_text(encoding="utf-8-sig")
                    except UnicodeDecodeError:
                        path.read_text(encoding="gb18030")
                else:
                    # 二进制模型只检查可读性，不因缺少模型运行库而判定损坏。
                    with path.open("rb") as stream:
                        stream.read(8)
            except Exception as exc:
                problems.append(
                    {
                        "path": path.relative_to(self.base_dir).as_posix(),
                        "full_path": str(path),
                        "type": path.suffix[1:].upper() or "未知",
                        "error": str(exc),
                    }
                )
        return problems
