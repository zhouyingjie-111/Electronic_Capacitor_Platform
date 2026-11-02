"""台达杯 2.0 页面、资源定位与旧入口迁移的回归检查。"""

import importlib.util
import io
import hashlib
import json
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.ids = set()
        self.fragments = []

    def handle_starttag(self, tag, attrs):
        self.urls.extend(
            value
            for key, value in attrs
            if key in {"href", "src"} and value and value.startswith("/")
        )
        self.ids.update(value for key, value in attrs if key == "id")
        self.fragments.extend(
            value[1:]
            for key, value in attrs
            if key == "href" and value and value.startswith("#")
        )


class ProjectPortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = []
        for key, path in [("release", ROOT / "webapp/app.py")]:
            spec = importlib.util.spec_from_file_location("portal_test_" + key, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            module.app.config["TESTING"] = True
            cls.apps.append((key, module))

    def logged_in(self, module):
        client = module.app.test_client()
        with client.session_transaction() as state:
            state["platform_entered"] = True
        return client

    def test_both_entry_points_use_the_same_three_cards(self):
        from project_portal import CARDS

        self.assertEqual(
            [card["key"] for card in CARDS], ["tracking", "capacitance", "chat"]
        )
        pages = []
        for key, module in self.apps:
            with self.subTest(entry=key):
                response = self.logged_in(module).get("/")
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertEqual(html.count('class="function-card '), 3)
                self.assertNotIn("/project/simulation", html)
                self.assertNotIn("/project/experiments", html)
                for old in ("交错并联Buck", "原理图/PCB自动设计", "控制器代码自动生成"):
                    self.assertNotIn(old, html)
                pages.append(html)
        self.assertTrue(pages)

    def test_every_local_page_link_and_image_exists(self):
        for key, module in self.apps:
            client = self.logged_in(module)
            for page in (
                "tracking",
                "tracking?section=algorithm",
                "tracking?section=simulation",
                "tracking?section=experiments",
                "capacitance",
                "capacitance?section=algorithm",
                "capacitance?section=simulation",
                "capacitance?section=experiments",
            ):
                with self.subTest(entry=key, page=page):
                    response = client.get("/project/" + page)
                    self.assertEqual(response.status_code, 200)
                    links = Links()
                    links.feed(response.get_data(as_text=True))
                    for fragment in links.fragments:
                        self.assertIn(fragment, links.ids, fragment)
                    for link in set(links.urls):
                        target = urlsplit(link)
                        path = target.path + (
                            "?" + target.query if target.query else ""
                        )
                        with client.head(path) as linked_response:
                            self.assertEqual(linked_response.status_code, 200, path)

    def test_old_design_urls_migrate_to_the_new_pages(self):
        for key, module in self.apps:
            client = self.logged_in(module)
            for task, page in [
                ("ppo", "tracking"),
                ("run_sim", "tracking?section=simulation#simulation-validation"),
                ("eda", "tracking?section=simulation#simulation-validation"),
                ("experiments", "tracking?section=experiments#tracking-results"),
            ]:
                with self.subTest(entry=key, task=task):
                    response = client.get("/run?task=" + task)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.headers["Location"], "/project/" + page)
            self.assertEqual(
                client.post("/api/run/start", json={"task": "ppo"}).status_code, 410
            )
            self.assertEqual(client.post("/api/eda/open_design").status_code, 410)

    def test_results_are_categorized_and_old_urls_keep_working(self):
        tracking_figures = {"simulation_pi.png", "simulation_ann.png"}
        capacitance_figures = {"capacitance_network.png"}
        for key, module in self.apps:
            client = self.logged_in(module)
            for page, expected, other in [
                ("tracking", tracking_figures, capacitance_figures),
                ("capacitance", capacitance_figures, tracking_figures),
            ]:
                with self.subTest(entry=key, page=page):
                    html = "".join(
                        client.get("/project/" + page + "?section=" + section).get_data(
                            as_text=True
                        )
                        for section in (
                            ("algorithm", "simulation", "experiments")
                            if page == "tracking"
                            else ("algorithm", "experiments")
                        )
                    )
                    for filename in expected:
                        self.assertIn("figures/" + filename, html)
                    for filename in other:
                        self.assertNotIn("figures/" + filename, html)
            for old_url, new_url in [
                (
                    "/project/simulation",
                    "/project/tracking?section=simulation#simulation-validation",
                ),
                (
                    "/project/experiments",
                    "/project/tracking?section=experiments#tracking-results",
                ),
                (
                    "/project/experiments?module=capacitance",
                    "/project/capacitance?section=experiments#capacitance-results",
                ),
            ]:
                response = client.get(old_url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.headers["Location"], new_url)

    def test_data_and_metrics_are_loaded_from_the_new_project(self):
        from project_portal import DELTA_ROOT, csv_preview, model_metrics, resource_path

        preview = csv_preview(resource_path("summary"), 8)
        self.assertEqual(len(preview["rows"]), min(8, preview["total"]))
        self.assertIn("D_T_UP_given", preview["columns"])
        self.assertTrue(all(preview["columns"]))
        self.assertEqual(len(model_metrics()), 4)
        for _, module in self.apps:
            for path in module.csv_data_manager.data_dirs.values():
                self.assertTrue(Path(path).is_relative_to(DELTA_ROOT))

    def test_sections_load_only_after_navigation_and_tracking_has_no_code_links(self):
        for key, module in self.apps:
            client = module.app.test_client()
            for page in ("tracking", "capacitance"):
                with self.subTest(entry=key, page=page):
                    default = client.get("/project/" + page).get_data(as_text=True)
                    self.assertIn('data-current-section="background"', default)
                    self.assertNotIn("figures/prototype_", default)
                    self.assertNotIn("figures/simulation_", default)
                    self.assertIn("section=experiments", default)
                    self.assertIn("section=background", default)
                    background = client.get(
                        "/project/" + page + "?section=background"
                    ).get_data(as_text=True)
                    self.assertIn('data-current-section="background"', background)
                    self.assertIn("figures/background_", background)
                    self.assertEqual(default, background)
                    algorithm_page = client.get(
                        "/project/" + page + "?section=algorithm"
                    ).get_data(as_text=True)
                    self.assertIn('data-current-section="algorithm"', algorithm_page)
                    self.assertNotIn("figures/background_", algorithm_page)
                    self.assertNotIn('id="model-results"', background)
                    self.assertNotIn('id="capacitance-results"', background)
                    for reference in (
                        "报告",
                        "PDF 第",
                        "#page=",
                        "5.决赛报告",
                        "台达杯 2.0",
                    ):
                        self.assertNotIn(reference, background)
                    experiment = client.get(
                        "/project/" + page + "?section=experiments"
                    ).get_data(as_text=True)
                    self.assertIn('data-current-section="experiments"', experiment)
                    self.assertIn('id="serial-communication"', experiment)
                    self.assertNotIn("figures/prototype_", experiment)
                    self.assertNotIn('id="model-results"', experiment)
                    self.assertNotIn('id="recognition-pipeline"', experiment)
                self.assertEqual(
                    client.get("/project/" + page + "?section=unknown").status_code, 404
                )
            for section in ("algorithm", "simulation", "experiments"):
                html = client.get("/project/tracking?section=" + section).get_data(
                    as_text=True
                )
                for removed in (
                    "DSsample",
                    "查看部署记录",
                    "训练代码",
                    "汇总代码",
                    "批量仿真代码",
                    "项目代码与部署文件",
                    "当前轻量化导出模型",
                ):
                    self.assertNotIn(removed, html)
                for resource in (
                    "training",
                    "preprocessing",
                    "acquisition",
                    "cpu_compile",
                    "inference",
                ):
                    self.assertNotIn("/project/resource/" + resource, html)
            algorithm = client.get("/project/tracking?section=algorithm").get_data(
                as_text=True
            )
            self.assertIn("展示前8行", algorithm)
            self.assertNotIn("下面展示前8行", algorithm)
            self.assertNotIn("完整数据保留", algorithm)
            self.assertEqual(algorithm.count('_dtgiven_scatter.svg" alt='), 4)
            self.assertEqual(algorithm.count('_dtgiven_line.svg" alt='), 4)
            self.assertNotIn("loss_curve", algorithm)
            simulation = client.get("/project/tracking?section=simulation").get_data(
                as_text=True
            )
            self.assertIn("figures/simulation_pi.png", simulation)
            self.assertNotIn("figures/pi_20a.png", simulation)
            self.assertNotIn('id="model-results"', simulation)
            model_links = Links()
            model_links.feed(simulation)
            self.assertEqual(
                sorted(
                    url
                    for url in model_links.urls
                    if url.startswith("/project/resource/")
                ),
                [],
            )
            self.assertIn('id="simulation-type"', simulation)
            self.assertIn('id="simulation-duration"', simulation)
            for control in ("pi", "ann", "custom"):
                self.assertIn('<option value="' + control + '">', simulation)
            self.assertIn(
                '<details class="panel simulation-reference" id="simulation-reference">',
                simulation,
            )
            self.assertNotIn('id="simulation-reference" open', simulation)
            # The assistant accepts SLX uploads; simulation still exposes no model-file links.
            for removed in ("对应文件位置", "查看 PI 仿真模型", "查看 ANN 仿真模型"):
                self.assertNotIn(removed, simulation)
            self.assertIn("自定义瞬态占空比参数仿真", simulation)
            self.assertNotIn("模型待提供", simulation)
            self.assertNotIn('id="simulation-actual"', simulation)
            self.assertNotIn('id="simulation-fit"', simulation)
            self.assertIn('id="scope-web-viewer"', simulation)
            self.assertIn('id="simulation-ann-code"', simulation)
            for name in ("P", "TP", "P_V", "I_V", "P_I", "I_I", "D_rise", "D_fall"):
                self.assertIn(f'data-simulation-parameter="{name}"', simulation)
            for removed in ("基准控制与数据采集", "瞬态占空比寻优", "COT 控制研究"):
                self.assertNotIn(removed, simulation)
            capacitance_simulation = client.get(
                "/project/capacitance?section=simulation"
            ).get_data(as_text=True)
            self.assertIn(
                'id="simulation-type" type="hidden" value="capacitance"',
                capacitance_simulation,
            )
            self.assertIn('data-module="capacitance"', capacitance_simulation)
            self.assertNotIn("figures/", capacitance_simulation)
            self.assertNotIn('id="capacitance-results"', capacitance_simulation)

    def test_simulation_api_rejects_unknown_models_and_hides_old_file_pages(self):
        for key, module in self.apps:
            client = module.app.test_client()
            for control in ("custom", "../../other.slx", 17):
                response = client.post(
                    "/api/project/simulation/start", json={"control": control}
                )
                self.assertIn(response.status_code, (400, 409))
            for body in ([], ["pi"], "pi"):
                self.assertEqual(
                    client.post("/api/project/simulation/start", json=body).status_code,
                    400,
                )
            self.assertEqual(
                client.get("/api/project/simulation/not-a-job").status_code, 404
            )
            self.assertEqual(
                client.post("/api/project/simulation/not-a-job/stop").status_code, 404
            )
            for resource in ("pi_model", "ann_model"):
                response = client.get("/project/resource/" + resource)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response.headers["Location"], "/project/tracking?section=simulation"
                )

    def test_simulation_api_forwards_duration_and_disables_diagram_caching(self):
        for key, module in self.apps:
            client = module.app.test_client()
            runner = module.app.extensions["project_simulations"]
            with patch.object(runner, "start", return_value={"id": "test"}) as start:
                self.assertEqual(
                    client.post(
                        "/api/project/simulation/start",
                        json={"control": "pi", "duration": 0.003},
                    ).status_code,
                    202,
                )
                start.assert_called_once_with("pi", 0.003, None, None)
            with patch.object(
                runner,
                "diagram",
                return_value=ROOT / "webapp/static/project/figures/simulation_pi.png",
            ):
                with client.get("/api/project/simulation/test/diagram") as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("no-store", response.headers["Cache-Control"])
                    self.assertNotIn("ETag", response.headers)

    def test_dtgiven_plots_match_saved_results_and_current_sources(self):
        from project_portal import MODEL_VERSIONS, resource_path, model_metrics

        data = json.loads(
            (ROOT / "webapp/tracking_plot_data.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            data["data_sha256"],
            hashlib.sha256(resource_path("summary").read_bytes()).hexdigest(),
        )
        for version in model_metrics():
            self.assertEqual(list(version["test_metrics"]), ["DTgiven"])
            key = version["key"]
            result = data["results"][key]
            weights = (
                resource_path("summary").parent
                / MODEL_VERSIONS[key][1]
                / "ann_up_down_5in2out_parameters.npz"
            )
            self.assertEqual(
                result["weights_sha256"],
                hashlib.sha256(weights.read_bytes()).hexdigest(),
            )
            actual, predicted = result["actual"], result["predicted"]
            errors = [p - a for a, p in zip(actual, predicted)]
            self.assertEqual(len(errors), version["test_metrics"]["DTgiven"]["samples"])
            self.assertAlmostEqual(
                sum(abs(error) for error in errors) / len(errors),
                version["test_metrics"]["DTgiven"]["mae"],
                places=6,
            )
            self.assertAlmostEqual(
                sum(error**2 for error in errors) / len(errors),
                version["test_metrics"]["DTgiven"]["mse"],
                places=6,
            )
            for chart in ("scatter", "line"):
                text = (
                    ROOT / f"webapp/static/project/tracking/{key}_dtgiven_{chart}.svg"
                ).read_text(encoding="utf-8")
                self.assertIn("DTgiven", text)
                self.assertNotIn("DSsample", text)

    def test_pdf_diagrams_keep_white_background_and_two_models_exist(self):
        from PIL import Image
        from project_portal import resource_path

        self.assertEqual(resource_path("ann_model").name, "DualBuckPPS_ANN.slx")
        self.assertEqual(resource_path("pi_model").name, "DualBuckPPS_PI.slx")
        self.assertEqual(resource_path("custom_model").name, "DualBuckPPS_define.slx")
        for filename in (
            "efficiency_setup.png",
            "simulation_pi.png",
            "simulation_ann.png",
            "background_tracking_challenge.png",
        ):
            with (
                self.subTest(image=filename),
                Image.open(ROOT / "webapp/static/project/figures" / filename) as image,
            ):
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                preview_path = (
                    ROOT
                    / "webapp/static/project/figures/previews"
                    / (Path(filename).stem + ".webp")
                )
                with Image.open(preview_path) as preview:
                    self.assertEqual(preview.size, image.size)
                    self.assertEqual(preview.convert("RGB").tobytes(), image.tobytes())

    def test_pages_are_independent_of_document_locations(self):
        for key, module in self.apps:
            client = self.logged_in(module)
            for page in ("tracking", "capacitance"):
                with self.subTest(entry=key, page=page):
                    html = client.get("/project/" + page).get_data(as_text=True)
                    for reference in (
                        "报告",
                        "PDF 第",
                        "#page=",
                        "/project/resource/report",
                    ):
                        self.assertNotIn(reference, html)
            self.assertEqual(client.get("/project/resource/report").status_code, 404)
            self.assertEqual(
                client.get("/project-static/figures/sources.json").status_code, 404
            )

    def test_source_code_opens_in_an_editor_instead_of_running(self):
        from project_portal import resource_path

        client = self.logged_in(self.apps[0][1])
        with (
            patch("project_portal.subprocess.Popen") as editor,
            patch("project_portal.os.startfile") as default_app,
        ):
            response = client.post("/api/project/open/training")
            self.assertEqual(response.status_code, 200)
            editor.assert_called_once_with(
                ["notepad.exe", str(resource_path("training"))]
            )
            default_app.assert_not_called()

    def test_welcome_entry_needs_no_identity_or_password(self):
        for key, module in self.apps:
            with self.subTest(entry=key):
                client = module.app.test_client()
                for page in ("/", "/role", "/welcome"):
                    response = client.get(page)
                    self.assertEqual(response.status_code, 200)
                    html = response.get_data(as_text=True)
                    self.assertIn("欢迎使用", html)
                    self.assertIn("进入平台", html)
                    for old in (
                        'type="password"',
                        "<select",
                        "选择身份",
                        "管理员",
                        "普通用户",
                    ):
                        self.assertNotIn(old, html)
                self.assertEqual(client.post("/enter").status_code, 302)
                main = client.get("/").get_data(as_text=True)
                self.assertEqual(main.count('class="function-card '), 3)
                self.assertNotIn("用户身份", main)
                self.assertNotIn("退出登录", main)
                # 旧的普通用户会话不会限制任何功能。
                with client.session_transaction() as state:
                    state["role"] = "user"
                self.assertEqual(client.get("/database_manager").status_code, 200)
                with (
                    patch.object(
                        module.csv_data_manager,
                        "get_csv_file_path",
                        return_value=str(ROOT / "webapp/app.py"),
                    ),
                    patch.object(
                        module.csv_data_manager,
                        "add_record",
                        return_value={"success": True},
                    ) as add,
                ):
                    response = client.post(
                        "/api/db/file/record",
                        json={"file": "InputData/sample.csv", "data": {"x": "1"}},
                    )
                    self.assertTrue(response.get_json()["success"])
                    add.assert_called_once()
                self.assertEqual(client.post("/logout").status_code, 302)
                self.assertIn("欢迎使用", client.get("/").get_data(as_text=True))
                self.assertTrue(
                    client.post(
                        "/api/role/set", json={"role": "user", "password": "ignored"}
                    ).get_json()["ok"]
                )

    def test_resource_allowlist_without_login(self):
        for _, module in self.apps:
            client = module.app.test_client()
            self.assertEqual(client.get("/project/experiments").status_code, 302)
            with patch("project_portal.subprocess.Popen") as editor:
                self.assertEqual(
                    client.post("/api/project/open/training").status_code, 200
                )
                editor.assert_called_once()
            self.assertEqual(client.get("/project/resource/unknown").status_code, 404)
            with client.get("/project/resource/summary?download=1") as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response.headers["Content-Disposition"])

    def test_training_upload_preview_and_file_validation_on_both_entries(self):
        from training_runner import REQUIRED_COLUMNS

        data = (
            ",".join(REQUIRED_COLUMNS)
            + "\n"
            + "\n".join(
                f"{18+i},5e-5,{70+i},190,{40+i},220,.1,.4,.2,.3" for i in range(6)
            )
        ).encode()
        for _, module in self.apps:
            client = module.app.test_client()
            uploaded = client.post(
                "/api/project/training/upload/dataset",
                data={"file": (io.BytesIO(data), "user-data.csv")},
            )
            self.assertEqual(uploaded.status_code, 200)
            token = uploaded.get_json()["token"]
            preview = client.get(
                "/api/project/training/preview", query_string={"dataset": token}
            ).get_json()
            self.assertEqual(preview["total"], 6)
            self.assertEqual(preview["rows"][0][1], "5e-5")
            invalid = client.post(
                "/api/project/training/upload/dataset",
                data={"file": (io.BytesIO(b"a,b\n1,2"), "bad.csv")},
            )
            self.assertEqual(invalid.status_code, 400)
            self.assertIn("缺少必要列", invalid.get_json()["error"])
            self.assertEqual(
                client.post(
                    "/api/project/training/start", json={"dataset": "../private.csv"}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/project/training/compile", json={"target": "arbitrary"}
                ).status_code,
                400,
            )
            self.assertEqual(
                client.post(
                    "/api/project/training/compile",
                    json={"target": "npu"},
                    headers={"Origin": "https://other.invalid"},
                ).status_code,
                403,
            )
            html = client.get("/project/tracking?section=algorithm").get_data(
                as_text=True
            )
            self.assertIn("tracking_network_40_60us.png", html)
            self.assertIn('id="ann-training-form"', html)
            self.assertIn(
                '<details class="panel simulation-reference" id="model-results">', html
            )
            self.assertNotIn("20–90 μs", html)


if __name__ == "__main__":
    unittest.main()
