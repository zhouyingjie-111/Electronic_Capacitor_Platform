"""Fast release smoke checks; no API keys, serial port, MATLAB or training."""

from pathlib import Path
from urllib.parse import urlsplit
from html.parser import HTMLParser


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if (
                key in ("src", "href")
                and value
                and value.startswith("/project-static/")
            ):
                self.urls.add(value)


def smoke_check(app):
    from project_portal import DELTA_ROOT

    required = [
        "OutputData/仿真结果汇总.csv",
        "InputData/capacitance_scope",
        "Code/python/ANN_5IN2OUT_ultralight.py",
        "Code/python/scope_cap/train.py",
        "Simulink/DualBuckPPS_PI.slx",
        "Simulink/DualBuckPPS_ANN.slx",
        "Simulink/DualBuckPPS_define.slx",
        "Simulink/DualBuckPPS_CNN.slx",
    ]
    for name in required:
        assert (DELTA_ROOT / name).exists(), f"缺少资源：{name}"
    assets = Assets()
    with app.test_client() as client:
        assert client.get("/health").json["status"] == "ok"
        assert client.post("/enter").status_code == 302
        paths = ["/", "/welcome", "/database_manager", "/project/chat"]
        paths += [
            f"/project/{module}?section={section}"
            for module in ("tracking", "capacitance")
            for section in ("background", "algorithm", "simulation", "experiments")
        ]
        for path in paths:
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
            assets.feed(response.get_data(as_text=True))
        for url in sorted(assets.urls):
            assert client.get(url).status_code == 200, f"缺少静态文件：{url}"
        assert client.get("/api/db/stats").status_code == 200
        assert client.get("/api/project/serial/status").json["connected"] is False
        assert (
            client.get(
                "/api/project/serial/status", environ_base={"REMOTE_ADDR": "192.0.2.1"}
            ).status_code
            == 403
        )
    print(
        f"检查通过：{len(paths)} 个页面、{len(assets.urls)} 个静态资源、默认数据及本机接口边界。"
    )
