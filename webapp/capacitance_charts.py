"""Small standalone SVG charts from the actual saved CNN output files."""

import csv
import html
import json
import math

from capacitance_data import OUTPUTS
from training_runner import TrainingError


def chart(folder, kind, target="C2_uF"):
    if kind not in ("loss", "scatter", "prediction") or target not in OUTPUTS:
        raise TrainingError("图表不存在。", 404)
    if not (
        folder / ("history.json" if kind == "loss" else "predictions.csv")
    ).is_file():
        raise TrainingError("图表尚未生成。", 404)
    series = []
    title = ""
    scatter = kind == "scatter"
    if kind == "loss":
        history = json.loads((folder / "history.json").read_text(encoding="utf-8"))
        series = [
            ("训练损失", [(i + 1, float(v)) for i, v in enumerate(history["loss"])]),
            (
                "验证损失",
                [(i + 1, float(v)) for i, v in enumerate(history["val_loss"])],
            ),
        ]
        title = "训练与验证损失"
        xlabel = "训练轮次"
        ylabel = "Huber loss"
    else:
        with (folder / "predictions.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r["split"] == "test"]
        actual = [float(r["true_" + target]) for r in rows]
        pred = [float(r["pred_" + target]) for r in rows]
        if not rows:
            raise TrainingError("尚无测试集数据。", 404)
        if scatter:
            series = [
                ("预测值", list(zip(actual, pred))),
                (
                    "参考线",
                    [
                        (min(actual + pred), min(actual + pred)),
                        (max(actual + pred), max(actual + pred)),
                    ],
                ),
            ]
            xlabel = "参考值"
            ylabel = "预测值"
            title = target + " · 测试窗口预测对比"
        else:
            series = [
                ("参考值", list(enumerate(actual, 1))),
                ("预测值", list(enumerate(pred, 1))),
            ]
            xlabel = "测试窗口"
            ylabel = target
            title = target + " · 逐窗口预测"
    points = [p for _, data in series for p in data if all(math.isfinite(v) for v in p)]
    if not points:
        raise TrainingError("没有有效绘图数据。", 404)
    xs, ys = zip(*points)
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmin == xmax:
        xmin -= max(1, abs(xmin) * 0.03)
        xmax += max(1, abs(xmax) * 0.03)
    if ymin == ymax:
        ymin -= max(0.01, abs(ymin) * 0.03)
        ymax += max(0.01, abs(ymax) * 0.03)
    margin = (ymax - ymin) * 0.08
    ymin -= margin
    ymax += margin

    def x(v):
        return 78 + 490 * (v - xmin) / (xmax - xmin)

    def y(v):
        return 247 - 177 * (v - ymin) / (ymax - ymin)

    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="320" viewBox="0 0 640 320"><rect width="640" height="320" fill="white"/>',
        '<g font-family="Times New Roman,SimSun,serif" fill="#3d4c65" font-size="13">',
        f'<text x="320" y="27" text-anchor="middle" font-size="17">{html.escape(title)}</text>',
    ]
    for i in range(5):
        xv = xmin + (xmax - xmin) * i / 4
        yv = ymin + (ymax - ymin) * i / 4
        out.extend(
            [
                f'<path d="M78 {y(yv):.2f}H568" stroke="#e8eef7"/><text x="69" y="{y(yv)+4:.2f}" text-anchor="end">{yv:.4g}</text>',
                f'<text x="{x(xv):.2f}" y="269" text-anchor="middle">{xv:.4g}</text>',
            ]
        )
    for index, (name, data) in enumerate(series):
        color = ("#3978c4", "#c38b42")[index % 2]
        if not scatter or index == 1:
            out.append(
                '<polyline fill="none" stroke="'
                + color
                + '" stroke-width="1.8" points="'
                + " ".join(f"{x(a):.2f},{y(b):.2f}" for a, b in data)
                + '"/>'
            )
        if kind != "loss" and (not scatter or index == 0):
            out.extend(
                f'<circle cx="{x(a):.2f}" cy="{y(b):.2f}" r="3.5" fill="{color}"/>'
                for a, b in data
            )
        out.append(f'<text x="{380+index*94}" y="49" fill="{color}">{name}</text>')
    out.extend(
        [
            f'<text x="320" y="303" text-anchor="middle">{html.escape(xlabel)}</text>',
            f'<text x="18" y="157" text-anchor="middle" transform="rotate(-90 18 157)">{html.escape(ylabel)}</text>',
            "</g></svg>",
        ]
    )
    return "".join(out)
