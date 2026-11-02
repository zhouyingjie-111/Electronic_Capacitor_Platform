# -*- coding: utf-8 -*-
"""手动调用训练脚本导出的NPZ ANN。
安装依赖：python -m pip install numpy
运行：python ann_predict_npz.py
或：python ann_predict_npz.py --model "D:/模型/ann_up_down_5in2out_parameters.npz"
输入单位必须与训练 CSV 一致；本脚本自动标准化并还原输出尺度。
"""
import argparse
from pathlib import Path
import re

import numpy as np

# 可填入你的 .npz 文件完整路径；留空时先查找脚本同目录，否则提示输入。
MODEL_PATH = str(Path(__file__).with_name("ann_up_down_5in2out_parameters.npz"))
INPUT_NAMES = ["P_peak", "T_rise_fall", "VC1", "VG1", "direction_state"]


class ANNPredictor:
    """模型只加载一次；predict 可接受一组 (5,) 或多组 (N,5) 数据。"""

    def __init__(self, model_path):
        self.model_path = Path(model_path).expanduser()
        self.layers = []
        # 使用纯数值 NPZ，不需要 h5py、PyTorch 或 ONNX Runtime。
        self.output_names = ["DTgiven", "DSsample"]
        with np.load(self.model_path, allow_pickle=False) as f:
            self.scaler = {}
            for name, length in [("x_mean", 4), ("x_std", 4), ("y_mean", 2), ("y_std", 2)]:
                arr = np.asarray(f[name], dtype=np.float32).reshape(-1)
                if arr.shape != (length,) or not np.isfinite(arr).all():
                    raise ValueError(f"标准化参数 {name} 无效。")
                if name.endswith("std") and np.any(arr <= 0):
                    raise ValueError(f"标准化参数 {name} 必须大于零。")
                self.scaler[name] = arr
            indices = sorted(int(key[1:]) for key in f.files if re.fullmatch(r"W[1-9][0-9]*", key))
            if not indices or indices != list(range(1, len(indices) + 1)):
                raise ValueError("模型权重必须按 W1、W2、... 连续编号。")
            width = 5
            self.dimensions = [width]
            for i in indices:
                w = np.asarray(f[f"W{i}"], dtype=np.float32)
                b = np.asarray(f[f"B{i}"], dtype=np.float32)
                if w.ndim != 2 or w.shape[1] != width or b.shape != (w.shape[0],):
                    raise ValueError(f"网络层 {i} 尺寸不匹配。")
                if not np.isfinite(w).all() or not np.isfinite(b).all():
                    raise ValueError(f"网络层 {i} 含有无效权重。")
                activation = "Linear" if i == indices[-1] else "ReLU"
                self.layers.append((w, b, activation))
                width = w.shape[0]
                self.dimensions.append(width)
            if width != 2:
                raise ValueError("模型必须具有两个输出。")

    def predict(self, raw_inputs):
        """顺序为 P_peak, T_rise_fall, VC1, VG1, direction_state。
        direction_state: 0=UP，1=DOWN。返回原始尺度的 DTgiven、DSsample。
        """
        x = np.asarray(raw_inputs, dtype=np.float32)
        single = x.ndim == 1
        if single:
            x = x.reshape(1, -1)
        if x.ndim != 2 or x.shape[1] != 5 or x.shape[0] == 0:
            raise ValueError("请输入 5 个数，或形状为 (N,5) 的数据。")
        if not np.isfinite(x).all():
            raise ValueError("输入不能包含 NaN、无穷大或超出 float32 范围的数值。")
        if not np.isin(x[:, 4], [0, 1]).all():
            raise ValueError("direction_state 只能为 0（UP）或 1（DOWN）。")
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            h = np.concatenate([
                (x[:, :4] - self.scaler["x_mean"]) / self.scaler["x_std"],
                (x[:, 4:5] >= 0.5).astype(np.float32),
            ], axis=1)
            for w, b, activation in self.layers:
                h = h @ w.T + b
                if activation == "ReLU":
                    h = np.maximum(h, np.float32(0))
            y = h * self.scaler["y_std"] + self.scaler["y_mean"]
        if not np.isfinite(y).all():
            raise ValueError("推理结果不是有限数，请检查输入范围和模型参数。")
        return y[0] if single else y


def main():
    parser = argparse.ArgumentParser(description="手动输入数据，调用 ANN NPZ 模型推理。")
    parser.add_argument("--model", default=MODEL_PATH, help="训练生成的 .npz 文件路径")
    args = parser.parse_args()
    print("ANN 推理程序已启动（仅使用 NumPy）。", flush=True)
    path = args.model
    if not path:
        nearby = Path(__file__).resolve().parent / "ann_up_down_5in2out_parameters.npz"
        path = str(nearby) if nearby.is_file() else input("请输入 .npz 模型完整路径：").strip()
    path = path.strip().strip('"').strip("'")
    try:
        predictor = ANNPredictor(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"模型加载失败：{exc}")
        return 1
    print("模型加载成功，网络结构：" + " → ".join(map(str, predictor.dimensions)))
    print("输入顺序：P_peak, T_rise_fall, VC1, VG1, direction_state")
    print("方向：0=UP（使用 VC1_UP、VG1_UP）；1=DOWN（使用 VC1_DOWN、VG1_DOWN）")
    print("输入单位必须与训练 CSV 相同；无需自行标准化。")
    print("每行输入 5 个值，用空格或逗号分隔；输入 q 退出。")
    while True:
        try:
            line = input("\n输入数据：").strip()
            if line.lower() in {"q", "quit", "exit"}:
                break
            if not line:
                continue
            values = [float(v) for v in re.split(r"[,，\s]+", line)]
            prediction = predictor.predict(values)
            for name, value in zip(predictor.output_names, prediction):
                print(f"{name} = {float(value):.10g}")
        except (ValueError, FloatingPointError) as exc:
            print(f"输入或计算错误：{exc}，请重新输入。")
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt):
        print("\n已退出。")
