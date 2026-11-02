# -*- coding: utf-8 -*-
"""
UP/DOWN 统一 ANN 回归模型 —— F28P550SJ9PZ CPU/DSP 部署版（不使用 NPU）。

模型输入：P_peak、T_rise_fall、VC1、VG1、direction_state
模型输出：DTgiven、DSsample

保留功能：
    1. 按原始工况划分 train / val / test；
    2. 验证集选模、Early Stopping、AdamW 正则化；
    3. 终端输出 DTgiven、DSsample 的 MAE、MSE、R^2 和百分比预测误差；
    4. 输出一个便携式 HDF5 模型文件（.h5）；
    5. 仅生成三张必要图片和一个精简指标 JSON。

默认输出文件：
    1. loss_curve.png
    2. test_prediction_comparison.png
    3. test_true_vs_predicted.png
    4. ann_up_down_5in2out_high_accuracy.h5
    5. metrics_summary.json

依赖：
    pip install numpy pandas matplotlib torch h5py onnx onnxruntime
"""

import os
from pathlib import Path
import io
import json
import random
import warnings
import contextlib
import tempfile
import shutil
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import h5py

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader


# ============================================================
# 1. 用户配置区
# ============================================================

CSV_FILE_PATH = os.environ.get(
    "ANN_CSV_PATH",
    str(Path(__file__).resolve().parents[2] / "OutputData/仿真结果汇总.csv"),
)
OUTPUT_DIR = os.environ.get(
    "ANN_OUTPUT_DIR",
    str(
        Path(__file__).resolve().parents[2]
        / "OutputData/ann_up_down_5in2out_retrained_ultralight"
    ),
)
MODEL_NAME = os.environ.get("ANN_MODEL_NAME", "ann_up_down_5in2out")

# UP 输入输出列
UP_INPUT_COLUMNS = ["P_peak", "T_rise_fall", "VC1_UP", "VG1_UP"]
UP_TARGET_COLUMNS = ["D_T_UP_given", "PWM_S_UP_first"]

# DOWN 输入输出列
DOWN_INPUT_COLUMNS = ["P_peak", "T_rise_fall", "VC1_DOWN", "VG1_DOWN"]
DOWN_TARGET_COLUMNS = ["D_T_DOWN_given", "PWM_S_DOWN_first"]

# 模型统一后的输入输出顺序
MODEL_INPUT_COLUMNS = ["P_peak", "T_rise_fall", "VC1", "VG1", "direction_state"]
MODEL_OUTPUT_COLUMNS = ["D_T_given", "PWM_S_first"]

# direction_state 定义
DIRECTION_UP = 0.0
DIRECTION_DOWN = 1.0


# ============================================================
# 2. 数据集划分参数
# ============================================================

TRAIN_RATIO = 0.70
VAL_RATIO = 0.15
TEST_RATIO = 0.15

# 按原始工况划分后，再展开为 UP/DOWN，防止同一原始工况泄漏到不同数据集。
SPLIT_BY_RAW_ROW = True

# 在划分之前先合并完全相同的原始输入工况，进一步避免重复工况跨集合泄漏。
# mean  : 相同原始输入工况的目标值取平均
# first : 只保留第一条
# none  : 不进行合并
RAW_DUPLICATE_MODE = "mean"
RAW_INPUT_KEY_COLUMNS = [
    "P_peak",
    "T_rise_fall",
    "VC1_UP",
    "VG1_UP",
    "VC1_DOWN",
    "VG1_DOWN",
]

# 展开为统一 UP/DOWN 数据后，对完全相同的统一模型输入再次处理重复项。
DUPLICATE_MODE = "mean"

# 验证集用于学习率调度、最佳模型选择和提前停止；测试集仅用于最终评价。
USE_VAL_FOR_SCHEDULER_AND_BEST = True


# ============================================================
# 3. 防过拟合训练参数
# ============================================================

# 15000 为每次训练的最大轮数；验证集 Early Stopping 会自动提前结束。
EPOCHS = 15000
LEARNING_RATE = 8e-4
BATCH_SIZE = 64
RANDOM_SEED = 42

# 使用多个随机初始点训练，并仅依据验证集误差选择最终模型。
# 这可以降低单次随机初始化不理想造成的预测误差，同时不使用测试集选模。
TRAINING_SEEDS = (42, 142, 242)

# 再次轻量化：5 -> 8 -> 4 -> 2，两个隐藏层，保持 float32。
# 参数量 94，全连接 MAC 80；相比上一版 1810/1728 大幅减小。
# 目标是降低板上延迟，不代表已达到100us，也不保证原预测精度。
# 数据划分、损失、Dropout、多种子选模及其他训练超参数不变。
# 精度需用相同数据划分与原模型对比，不能仅由结构保证。
HIDDEN_DIMS = (8, 4)
DROPOUT = 0.05

# AdamW 的 L2 权重正则化。
WEIGHT_DECAY = 1e-4

# 验证集提前停止参数。
EARLY_STOPPING_PATIENCE = 700
EARLY_STOPPING_MIN_DELTA = 1e-8

# 验证集学习率调度参数。
SCHEDULER_FACTOR = 0.5
SCHEDULER_PATIENCE = 200
MIN_LEARNING_RATE = 5e-8

# 默认关闭只针对训练集继续压低误差的 LBFGS，避免再次过拟合。
# 若以后需要开启，代码会仅在验证集误差改善时接受 LBFGS 结果。
USE_LBFGS_FINE_TUNE = False
LBFGS_MAX_ITER = 300
LBFGS_LEARNING_RATE = 0.3

PRINT_EVERY = 250
SAVE_H5_MODEL = True
SAVE_JSON_SUMMARY = True

# True：运行前清除该专用输出目录中的旧版模型输出，避免历史文件继续堆积。
CLEAN_OUTPUT_DIR_BEFORE_RUN = True

# ============================================================
# 3.1 模型与 DSP 部署文件导出开关（不改变训练过程）
# ============================================================
SAVE_PYTORCH_CHECKPOINT = True
SAVE_STATE_DICT = True
SAVE_TORCHSCRIPT_MODEL = True
SAVE_ONNX_MODEL = True
SAVE_NUMPY_PARAMETERS = True
SAVE_INFERENCE_CODE = True
SAVE_DSP_DEPLOYMENT_FILES = True
SAVE_DEPLOYMENT_MANIFEST = True

ONNX_OPSET_VERSION = 17
# 额外导出 batch=1 的完整模型，供 TI MCU NNC 编译。
SAVE_STATIC_FULL_ONNX = True
# 仅优化固定batch部署ONNX；训练/其他导出仍使用原标准化流程。
FOLD_SCALER_IN_STATIC_ONNX = True
VERIFY_ONNX_WITH_RUNTIME = True
ONNX_VERIFY_ATOL = 2e-5
ONNX_VERIFY_RTOL = 2e-5
DSP_TEST_VECTOR_COUNT = 8

# ANN 直接使用 5 个模型输入：
#   4 个连续输入的训练集标准化值 + direction_state
# 隐藏层负责学习非线性关系，不再额外构造 45 维二次项和交叉项。
FEATURE_DIM = 5

# 绘图字体设置：新罗马字体，五号=10.5 pt
PLOT_FONT_FAMILY = "Times New Roman"
PLOT_FONT_SIZE = 10.5


# ============================================================
# 4. 固定随机种子与路径
# ============================================================


def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def get_csv_path() -> str:
    csv_path = CSV_FILE_PATH.strip().strip('"').strip("'")
    csv_path = os.path.abspath(csv_path)
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    return csv_path


def get_output_dir(csv_path: str) -> str:
    if OUTPUT_DIR.strip() == "":
        out_dir = os.path.join(os.path.dirname(csv_path), "ann_up_down_5in2out_split")
    else:
        out_dir = OUTPUT_DIR.strip().strip('"').strip("'")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def ensure_output_dir(output_dir: str) -> str:
    """
    确保模型输出目录在真正写文件前存在。

    说明：训练可能持续较长时间。Windows 下如果输出目录在训练期间被手工删除、
    被同步/清理软件移走，或者目录尚未创建完成，TorchScript/ONNX 的底层保存接口
    不会自动创建父目录，会直接报 "Parent directory ... does not exist"。

    该函数只处理文件输出路径，不参与训练、数据划分、标准化、网络结构、优化器、
    Early Stopping 或模型选择，因此不会改变任何训练结果。
    """
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    if not os.path.isdir(output_dir):
        raise RuntimeError(f"Failed to create output directory: {output_dir}")
    return output_dir


def save_via_ascii_temp(writer, final_path: str, suffix: str) -> str:
    """
    先让 PyTorch/ONNX 写入仅含 ASCII 的系统临时路径，再由 Python 复制到最终路径。

    这是针对 Windows 下 PyTorch C++ 保存接口在中文/非 ASCII 路径上可能错误报告
    "Parent directory ... does not exist" 的兼容处理。
    只改变文件保存方式，不改变训练、模型参数或推理数学关系。
    """
    final_path = os.path.abspath(final_path)
    final_dir = ensure_output_dir(os.path.dirname(final_path))
    final_path = os.path.join(final_dir, os.path.basename(final_path))

    fd, temp_path = tempfile.mkstemp(prefix="ann_export_", suffix=suffix)
    os.close(fd)
    try:
        # 某些导出器要求目标文件不存在。
        try:
            os.remove(temp_path)
        except OSError:
            pass

        writer(temp_path)

        if not os.path.isfile(temp_path):
            raise RuntimeError(f"Temporary export file was not created: {temp_path}")

        # 复制前再次确认目标目录存在。
        ensure_output_dir(final_dir)
        shutil.copyfile(temp_path, final_path)

        if not os.path.isfile(final_path):
            raise RuntimeError(f"Exported file was not created: {final_path}")
        return final_path
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def clean_previous_outputs(output_dir: str) -> None:
    """只清理本模型历史生成物，避免旧图片和旧导出文件继续堆积。"""
    removable_names = {
        "test_metrics_" + "_".join(str(v) for v in HIDDEN_DIMS) + ".txt",
        "metrics_summary.json",
        "deployment_manifest.json",
        "requirements_deployment.txt",
        "ann_predict_python.py",
        "ann_predict_matlab.m",
        "ann_predict_up_matlab.m",
        "ann_predict_down_matlab.m",
        "ann_inference.c",
        "ann_inference.h",
        "ann_test_vectors.h",
        "ann_dsp_self_test.c",
        "ann_dsp_self_test.h",
        "ann_dsp_integration_example.c",
        "DSP_DEPLOYMENT_README.txt",
    }

    for file_name in os.listdir(output_dir):
        file_path = os.path.join(output_dir, file_name)
        if not os.path.isfile(file_path):
            continue

        is_old_image = file_name.lower().endswith(".png")
        is_old_model = file_name.startswith(MODEL_NAME) and file_name.lower().endswith(
            (".h5", ".pth", ".pt", ".onnx", ".npz")
        )
        is_known_export = file_name in removable_names

        if is_old_image or is_old_model or is_known_export:
            try:
                os.remove(file_path)
            except OSError:
                pass


# ============================================================
# 5. 数据读取、清洗、划分与展开
# ============================================================


def read_csv_auto_encoding(csv_path: str) -> pd.DataFrame:
    last_error = None
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb2312"]:
        try:
            df = pd.read_csv(csv_path, encoding=enc)
            df.columns = [str(c).strip() for c in df.columns]
            print(f"CSV loaded successfully. Encoding = {enc}")
            return df
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Failed to read CSV. Last error: {last_error}")


def required_columns() -> List[str]:
    return list(
        dict.fromkeys(
            UP_INPUT_COLUMNS
            + UP_TARGET_COLUMNS
            + DOWN_INPUT_COLUMNS
            + DOWN_TARGET_COLUMNS
        )
    )


def check_required_columns(df: pd.DataFrame) -> None:
    missing = [c for c in required_columns() if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")


def clean_raw_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    check_required_columns(raw_df)
    df = raw_df.copy()
    req = required_columns()

    for col in req:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=req).reset_index(drop=True)
    df["raw_case_id"] = np.arange(len(df), dtype=np.int32)

    print(f"Valid raw rows after numeric conversion and dropna: {len(df)} / {before}")
    return df


def merge_duplicate_raw_cases(df: pd.DataFrame) -> pd.DataFrame:
    """
    在 train/val/test 划分之前合并完全相同的原始输入工况。

    这样可以避免同一输入工况的重复记录被随机分配到训练集和测试集，
    从而造成数据泄漏和过于乐观的泛化结果。
    """
    if RAW_DUPLICATE_MODE == "none":
        result = df.copy().reset_index(drop=True)
    elif RAW_DUPLICATE_MODE == "first":
        result = df.drop_duplicates(
            subset=RAW_INPUT_KEY_COLUMNS, keep="first"
        ).reset_index(drop=True)
    elif RAW_DUPLICATE_MODE == "mean":
        target_columns = list(dict.fromkeys(UP_TARGET_COLUMNS + DOWN_TARGET_COLUMNS))
        result = (
            df.groupby(RAW_INPUT_KEY_COLUMNS, as_index=False, dropna=False)[
                target_columns
            ]
            .mean()
            .reset_index(drop=True)
        )
    else:
        raise ValueError("RAW_DUPLICATE_MODE must be 'mean', 'first', or 'none'.")

    result["raw_case_id"] = np.arange(len(result), dtype=np.int32)
    print(
        f"Raw duplicate handling ({RAW_DUPLICATE_MODE}): " f"{len(df)} -> {len(result)}"
    )
    return result


def split_raw_dataframe(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, List[int]]]:
    if abs(TRAIN_RATIO + VAL_RATIO + TEST_RATIO - 1.0) > 1e-8:
        raise ValueError("TRAIN_RATIO + VAL_RATIO + TEST_RATIO must equal 1.0")

    n = len(df)
    if n < 5:
        raise ValueError(
            "Dataset is too small for train/val/test split. Need at least 5 raw rows."
        )

    rng = np.random.default_rng(RANDOM_SEED)
    perm = rng.permutation(n)

    n_train = int(round(n * TRAIN_RATIO))
    n_val = int(round(n * VAL_RATIO))
    n_test = n - n_train - n_val

    # 保证每个集合至少有 1 个原始样本
    if n_val <= 0:
        n_val = 1
        n_train -= 1
    if n_test <= 0:
        n_test = 1
        n_train -= 1
    if n_train <= 0:
        raise ValueError("Train set is empty. Please adjust split ratios.")

    train_idx = perm[:n_train]
    val_idx = perm[n_train : n_train + n_val]
    test_idx = perm[n_train + n_val :]

    train_raw = df.iloc[train_idx].reset_index(drop=True)
    val_raw = df.iloc[val_idx].reset_index(drop=True)
    test_raw = df.iloc[test_idx].reset_index(drop=True)

    split_info = {
        "train_raw_case_id": train_raw["raw_case_id"].astype(int).tolist(),
        "val_raw_case_id": val_raw["raw_case_id"].astype(int).tolist(),
        "test_raw_case_id": test_raw["raw_case_id"].astype(int).tolist(),
    }

    print("\n" + "=" * 70)
    print("Raw-row split")
    print("=" * 70)
    print(f"Raw rows total : {n}")
    print(f"Train raw rows : {len(train_raw)}")
    print(f"Val raw rows   : {len(val_raw)}")
    print(f"Test raw rows  : {len(test_raw)}")
    print("Each raw row will be expanded into UP and DOWN samples.")

    return train_raw, val_raw, test_raw, split_info


def build_unified_dataframe(raw_df: pd.DataFrame, split_name: str) -> pd.DataFrame:
    """
    将原始一行数据展开成两行：
        UP   : P_peak, T_rise_fall, VC1_UP,   VG1_UP,   0 -> D_T_UP_given,   PWM_S_UP_first
        DOWN : P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1 -> D_T_DOWN_given, PWM_S_DOWN_first
    """
    df = raw_df.copy()

    up_df = pd.DataFrame(
        {
            "P_peak": df["P_peak"],
            "T_rise_fall": df["T_rise_fall"],
            "VC1": df["VC1_UP"],
            "VG1": df["VG1_UP"],
            "direction_state": DIRECTION_UP,
            "D_T_given": df["D_T_UP_given"],
            "PWM_S_first": df["PWM_S_UP_first"],
            "direction_name": "UP",
            "split": split_name,
            "raw_case_id": df["raw_case_id"],
        }
    )

    down_df = pd.DataFrame(
        {
            "P_peak": df["P_peak"],
            "T_rise_fall": df["T_rise_fall"],
            "VC1": df["VC1_DOWN"],
            "VG1": df["VG1_DOWN"],
            "direction_state": DIRECTION_DOWN,
            "D_T_given": df["D_T_DOWN_given"],
            "PWM_S_first": df["PWM_S_DOWN_first"],
            "direction_name": "DOWN",
            "split": split_name,
            "raw_case_id": df["raw_case_id"],
        }
    )

    data = pd.concat([up_df, down_df], ignore_index=True)
    before_dropna = len(data)
    data = data.dropna(subset=MODEL_INPUT_COLUMNS + MODEL_OUTPUT_COLUMNS).reset_index(
        drop=True
    )
    print(f"{split_name} unified valid rows: {len(data)} / {before_dropna}")

    before_duplicate = len(data)
    key_cols = MODEL_INPUT_COLUMNS

    if DUPLICATE_MODE == "mean":
        duplicate_rows = (
            before_duplicate
            - data.drop_duplicates(subset=key_cols, keep="first").shape[0]
        )
        if duplicate_rows > 0:
            print(
                f"{split_name}: duplicate input rows found: {duplicate_rows}. Mode = mean."
            )
        data = data.groupby(key_cols, as_index=False).agg(
            {
                "D_T_given": "mean",
                "PWM_S_first": "mean",
                "direction_name": "first",
                "split": "first",
                "raw_case_id": "first",
            }
        )
        print(
            f"{split_name}: after duplicate-input averaging: {before_duplicate} -> {len(data)}"
        )
    elif DUPLICATE_MODE == "first":
        data = data.drop_duplicates(subset=key_cols, keep="first").reset_index(
            drop=True
        )
        print(
            f"{split_name}: after keeping first duplicate input: {before_duplicate} -> {len(data)}"
        )
    elif DUPLICATE_MODE == "none":
        print(f"{split_name}: duplicate inputs are kept unchanged.")
    else:
        raise ValueError("DUPLICATE_MODE must be 'mean', 'first', or 'none'.")

    return data.reset_index(drop=True)


# ============================================================
# 6. 标准化与增强特征
# ============================================================


def build_scaler(train_df: pd.DataFrame) -> Dict[str, np.ndarray]:
    # 只允许使用训练集计算 scaler，避免验证/测试集信息泄漏
    x_cont = train_df[["P_peak", "T_rise_fall", "VC1", "VG1"]].to_numpy(
        dtype=np.float32
    )
    y_raw = train_df[MODEL_OUTPUT_COLUMNS].to_numpy(dtype=np.float32)

    x_mean = x_cont.mean(axis=0)
    x_std = x_cont.std(axis=0)
    y_mean = y_raw.mean(axis=0)
    y_std = y_raw.std(axis=0)

    x_std[x_std == 0] = 1.0
    y_std[y_std == 0] = 1.0

    return {
        "x_mean": x_mean.astype(np.float32),
        "x_std": x_std.astype(np.float32),
        "y_mean": y_mean.astype(np.float32),
        "y_std": y_std.astype(np.float32),
    }


def make_feature_matrix_from_raw(
    raw_x: np.ndarray, scaler: Dict[str, np.ndarray]
) -> np.ndarray:
    """
    构造 ANN 的 5 维输入特征。

    raw_x 列顺序：
        0: P_peak
        1: T_rise_fall
        2: VC1
        3: VG1
        4: direction_state，0=UP，1=DOWN

    前 4 个连续量仅使用训练集均值和标准差进行标准化，
    direction_state 保持为 0/1。网络隐藏层自行学习非线性与变量耦合关系。
    """
    raw_x = np.asarray(raw_x, dtype=np.float32)

    if raw_x.ndim != 2 or raw_x.shape[1] != 5:
        raise ValueError(f"raw_x must have shape (N, 5), got {raw_x.shape}")

    x_cont = raw_x[:, 0:4]
    direction = (raw_x[:, 4:5] >= 0.5).astype(np.float32)

    x_norm = (x_cont - scaler["x_mean"].reshape(1, 4)) / scaler["x_std"].reshape(1, 4)

    features = np.concatenate([x_norm, direction], axis=1).astype(np.float32)

    if features.shape[1] != FEATURE_DIM:
        raise RuntimeError(
            f"Feature dimension error: got {features.shape[1]}, expected {FEATURE_DIM}"
        )

    return features


def transform_data(
    df: pd.DataFrame, scaler: Dict[str, np.ndarray]
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raw_x = df[MODEL_INPUT_COLUMNS].to_numpy(dtype=np.float32)
    y_raw = df[MODEL_OUTPUT_COLUMNS].to_numpy(dtype=np.float32)
    direction_names = df["direction_name"].to_numpy(dtype=str)

    x_feature = make_feature_matrix_from_raw(raw_x, scaler)
    y_scaled = (y_raw - scaler["y_mean"].reshape(1, 2)) / scaler["y_std"].reshape(1, 2)

    return (
        x_feature.astype(np.float32),
        y_scaled.astype(np.float32),
        raw_x.astype(np.float32),
        y_raw.astype(np.float32),
        direction_names,
    )


class RegressionDataset(Dataset):
    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.x = torch.tensor(x, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)

    def __len__(self) -> int:
        return len(self.x)

    def __getitem__(self, index: int):
        return self.x[index], self.y[index]


# ============================================================
# 7. ANN 模型
# ============================================================


class ANNRegressor(nn.Module):
    def __init__(
        self,
        input_dim: int = FEATURE_DIM,
        output_dim: int = 2,
        hidden_dims=HIDDEN_DIMS,
        dropout: float = DROPOUT,
    ):
        super().__init__()
        layers: List[nn.Module] = []
        last_dim = input_dim

        for hidden_dim in hidden_dims:
            linear = nn.Linear(last_dim, hidden_dim)
            nn.init.kaiming_uniform_(linear.weight, nonlinearity="relu")
            nn.init.zeros_(linear.bias)
            layers.append(linear)
            layers.append(nn.ReLU())
            if dropout > 0:
                layers.append(nn.Dropout(dropout))
            last_dim = hidden_dim

        output_layer = nn.Linear(last_dim, output_dim)
        nn.init.xavier_uniform_(output_layer.weight)
        nn.init.zeros_(output_layer.bias)
        layers.append(output_layer)

        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


# ============================================================
# 8. 防过拟合训练：AdamW + 验证集选模 + Early Stopping
# ============================================================


def evaluate_scaled_loss(
    model: nn.Module, criterion, x: np.ndarray, y: np.ndarray, device: torch.device
) -> float:
    if len(x) == 0:
        return float("inf")
    model.eval()
    with torch.no_grad():
        xt = torch.tensor(x, dtype=torch.float32).to(device)
        yt = torch.tensor(y, dtype=torch.float32).to(device)
        loss = criterion(model(xt), yt).item()
    return float(loss)


def train_model(
    x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray
):
    """
    高精度且受验证集约束的训练流程：
        1. 对 TRAINING_SEEDS 中的多个随机初始点分别训练；
        2. 每次训练均使用 AdamW、验证集调度和 Early Stopping；
        3. 每次训练内部保存验证集误差最低的参数；
        4. 多次训练之间仍只依据验证集误差选择最终模型；
        5. 测试集完全不参与模型训练和模型选择。
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("=" * 78)
    print("Start high-accuracy ANN training")
    print("Multi-start AdamW + validation selection + Early Stopping")
    print("=" * 78)
    print(f"Device                : {device}")
    print(f"Train samples         : {len(x_train)}")
    print(f"Validation samples    : {len(x_val)}")
    print(f"Feature dimension     : {x_train.shape[1]}")
    print(f"Hidden dimensions     : {HIDDEN_DIMS}")
    print(f"Dropout               : {DROPOUT}")
    print(f"Weight decay          : {WEIGHT_DECAY}")
    print(f"Maximum epochs        : {EPOCHS}")
    print(f"Training restarts     : {len(TRAINING_SEEDS)}")
    print(f"Training seeds        : {TRAINING_SEEDS}")

    criterion = nn.MSELoss()
    global_best_val_loss = float("inf")
    global_best_state_dict = None
    global_best_seed = None
    global_best_history = None
    restart_summaries = []

    for restart_index, seed in enumerate(TRAINING_SEEDS, start=1):
        set_seed(seed)
        print("\n" + "-" * 78)
        print(f"Restart {restart_index}/{len(TRAINING_SEEDS)}, seed = {seed}")
        print("-" * 78)

        dataset = RegressionDataset(x_train, y_train)
        generator = torch.Generator()
        generator.manual_seed(seed)
        dataloader = DataLoader(
            dataset,
            batch_size=min(BATCH_SIZE, len(dataset)),
            shuffle=True,
            drop_last=False,
            generator=generator,
        )

        model = ANNRegressor(
            input_dim=FEATURE_DIM,
            output_dim=len(MODEL_OUTPUT_COLUMNS),
            hidden_dims=HIDDEN_DIMS,
            dropout=DROPOUT,
        ).to(device)

        optimizer = torch.optim.AdamW(
            model.parameters(),
            lr=LEARNING_RATE,
            weight_decay=WEIGHT_DECAY,
            amsgrad=True,
        )

        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="min",
            factor=SCHEDULER_FACTOR,
            patience=SCHEDULER_PATIENCE,
            min_lr=MIN_LEARNING_RATE,
        )

        best_val_loss = float("inf")
        best_epoch = 0
        best_state_dict = None
        early_stop_counter = 0
        stopped_early = False

        train_loss_history: List[float] = []
        val_loss_history: List[float] = []
        lr_history: List[float] = []

        for epoch in range(1, EPOCHS + 1):
            model.train()
            total_loss = 0.0

            for batch_x, batch_y in dataloader:
                batch_x = batch_x.to(device)
                batch_y = batch_y.to(device)

                optimizer.zero_grad(set_to_none=True)
                pred_y = model(batch_x)
                loss = criterion(pred_y, batch_y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()

                total_loss += loss.item() * batch_x.size(0)

            train_loss = total_loss / len(dataset)
            val_loss = evaluate_scaled_loss(model, criterion, x_val, y_val, device)

            scheduler.step(val_loss)
            current_lr = optimizer.param_groups[0]["lr"]

            train_loss_history.append(float(train_loss))
            val_loss_history.append(float(val_loss))
            lr_history.append(float(current_lr))

            if val_loss < best_val_loss - EARLY_STOPPING_MIN_DELTA:
                best_val_loss = float(val_loss)
                best_epoch = int(epoch)
                best_state_dict = {
                    key: value.detach().cpu().clone()
                    for key, value in model.state_dict().items()
                }
                early_stop_counter = 0
            else:
                early_stop_counter += 1

            if epoch == 1 or epoch % PRINT_EVERY == 0 or epoch == EPOCHS:
                print(
                    f"Epoch [{epoch:5d}/{EPOCHS}], "
                    f"Train MSE = {train_loss:.8e}, "
                    f"Val MSE = {val_loss:.8e}, "
                    f"BestVal = {best_val_loss:.8e}, "
                    f"NoImprove = {early_stop_counter:4d}, "
                    f"LR = {current_lr:.3e}",
                    flush=True,
                )

            if early_stop_counter >= EARLY_STOPPING_PATIENCE:
                stopped_early = True
                print(
                    f"Early stopping at epoch {epoch}. "
                    f"Best epoch = {best_epoch}, "
                    f"best validation MSE = {best_val_loss:.8e}"
                )
                break

        if best_state_dict is None:
            raise RuntimeError(f"No valid best model was recorded for seed {seed}.")

        model.load_state_dict(best_state_dict)
        model.eval()
        final_train_loss = evaluate_scaled_loss(
            model, criterion, x_train, y_train, device
        )
        final_val_loss = evaluate_scaled_loss(model, criterion, x_val, y_val, device)

        local_history = {
            "train_loss": train_loss_history,
            "val_loss": val_loss_history,
            "lr": lr_history,
            "model_selection": "best_validation_loss_across_multiple_restarts",
            "selected_seed": int(seed),
            "best_epoch": int(best_epoch),
            "actual_epochs": int(len(train_loss_history)),
            "stopped_early": bool(stopped_early),
            "best_val_loss": float(final_val_loss),
            "train_loss_of_selected_model": float(final_train_loss),
            "lbfgs_used": False,
            "lbfgs_accepted": False,
        }

        restart_summaries.append(
            {
                "seed": int(seed),
                "best_epoch": int(best_epoch),
                "actual_epochs": int(len(train_loss_history)),
                "train_loss": float(final_train_loss),
                "val_loss": float(final_val_loss),
                "stopped_early": bool(stopped_early),
            }
        )

        print(f"Restart train scaled MSE = {final_train_loss:.8e}")
        print(f"Restart val scaled MSE   = {final_val_loss:.8e}")

        if final_val_loss < global_best_val_loss:
            global_best_val_loss = float(final_val_loss)
            global_best_seed = int(seed)
            global_best_state_dict = {
                key: value.detach().cpu().clone()
                for key, value in best_state_dict.items()
            }
            global_best_history = local_history

    if global_best_state_dict is None or global_best_history is None:
        raise RuntimeError("No valid model was selected across training restarts.")

    final_model = ANNRegressor(
        input_dim=FEATURE_DIM,
        output_dim=len(MODEL_OUTPUT_COLUMNS),
        hidden_dims=HIDDEN_DIMS,
        dropout=DROPOUT,
    ).to(device)
    final_model.load_state_dict(global_best_state_dict)
    final_model.eval()

    final_train_loss = evaluate_scaled_loss(
        final_model, criterion, x_train, y_train, device
    )
    final_val_loss = evaluate_scaled_loss(final_model, criterion, x_val, y_val, device)

    global_best_history["restart_summaries"] = restart_summaries
    global_best_history["selected_seed"] = int(global_best_seed)
    global_best_history["best_val_loss"] = float(final_val_loss)
    global_best_history["train_loss_of_selected_model"] = float(final_train_loss)

    print("\n" + "=" * 78)
    print("Multi-start training finished")
    print("=" * 78)
    print(f"Selected seed                = {global_best_seed}")
    print(f"Selected best epoch          = {global_best_history['best_epoch']}")
    print(f"Selected train scaled MSE    = {final_train_loss:.8e}")
    print(f"Selected validation MSE      = {final_val_loss:.8e}")
    print("LBFGS fine-tuning is disabled to reduce overfitting.")

    return final_model, device, final_val_loss, global_best_history


# ============================================================
# 9. 预测、指标与图片
# ============================================================


def predict_all(
    model: nn.Module,
    device: torch.device,
    x_feature: np.ndarray,
    scaler: Dict[str, np.ndarray],
) -> np.ndarray:
    x_tensor = torch.tensor(x_feature, dtype=torch.float32).to(device)
    model.eval()
    with torch.no_grad():
        y_scaled_pred = model(x_tensor).cpu().numpy()
    y_pred = y_scaled_pred * scaler["y_std"].reshape(1, 2) + scaler["y_mean"].reshape(
        1, 2
    )
    return y_pred.astype(np.float32)


def _r2_each_output(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    """
    分别计算每个输出的决定系数 R^2：
        R^2 = 1 - sum((y_true - y_pred)^2) / sum((y_true - mean(y_true))^2)

    当某个输出的真实值全部相同、总平方和为 0 时，该输出的 R^2 无法定义，返回 NaN。
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    ss_res = np.sum((y_true - y_pred) ** 2, axis=0)
    y_mean = np.mean(y_true, axis=0, keepdims=True)
    ss_tot = np.sum((y_true - y_mean) ** 2, axis=0)

    r2 = np.full(y_true.shape[1], np.nan, dtype=np.float64)
    valid = ss_tot > np.finfo(np.float64).eps
    r2[valid] = 1.0 - ss_res[valid] / ss_tot[valid]
    return r2


def _mean_valid(values: np.ndarray) -> float:
    """对有限数值取平均；全部无效时返回 NaN。"""
    values = np.asarray(values, dtype=np.float64)
    valid = np.isfinite(values)
    if not np.any(valid):
        return float("nan")
    return float(np.mean(values[valid]))


def compute_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, direction_names: np.ndarray
) -> Dict[str, object]:
    """
    在反归一化后的原始输出尺度上计算指标。

    Overall 指标定义：
        MAE / MSE / RMSE：对全部样本和全部输出元素统一求平均；
        R^2：先分别计算两个输出的 R^2，再取有效输出的算术平均。
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    err = y_pred - y_true
    abs_err = np.abs(err)
    sq_err = err**2

    mae_each = abs_err.mean(axis=0)
    mse_each = sq_err.mean(axis=0)
    rmse_each = np.sqrt(mse_each)
    r2_each = _r2_each_output(y_true, y_pred)
    maxae_each = abs_err.max(axis=0)
    minae_each = abs_err.min(axis=0)
    round2_each = (np.round(y_pred, 2) == np.round(y_true, 2)).mean(axis=0)

    overall_mae = float(abs_err.mean())
    overall_mse = float(sq_err.mean())
    overall_rmse = float(np.sqrt(overall_mse))
    overall_r2 = _mean_valid(r2_each)

    # 估计误差统一按绝对误差定义：|预测值 - 真实值|。
    # 因此：平均估计误差等价于 MAE，最大/最小估计误差分别等价于 MaxAE/MinAE。
    overall_max_estimation_error = float(abs_err.max())
    overall_min_estimation_error = float(abs_err.min())

    metrics: Dict[str, object] = {
        "samples": int(len(y_true)),
        "mae": overall_mae,
        "mse": overall_mse,
        "rmse": overall_rmse,
        "r2": overall_r2,
        "r2_definition": "mean_of_per_output_r2",
        "maxae": overall_max_estimation_error,
        "minae": overall_min_estimation_error,
        # 下面三个字段使用更直观的名称写入 metrics_summary.json。
        "average_estimation_error": overall_mae,
        "maximum_estimation_error": overall_max_estimation_error,
        "minimum_estimation_error": overall_min_estimation_error,
        "round2_acc": float((np.round(y_pred, 2) == np.round(y_true, 2)).mean()),
        "mae_each": [float(v) for v in mae_each],
        "mse_each": [float(v) for v in mse_each],
        "rmse_each": [float(v) for v in rmse_each],
        "r2_each": [float(v) for v in r2_each],
        "maxae_each": [float(v) for v in maxae_each],
        "minae_each": [float(v) for v in minae_each],
        "round2_acc_each": [float(v) for v in round2_each],
        "per_output": {},
        "per_direction": {},
    }

    for i, name in enumerate(MODEL_OUTPUT_COLUMNS):
        metrics["per_output"][name] = {
            "mae": float(mae_each[i]),
            "mse": float(mse_each[i]),
            "rmse": float(rmse_each[i]),
            "r2": float(r2_each[i]),
            "minae": float(minae_each[i]),
            "maxae": float(maxae_each[i]),
            "average_estimation_error": float(mae_each[i]),
            "maximum_estimation_error": float(maxae_each[i]),
            "minimum_estimation_error": float(minae_each[i]),
            "round2_acc": float(round2_each[i]),
        }

    for direction in ["UP", "DOWN"]:
        mask = direction_names == direction
        if np.sum(mask) == 0:
            continue

        direction_true = y_true[mask]
        direction_pred = y_pred[mask]
        e = direction_pred - direction_true
        ae = np.abs(e)
        se = e**2
        direction_r2_each = _r2_each_output(direction_true, direction_pred)

        direction_mae = float(ae.mean())
        direction_maxae = float(ae.max())
        direction_minae = float(ae.min())

        metrics["per_direction"][direction] = {
            "samples": int(np.sum(mask)),
            "mae": direction_mae,
            "mse": float(se.mean()),
            "rmse": float(np.sqrt(se.mean())),
            "r2": _mean_valid(direction_r2_each),
            "r2_each": [float(v) for v in direction_r2_each],
            "minae": direction_minae,
            "maxae": direction_maxae,
            "average_estimation_error": direction_mae,
            "maximum_estimation_error": direction_maxae,
            "minimum_estimation_error": direction_minae,
        }

    return metrics


def _format_metric(value: float) -> str:
    """统一格式化终端指标，兼容 NaN。"""
    if not np.isfinite(value):
        return "NaN"
    return f"{value:.8e}"


def print_metrics(name: str, metrics: Dict[str, object]) -> None:
    print("\n" + "=" * 78)
    print(f"{name} fitting / prediction metrics on original output scale")
    print("=" * 78)
    print(f"Samples                     = {metrics['samples']}")
    print(f"Overall MAE                 = {_format_metric(metrics['mae'])}")
    print(f"Overall MSE                 = {_format_metric(metrics['mse'])}")
    print(f"Overall RMSE                = {_format_metric(metrics['rmse'])}")
    print(f"Overall R^2 (output mean)   = {_format_metric(metrics['r2'])}")
    print(f"Overall MaxAE               = {_format_metric(metrics['maxae'])}")
    print(f"Overall MinAE               = {_format_metric(metrics['minae'])}")
    print(f"Overall Round2 Acc          = {metrics['round2_acc']:.6f}")

    print("\nAbsolute estimation-error statistics:")
    print(
        f"Average estimation error    = {_format_metric(metrics['average_estimation_error'])}"
    )
    print(
        f"Maximum estimation error    = {_format_metric(metrics['maximum_estimation_error'])}"
    )
    print(
        f"Minimum estimation error    = {_format_metric(metrics['minimum_estimation_error'])}"
    )

    print("\nPer-output metrics:")
    for out_name, item in metrics["per_output"].items():
        print(
            f"{out_name:14s} | "
            f"MAE={_format_metric(item['mae'])}, "
            f"MSE={_format_metric(item['mse'])}, "
            f"RMSE={_format_metric(item['rmse'])}, "
            f"R^2={_format_metric(item['r2'])}, "
            f"AvgEstErr={_format_metric(item['average_estimation_error'])}, "
            f"MaxEstErr={_format_metric(item['maximum_estimation_error'])}, "
            f"MinEstErr={_format_metric(item['minimum_estimation_error'])}, "
            f"Round2={item['round2_acc']:.6f}"
        )

    print("\nPer-direction metrics:")
    for direction, item in metrics["per_direction"].items():
        print(
            f"{direction:4s} | "
            f"samples={item['samples']:4d}, "
            f"MAE={_format_metric(item['mae'])}, "
            f"MSE={_format_metric(item['mse'])}, "
            f"RMSE={_format_metric(item['rmse'])}, "
            f"R^2={_format_metric(item['r2'])}, "
            f"AvgEstErr={_format_metric(item['average_estimation_error'])}, "
            f"MaxEstErr={_format_metric(item['maximum_estimation_error'])}, "
            f"MinEstErr={_format_metric(item['minimum_estimation_error'])}"
        )


def apply_times_new_roman_style(
    ax,
    xlabel: str = None,
    ylabel: str = None,
    title: str = None,
    legend_loc: str = None,
) -> None:
    """将坐标轴、刻度与图例统一设置为新罗马字体，字号为五号(10.5pt)。"""
    if xlabel is not None:
        ax.set_xlabel(xlabel, fontname=PLOT_FONT_FAMILY, fontsize=PLOT_FONT_SIZE)
    if ylabel is not None:
        ax.set_ylabel(ylabel, fontname=PLOT_FONT_FAMILY, fontsize=PLOT_FONT_SIZE)
    if title is not None:
        ax.set_title(title, fontname=PLOT_FONT_FAMILY, fontsize=PLOT_FONT_SIZE)

    ax.tick_params(axis="both", labelsize=PLOT_FONT_SIZE)
    for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
        tick_label.set_fontname(PLOT_FONT_FAMILY)
        tick_label.set_fontsize(PLOT_FONT_SIZE)

    legend = ax.get_legend()
    if legend is not None:
        if legend_loc is not None:
            legend.set_loc(legend_loc)
        for text_item in legend.get_texts():
            text_item.set_fontname(PLOT_FONT_FAMILY)
            text_item.set_fontsize(PLOT_FONT_SIZE)


def save_loss_curve(history: Dict[str, List[float]], output_dir: str) -> None:
    train_loss = history["train_loss"]
    val_loss = history["val_loss"]
    x = np.arange(1, len(train_loss) + 1)

    fig = plt.figure(figsize=(8.0, 5.4))
    plt.plot(x, train_loss, linewidth=1.4, label="Train loss")
    if len(val_loss) > 0 and np.isfinite(np.asarray(val_loss)).any():
        plt.plot(x, val_loss, linewidth=1.4, label="Val loss")
    plt.grid(True, alpha=0.35)
    plt.legend(
        prop={"family": PLOT_FONT_FAMILY, "size": PLOT_FONT_SIZE}, loc="upper right"
    )
    apply_times_new_roman_style(
        plt.gca(),
        xlabel="Epoch",
        ylabel="Scaled MSE Loss",
        title="AdamW Training / Validation Loss",
        legend_loc="upper right",
    )
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, "loss_curve.png"), dpi=300)
    plt.close(fig)


def save_prediction_figures(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    output_dir: str,
    dataset_name: str = "test",
) -> None:
    """仅保存测试集的两个输出真实值—预测值散点图，不再生成单输出重复图片。"""
    plt.rcParams["font.sans-serif"] = ["Times New Roman", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 1, figsize=(6, 5))
    axes = np.asarray(axes).reshape(-1)
    display_names = ["DTgiven"]

    for i, display_name in enumerate(display_names):
        ax = axes[i]
        actual = np.asarray(y_true[:, i], dtype=np.float64)
        predicted = np.asarray(y_pred[:, i], dtype=np.float64)

        min_v = min(float(actual.min()), float(predicted.min()))
        max_v = max(float(actual.max()), float(predicted.max()))
        pad = max((max_v - min_v) * 0.05, 1e-5)
        min_v -= pad
        max_v += pad

        ax.scatter(actual, predicted, s=18, alpha=0.75, label="Samples")
        ax.plot(
            [min_v, max_v],
            [min_v, max_v],
            linestyle="--",
            linewidth=1.5,
            label="Ideal",
        )
        ax.set_xlim(min_v, max_v)
        ax.set_ylim(min_v, max_v)
        ax.grid(True, alpha=0.45)
        ax.legend(
            loc="upper left",
            prop={"family": PLOT_FONT_FAMILY, "size": PLOT_FONT_SIZE},
        )
        apply_times_new_roman_style(
            ax,
            xlabel="Actual",
            ylabel="Predicted",
            title=f"Test: {display_name}",
        )

    plt.tight_layout()
    plt.savefig(
        os.path.join(output_dir, "test_true_vs_predicted.png"),
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)


def save_actual_predicted_line_figures(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    output_dir: str,
    dataset_name: str,
) -> None:
    """
    按样本序号绘制两个实际模型输出的“原始值—预测值”折线对比图。

    输出顺序与模型一致：
        1. D_T_given
        2. PWM_S_first

    这里的原始值是经过当前代码数据清洗、数据集划分和重复输入处理后，
    真正参与该数据集评价的目标值；预测值是模型反归一化后的实际输出。
    """
    plt.rcParams["font.sans-serif"] = [
        "SimHei",
        "Microsoft YaHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False

    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    if y_true.shape != y_pred.shape:
        raise ValueError(
            f"Line-plot shape mismatch: y_true={y_true.shape}, y_pred={y_pred.shape}"
        )
    if y_true.ndim != 2 or y_true.shape[1] != len(MODEL_OUTPUT_COLUMNS):
        raise ValueError(
            f"Line-plot output dimension error: got {y_true.shape}, "
            f"expected (N, {len(MODEL_OUTPUT_COLUMNS)})"
        )

    sample_index = np.arange(len(y_true), dtype=np.int32)
    display_name = {
        "D_T_given": "DTgiven",
        "PWM_S_first": "DSsample",
    }

    fig, axes = plt.subplots(1, 1, figsize=(13.0, 4.5), sharex=True)
    axes = np.asarray(axes).reshape(-1)

    for i, output_name in enumerate(MODEL_OUTPUT_COLUMNS[:1]):
        ax = axes[i]

        # 先画预测曲线，再画真实值，使高度重合时真实值标记仍能被看见。
        (predicted_line,) = ax.plot(
            sample_index,
            y_pred[:, i],
            color="blue",
            linewidth=1.0,
            marker="s",
            markersize=3.2,
            markevery=1,
            label="Predicted Data",
            zorder=2,
        )
        (actual_line,) = ax.plot(
            sample_index,
            y_true[:, i],
            color="red",
            linewidth=0.75,
            marker="+",
            markersize=5.5,
            markeredgewidth=1.0,
            markevery=1,
            label="Actual Data",
            zorder=3,
        )

        ax.grid(True, alpha=0.45)
        ax.legend(
            handles=[actual_line, predicted_line],
            labels=["Actual Data", "Predicted Data"],
            loc="upper right",
            prop={"family": PLOT_FONT_FAMILY, "size": PLOT_FONT_SIZE},
        )
        apply_times_new_roman_style(
            ax,
            ylabel=display_name.get(output_name, output_name),
            title=(
                f"{dataset_name.upper()} - {display_name.get(output_name, output_name)} "
                f"Actual and Predicted Comparison"
            ),
            legend_loc="upper right",
        )

    apply_times_new_roman_style(axes[-1], xlabel="Sample Index")
    plt.tight_layout()
    plt.savefig(
        os.path.join(output_dir, "test_prediction_comparison.png"),
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)


def save_absdiff_error_range_bar(
    y_true: np.ndarray, y_pred: np.ndarray, output_dir: str, dataset_name: str
) -> None:
    """
    柱状图统计的误差严格使用：
        abs_diff = abs(y_pred - y_true)
    横轴为误差范围，纵轴为该范围内样本百分比。
    """
    abs_diff = np.abs(y_pred - y_true)

    error_bins = np.array(
        [0.0, 0.001, 0.002, 0.005, 0.01, 0.02, np.inf], dtype=np.float32
    )
    error_labels = [
        "0~0.001",
        "0.001~0.002",
        "0.002~0.005",
        "0.005~0.01",
        "0.01~0.02",
        ">=0.02",
    ]

    def compute_distribution(v: np.ndarray):
        v = np.asarray(v, dtype=np.float32).reshape(-1)
        counts = []
        for i in range(len(error_bins) - 1):
            left = error_bins[i]
            right = error_bins[i + 1]
            if np.isinf(right):
                mask = v >= left
            else:
                mask = (v >= left) & (v < right)
            counts.append(int(np.sum(mask)))
        counts = np.array(counts, dtype=np.int32)
        percents = counts / max(len(v), 1) * 100.0
        return counts, percents

    def draw_one(ax, values: np.ndarray, title: str):
        counts, percents = compute_distribution(values)
        x_pos = np.arange(len(error_labels))
        bars = ax.bar(x_pos, percents, width=0.68, alpha=0.85)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(error_labels, rotation=25)
        ax.grid(True, axis="y", linestyle="--", alpha=0.4)
        apply_times_new_roman_style(
            ax,
            xlabel="|Predicted - True| range",
            ylabel="Percentage (%)",
            title=title,
        )

        y_top = max(float(percents.max()) * 1.28, 5.0)
        ax.set_ylim(0, y_top)

        for rect, p, c in zip(bars, percents, counts):
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                rect.get_height() + y_top * 0.015,
                f"{p:.1f}%\n({c})",
                ha="center",
                va="bottom",
                fontsize=9,
            )

        min_err = float(values.min()) if len(values) > 0 else 0.0
        max_err = float(values.max()) if len(values) > 0 else 0.0
        mean_err = float(values.mean()) if len(values) > 0 else 0.0
        p_0005 = float(np.mean(values <= 0.005) * 100.0) if len(values) > 0 else 0.0
        p_001 = float(np.mean(values <= 0.01) * 100.0) if len(values) > 0 else 0.0
        p_002 = float(np.mean(values <= 0.02) * 100.0) if len(values) > 0 else 0.0

        info = (
            f"Min = {min_err:.6f}\n"
            f"Max = {max_err:.6f}\n"
            f"Mean = {mean_err:.6f}\n"
            f"<=0.005: {p_0005:.1f}%\n"
            f"<=0.01: {p_001:.1f}%\n"
            f"<=0.02: {p_002:.1f}%"
        )
        ax.text(
            0.98,
            0.98,
            info,
            transform=ax.transAxes,
            ha="right",
            va="top",
            fontsize=9,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.9),
        )

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.8))
    axes = axes.reshape(-1)
    for i, name in enumerate(MODEL_OUTPUT_COLUMNS):
        draw_one(axes[i], abs_diff[:, i], f"{dataset_name}: {name}")
    plt.tight_layout()
    plt.savefig(
        os.path.join(
            output_dir, f"absdiff_error_range_bar_{dataset_name}_2outputs.png"
        ),
        dpi=300,
    )
    plt.close(fig)

    for i, name in enumerate(MODEL_OUTPUT_COLUMNS):
        fig, ax = plt.subplots(figsize=(7.5, 5.8))
        draw_one(ax, abs_diff[:, i], f"{dataset_name}: {name}")
        plt.tight_layout()
        plt.savefig(
            os.path.join(
                output_dir, f"absdiff_error_range_bar_{dataset_name}_{name}.png"
            ),
            dpi=300,
        )
        plt.close(fig)


# ============================================================
# 10. 导出推理代码
# ============================================================


def extract_linear_layers(model: nn.Module) -> List[Dict[str, np.ndarray]]:
    layers = []
    for layer in model.net:
        if isinstance(layer, nn.Linear):
            W = layer.weight.detach().cpu().numpy().astype(np.float32)
            b = layer.bias.detach().cpu().numpy().astype(np.float32)
            layers.append({"W": W, "b": b})
    return layers


def np_array_text(arr: np.ndarray, precision: int = 10) -> str:
    arr = np.asarray(arr)
    if arr.ndim == 1:
        return "[" + ", ".join(f"{float(v):.{precision}g}" for v in arr) + "]"
    rows = []
    for row in arr:
        rows.append(", ".join(f"{float(v):.{precision}g}" for v in row))
    return "[" + "; ".join(rows) + "]"


def export_python_inference(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> None:
    layers = extract_linear_layers(model)
    params = {
        "model_input_columns": MODEL_INPUT_COLUMNS,
        "model_output_columns": MODEL_OUTPUT_COLUMNS,
        "feature_dim": FEATURE_DIM,
        "x_mean": scaler["x_mean"].tolist(),
        "x_std": scaler["x_std"].tolist(),
        "y_mean": scaler["y_mean"].tolist(),
        "y_std": scaler["y_std"].tolist(),
        "layers": [
            {"W": layer["W"].tolist(), "b": layer["b"].tolist()} for layer in layers
        ],
    }

    text = f"""# -*- coding: utf-8 -*-
import numpy as np

PARAMS = {json.dumps(params, ensure_ascii=False, indent=4)}

def make_features(P_peak, T_rise_fall, VC1, VG1, direction_state):
    raw = np.array([[P_peak, T_rise_fall, VC1, VG1]], dtype=np.float32)
    x_mean = np.array(PARAMS["x_mean"], dtype=np.float32).reshape(1, 4)
    x_std = np.array(PARAMS["x_std"], dtype=np.float32).reshape(1, 4)
    x_norm = (raw - x_mean) / x_std
    d = np.array(
        [[1.0 if direction_state >= 0.5 else 0.0]],
        dtype=np.float32,
    )
    return np.concatenate([x_norm, d], axis=1).astype(np.float32)

def ann_predict(P_peak, T_rise_fall, VC1, VG1, direction_state):
    h = make_features(P_peak, T_rise_fall, VC1, VG1, direction_state)
    for i, layer in enumerate(PARAMS["layers"]):
        W = np.array(layer["W"], dtype=np.float32)
        b = np.array(layer["b"], dtype=np.float32)
        h = h @ W.T + b
        if i < len(PARAMS["layers"]) - 1:
            h = np.maximum(h, 0.0)
    y_mean = np.array(PARAMS["y_mean"], dtype=np.float32).reshape(1, 2)
    y_std = np.array(PARAMS["y_std"], dtype=np.float32).reshape(1, 2)
    y = h * y_std + y_mean
    return y.reshape(-1)

def ann_predict_up(P_peak, T_rise_fall, VC1_UP, VG1_UP):
    return ann_predict(P_peak, T_rise_fall, VC1_UP, VG1_UP, 0.0)

def ann_predict_down(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN):
    return ann_predict(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1.0)

if __name__ == "__main__":
    # 输出顺序：D_T_given, PWM_S_first
    print(ann_predict_up(31.75, 0.000077, 0.0, 0.0))
"""
    with open(
        os.path.join(output_dir, "ann_predict_python.py"), "w", encoding="utf-8"
    ) as f:
        f.write(text)


def matlab_matrix_text(arr: np.ndarray, precision: int = 10) -> str:
    arr = np.asarray(arr)
    if arr.ndim == 1:
        return "[" + ", ".join(f"{float(v):.{precision}g}" for v in arr) + "]"
    rows = []
    for row in arr:
        rows.append(", ".join(f"{float(v):.{precision}g}" for v in row))
    return "[" + "; ".join(rows) + "]"


def export_matlab_inference(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> None:
    layers = extract_linear_layers(model)
    lines = []

    lines.append(
        "function y = ann_predict_matlab(P_peak, T_rise_fall, VC1, VG1, direction_state)"
    )
    lines.append("% Output order: y(1)=D_T_given, y(2)=PWM_S_first")
    lines.append("% direction_state: 0=UP, 1=DOWN")
    lines.append("x_mean = " + matlab_matrix_text(scaler["x_mean"]) + ";")
    lines.append("x_std  = " + matlab_matrix_text(scaler["x_std"]) + ";")
    lines.append("y_mean = " + matlab_matrix_text(scaler["y_mean"]) + ";")
    lines.append("y_std  = " + matlab_matrix_text(scaler["y_std"]) + ";")
    lines.append("")
    lines.append("p  = (single(P_peak)      - x_mean(1)) / x_std(1);")
    lines.append("t  = (single(T_rise_fall) - x_mean(2)) / x_std(2);")
    lines.append("vc = (single(VC1)         - x_mean(3)) / x_std(3);")
    lines.append("vg = (single(VG1)         - x_mean(4)) / x_std(4);")
    lines.append("d = single(direction_state ~= 0);")
    lines.append("x = single([p; t; vc; vg; d]);")
    lines.append("")

    for i, layer in enumerate(layers, start=1):
        W = layer["W"]
        b = layer["b"].reshape(-1, 1)
        lines.append(f"W{i} = single(" + matlab_matrix_text(W) + ");")
        lines.append(f"b{i} = single(" + matlab_matrix_text(b) + ");")
        lines.append(f"x = W{i} * x + b{i};")
        if i < len(layers):
            lines.append("x = max(x, 0);")
        lines.append("")

    lines.append("y = x .* y_std(:) + y_mean(:);")
    lines.append("end")

    with open(
        os.path.join(output_dir, "ann_predict_matlab.m"), "w", encoding="utf-8"
    ) as f:
        f.write("\n".join(lines))

    up_lines = [
        "function y = ann_predict_up_matlab(P_peak, T_rise_fall, VC1_UP, VG1_UP)",
        "% Output order: y(1)=D_T_UP_given, y(2)=PWM_S_UP_first",
        "y = ann_predict_matlab(P_peak, T_rise_fall, VC1_UP, VG1_UP, 0);",
        "end",
        "",
    ]
    with open(
        os.path.join(output_dir, "ann_predict_up_matlab.m"), "w", encoding="utf-8"
    ) as f:
        f.write("\n".join(up_lines))

    down_lines = [
        "function y = ann_predict_down_matlab(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN)",
        "% Output order: y(1)=D_T_DOWN_given, y(2)=PWM_S_DOWN_first",
        "y = ann_predict_matlab(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1);",
        "end",
        "",
    ]
    with open(
        os.path.join(output_dir, "ann_predict_down_matlab.m"), "w", encoding="utf-8"
    ) as f:
        f.write("\n".join(down_lines))


def c_float(v: float) -> str:
    s = f"{float(v):.9g}"
    # C 浮点字面量必须是 1.0f/0.0f，而不能是非法的 1f/0f。
    if "e" not in s.lower() and "." not in s:
        s += ".0"
    return s + "f"


def c_array_1d(name: str, arr: np.ndarray) -> str:
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    return (
        f"static const float {name}[{len(arr)}] = {{ "
        + ", ".join(c_float(v) for v in arr)
        + " };"
    )


def c_array_2d(name: str, arr: np.ndarray) -> str:
    arr = np.asarray(arr, dtype=np.float32)
    rows = []
    for row in arr:
        rows.append("    { " + ", ".join(c_float(v) for v in row) + " }")
    return (
        f"static const float {name}[{arr.shape[0]}][{arr.shape[1]}] = {{\n"
        + ",\n".join(rows)
        + "\n};"
    )


def export_c_inference(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> None:
    """导出适用于 F28P550SJ9PZ C28x CPU 的纯 float32 ANN 推理代码。

    不依赖 PyTorch/ONNX/NPU；权重和 scaler 使用 static const float；
    工作缓冲区使用文件静态 RAM；完整复制 Python 端标准化、ANN、反标准化流程。
    注意：使用共享静态工作缓冲区，因此 ann_predict() 不是可重入函数。
    """
    layers = extract_linear_layers(model)
    layer_dims = [(layer["W"].shape[0], layer["W"].shape[1]) for layer in layers]
    max_dim = max([FEATURE_DIM, 2] + [out_dim for out_dim, _ in layer_dims])
    parameter_count = int(sum(layer["W"].size + layer["b"].size for layer in layers))

    h_text = f"""#ifndef ANN_INFERENCE_H
#define ANN_INFERENCE_H

#include <stdint.h>

#ifdef __cplusplus
extern \"C\" {{
#endif

#define ANN_FEATURE_DIM          (5U)
#define ANN_OUTPUT_DIM           (2U)
#define ANN_MAX_HIDDEN_DIM       ({max_dim}U)
#define ANN_PARAMETER_COUNT      ({parameter_count}UL)

/* direction_state: 0 = UP, non-zero = DOWN */
/* y_out[0] = DTgiven, y_out[1] = DSsample */
void ann_predict(
    float P_peak,
    float T_rise_fall,
    float VC1,
    float VG1,
    uint16_t direction_state,
    float y_out[ANN_OUTPUT_DIM]
);

void ann_predict_up(
    float P_peak,
    float T_rise_fall,
    float VC1_UP,
    float VG1_UP,
    float y_out[ANN_OUTPUT_DIM]
);

void ann_predict_down(
    float P_peak,
    float T_rise_fall,
    float VC1_DOWN,
    float VG1_DOWN,
    float y_out[ANN_OUTPUT_DIM]
);

void ann_predict_scaled_features(
    const float x_feature[ANN_FEATURE_DIM],
    float y_scaled[ANN_OUTPUT_DIM]
);

#ifdef __cplusplus
}}
#endif

#endif /* ANN_INFERENCE_H */
"""

    c_lines = []
    c_lines.append(
        "/* Auto-generated float32 ANN inference for TI F28P550SJ9PZ C28x CPU. */"
    )
    c_lines.append(
        "/* No NPU, no dynamic memory, no filesystem, no external ML runtime. */"
    )
    c_lines.append('#include "ann_inference.h"')
    c_lines.append("")
    c_lines.append(c_array_1d("X_MEAN", scaler["x_mean"]))
    c_lines.append(c_array_1d("X_STD", scaler["x_std"]))
    c_lines.append(c_array_1d("Y_MEAN", scaler["y_mean"]))
    c_lines.append(c_array_1d("Y_STD", scaler["y_std"]))
    c_lines.append("")
    for i, layer in enumerate(layers, start=1):
        c_lines.append(c_array_2d(f"W{i}", layer["W"]))
        c_lines.append(c_array_1d(f"B{i}", layer["b"]))
        c_lines.append("")

    c_lines.append(f"static float g_ann_buf_a[{max_dim}];")
    c_lines.append(f"static float g_ann_buf_b[{max_dim}];")
    c_lines.append("")
    c_lines.append("static float ann_relu(float x)")
    c_lines.append("{")
    c_lines.append("    return (x > 0.0f) ? x : 0.0f;")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append(
        "static void ann_build_features(float P_peak, float T_rise_fall, float VC1, float VG1, uint16_t direction_state, float x[ANN_FEATURE_DIM])"
    )
    c_lines.append("{")
    c_lines.append("    x[0] = (P_peak      - X_MEAN[0]) / X_STD[0];")
    c_lines.append("    x[1] = (T_rise_fall - X_MEAN[1]) / X_STD[1];")
    c_lines.append("    x[2] = (VC1         - X_MEAN[2]) / X_STD[2];")
    c_lines.append("    x[3] = (VG1         - X_MEAN[3]) / X_STD[3];")
    c_lines.append("    x[4] = (direction_state != 0U) ? 1.0f : 0.0f;")
    c_lines.append("}")
    c_lines.append("")

    c_lines.append(
        "void ann_predict_scaled_features(const float x_feature[ANN_FEATURE_DIM], float y_scaled[ANN_OUTPUT_DIM])"
    )
    c_lines.append("{")
    c_lines.append("    uint16_t j;")
    c_lines.append("    uint16_t k;")
    c_lines.append("    for (j = 0U; j < ANN_FEATURE_DIM; ++j)")
    c_lines.append("    {")
    c_lines.append("        g_ann_buf_a[j] = x_feature[j];")
    c_lines.append("    }")
    prev = "g_ann_buf_a"
    curr = "g_ann_buf_b"
    for i, (out_dim, in_dim) in enumerate(layer_dims, start=1):
        c_lines.append(f"    for (j = 0U; j < {out_dim}U; ++j)")
        c_lines.append("    {")
        c_lines.append(f"        float sum = B{i}[j];")
        c_lines.append(f"        for (k = 0U; k < {in_dim}U; ++k)")
        c_lines.append("        {")
        c_lines.append(f"            sum += W{i}[j][k] * {prev}[k];")
        c_lines.append("        }")
        if i < len(layer_dims):
            c_lines.append(f"        {curr}[j] = ann_relu(sum);")
        else:
            c_lines.append(f"        {curr}[j] = sum;")
        c_lines.append("    }")
        prev, curr = curr, prev
    c_lines.append("    for (j = 0U; j < ANN_OUTPUT_DIM; ++j)")
    c_lines.append("    {")
    c_lines.append(f"        y_scaled[j] = {prev}[j];")
    c_lines.append("    }")
    c_lines.append("}")
    c_lines.append("")

    c_lines.append(
        "void ann_predict(float P_peak, float T_rise_fall, float VC1, float VG1, uint16_t direction_state, float y_out[ANN_OUTPUT_DIM])"
    )
    c_lines.append("{")
    c_lines.append("    float x_feature[ANN_FEATURE_DIM];")
    c_lines.append("    float y_scaled[ANN_OUTPUT_DIM];")
    c_lines.append("    uint16_t j;")
    c_lines.append(
        "    ann_build_features(P_peak, T_rise_fall, VC1, VG1, direction_state, x_feature);"
    )
    c_lines.append("    ann_predict_scaled_features(x_feature, y_scaled);")
    c_lines.append("    for (j = 0U; j < ANN_OUTPUT_DIM; ++j)")
    c_lines.append("    {")
    c_lines.append("        y_out[j] = y_scaled[j] * Y_STD[j] + Y_MEAN[j];")
    c_lines.append("    }")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append(
        "void ann_predict_up(float P_peak, float T_rise_fall, float VC1_UP, float VG1_UP, float y_out[ANN_OUTPUT_DIM])"
    )
    c_lines.append("{")
    c_lines.append("    ann_predict(P_peak, T_rise_fall, VC1_UP, VG1_UP, 0U, y_out);")
    c_lines.append("}")
    c_lines.append("")
    c_lines.append(
        "void ann_predict_down(float P_peak, float T_rise_fall, float VC1_DOWN, float VG1_DOWN, float y_out[ANN_OUTPUT_DIM])"
    )
    c_lines.append("{")
    c_lines.append(
        "    ann_predict(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1U, y_out);"
    )
    c_lines.append("}")

    with open(os.path.join(output_dir, "ann_inference.h"), "w", encoding="utf-8") as f:
        f.write(h_text)
    with open(os.path.join(output_dir, "ann_inference.c"), "w", encoding="utf-8") as f:
        f.write("\n".join(c_lines) + "\n")


def save_h5_model(
    model: nn.Module,
    scaler: Dict[str, np.ndarray],
    output_dir: str,
    history: Dict[str, object],
) -> str:
    """
    将 PyTorch ANN 权重、网络结构和标准化参数保存为便携式 HDF5 文件。

    说明：该文件是本代码定义的便携式 HDF5 模型格式，不是 TensorFlow/Keras
    的原生 .h5 文件。文件中包含完整推理所需的 Linear 层权重、偏置和 scaler。
    """
    h5_path = os.path.join(output_dir, f"{MODEL_NAME}.h5")
    linear_layers = extract_linear_layers(model)

    with h5py.File(h5_path, "w") as h5_file:
        h5_file.attrs["format"] = "pytorch_ann_portable_hdf5_v1"
        h5_file.attrs["framework"] = "PyTorch"
        h5_file.attrs["model_name"] = MODEL_NAME
        h5_file.attrs["input_dim"] = int(FEATURE_DIM)
        h5_file.attrs["output_dim"] = int(len(MODEL_OUTPUT_COLUMNS))
        h5_file.attrs["hidden_dims_json"] = json.dumps(list(HIDDEN_DIMS))
        h5_file.attrs["dropout"] = float(DROPOUT)
        h5_file.attrs["activation"] = "ReLU"
        h5_file.attrs["input_columns_json"] = json.dumps(MODEL_INPUT_COLUMNS)
        h5_file.attrs["output_columns_json"] = json.dumps(["DTgiven", "DSsample"])
        h5_file.attrs["feature_definition"] = (
            "four_train_standardized_continuous_inputs_plus_direction_state"
        )
        h5_file.attrs["best_epoch"] = int(history.get("best_epoch", -1))
        h5_file.attrs["actual_epochs"] = int(history.get("actual_epochs", -1))
        h5_file.attrs["best_validation_scaled_mse"] = float(
            history.get("best_val_loss", np.nan)
        )

        scaler_group = h5_file.create_group("scaler")
        for name in ["x_mean", "x_std", "y_mean", "y_std"]:
            scaler_group.create_dataset(
                name,
                data=np.asarray(scaler[name], dtype=np.float32),
            )

        layers_group = h5_file.create_group("linear_layers")
        for index, layer in enumerate(linear_layers):
            layer_group = layers_group.create_group(f"layer_{index}")
            layer_group.attrs["activation_after_layer"] = (
                "ReLU" if index < len(linear_layers) - 1 else "Linear"
            )
            layer_group.create_dataset(
                "weight",
                data=np.asarray(layer["W"], dtype=np.float32),
                compression="gzip",
                compression_opts=4,
            )
            layer_group.create_dataset(
                "bias",
                data=np.asarray(layer["b"], dtype=np.float32),
                compression="gzip",
                compression_opts=4,
            )

    return h5_path


def save_checkpoint(
    model: nn.Module,
    scaler: Dict[str, np.ndarray],
    best_loss: float,
    output_dir: str,
    split_info: Dict[str, List[int]],
    history: Dict[str, object],
) -> None:
    checkpoint = {
        "model_state_dict": model.state_dict(),
        "model_input_columns": MODEL_INPUT_COLUMNS,
        "model_output_columns": MODEL_OUTPUT_COLUMNS,
        "feature_dim": FEATURE_DIM,
        "feature_definition": "normalized_P_T_VC1_VG1_plus_direction_state",
        "hidden_dims": HIDDEN_DIMS,
        "dropout": DROPOUT,
        "scaler": scaler,
        "split_info": split_info,
        "history_summary": {
            "best_epoch": history.get("best_epoch"),
            "actual_epochs": history.get("actual_epochs"),
            "stopped_early": history.get("stopped_early"),
            "best_val_loss": history.get("best_val_loss"),
            "train_loss_of_selected_model": history.get("train_loss_of_selected_model"),
            "lbfgs_used": history.get("lbfgs_used"),
            "lbfgs_accepted": history.get("lbfgs_accepted"),
        },
        "training_settings": {
            "max_epochs": EPOCHS,
            "learning_rate": LEARNING_RATE,
            "batch_size": BATCH_SIZE,
            "random_seed": RANDOM_SEED,
            "training_seeds": list(TRAINING_SEEDS),
            "optimizer": "AdamW(amsgrad=True)",
            "weight_decay": WEIGHT_DECAY,
            "scheduler": {
                "name": "ReduceLROnPlateau",
                "monitor": "validation_loss",
                "factor": SCHEDULER_FACTOR,
                "patience": SCHEDULER_PATIENCE,
                "min_lr": MIN_LEARNING_RATE,
            },
            "early_stopping": {
                "monitor": "validation_loss",
                "patience": EARLY_STOPPING_PATIENCE,
                "min_delta": EARLY_STOPPING_MIN_DELTA,
            },
            "gradient_clip_norm": 10.0,
            "use_lbfgs_fine_tune": USE_LBFGS_FINE_TUNE,
            "lbfgs_learning_rate": LBFGS_LEARNING_RATE,
            "lbfgs_max_iter": LBFGS_MAX_ITER,
            "raw_duplicate_mode": RAW_DUPLICATE_MODE,
            "duplicate_mode_after_expansion": DUPLICATE_MODE,
            "train_ratio": TRAIN_RATIO,
            "val_ratio": VAL_RATIO,
            "test_ratio": TEST_RATIO,
            "split_by_raw_row": SPLIT_BY_RAW_ROW,
            "model_selection": "best_validation_loss",
        },
        "best_validation_scaled_mse_loss": float(best_loss),
    }
    torch.save(
        checkpoint,
        os.path.join(output_dir, f"{MODEL_NAME}_checkpoint.pth"),
    )


# ============================================================
# 10.1 CPU/DSP 部署模型文件导出（不使用 NPU）
# ============================================================


class FullRawInputInferenceWrapper(nn.Module):
    """ONNX/TorchScript 全流程包装：原始 5 输入 -> 最终 2 输出。"""

    def __init__(self, base_model: nn.Module, scaler: Dict[str, np.ndarray]):
        super().__init__()
        self.base_model = base_model
        self.register_buffer(
            "x_mean", torch.tensor(scaler["x_mean"], dtype=torch.float32).view(1, 4)
        )
        self.register_buffer(
            "x_std", torch.tensor(scaler["x_std"], dtype=torch.float32).view(1, 4)
        )
        self.register_buffer(
            "y_mean", torch.tensor(scaler["y_mean"], dtype=torch.float32).view(1, 2)
        )
        self.register_buffer(
            "y_std", torch.tensor(scaler["y_std"], dtype=torch.float32).view(1, 2)
        )

    def forward(self, raw_x: torch.Tensor) -> torch.Tensor:
        x_cont = raw_x[:, 0:4]
        direction = (raw_x[:, 4:5] >= 0.5).to(dtype=torch.float32)
        x_norm = (x_cont - self.x_mean) / self.x_std
        features = torch.cat((x_norm, direction), dim=1)
        y_scaled = self.base_model(features)
        return y_scaled * self.y_std + self.y_mean


class FoldedRawInputInferenceWrapper(nn.Module):
    """把输入/输出的仿射缩放合入首尾Linear，方向0/1转换保持不变。

    对实数数学是等价变换，float32可能产生舍入差异，导出前必须校验。
    不修改训练模型、训练scaler或原full/core ONNX。
    """

    def __init__(self, model, scaler):
        super().__init__()
        self.base_model = make_cpu_model_copy(model)
        layers = [
            layer for layer in self.base_model.net if isinstance(layer, nn.Linear)
        ]
        first, last = layers[0], layers[-1]
        with torch.no_grad():
            # 用float64计算变换后的常量，再存为部署用float32。
            mean = torch.tensor(scaler["x_mean"], dtype=torch.float64)
            std = torch.tensor(scaler["x_std"], dtype=torch.float64)
            weight = first.weight.detach().double().clone()
            weight[:, :4] = weight[:, :4] / std.view(1, 4)
            bias = first.bias.detach().double() - weight[:, :4].matmul(mean)
            first.weight.copy_(weight.float())
            first.bias.copy_(bias.float())
            y_std = torch.tensor(scaler["y_std"], dtype=torch.float64)
            y_mean = torch.tensor(scaler["y_mean"], dtype=torch.float64)
            last.weight.copy_(
                (last.weight.detach().double() * y_std.view(2, 1)).float()
            )
            last.bias.copy_((last.bias.detach().double() * y_std + y_mean).float())

    def forward(self, raw_x):
        direction = (raw_x[:, 4:5] >= 0.5).to(dtype=torch.float32)
        features = torch.cat((raw_x[:, :4], direction), dim=1)
        return self.base_model(features)


def make_cpu_model_copy(model: nn.Module) -> nn.Module:
    """创建与已选最佳模型完全相同的 CPU/eval 副本，不改变原模型。"""
    cpu_model = ANNRegressor(
        input_dim=FEATURE_DIM,
        output_dim=len(MODEL_OUTPUT_COLUMNS),
        hidden_dims=HIDDEN_DIMS,
        dropout=DROPOUT,
    ).cpu()
    state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
    cpu_model.load_state_dict(state)
    cpu_model.eval()
    return cpu_model


def save_state_dict_model(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> str:
    output_dir = ensure_output_dir(output_dir)
    path = os.path.join(output_dir, f"{MODEL_NAME}_state_dict.pth")
    payload = {
        "model_state_dict": {
            k: v.detach().cpu() for k, v in model.state_dict().items()
        },
        "model_input_columns": MODEL_INPUT_COLUMNS,
        "model_output_columns": MODEL_OUTPUT_COLUMNS,
        "feature_dim": FEATURE_DIM,
        "hidden_dims": HIDDEN_DIMS,
        "dropout": DROPOUT,
        "x_mean": np.asarray(scaler["x_mean"], dtype=np.float32),
        "x_std": np.asarray(scaler["x_std"], dtype=np.float32),
        "y_mean": np.asarray(scaler["y_mean"], dtype=np.float32),
        "y_std": np.asarray(scaler["y_std"], dtype=np.float32),
    }
    torch.save(payload, path)
    return path


def save_torchscript_model(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> str:
    """保存完整 TorchScript；使用 ASCII 临时路径规避 Windows 中文路径问题。"""
    output_dir = ensure_output_dir(output_dir)
    cpu_model = make_cpu_model_copy(model)
    wrapper = FullRawInputInferenceWrapper(cpu_model, scaler).cpu().eval()
    example_raw = torch.zeros((1, 5), dtype=torch.float32)
    traced = torch.jit.trace(wrapper, example_raw)
    path = os.path.join(output_dir, f"{MODEL_NAME}_torchscript.pt")

    def _writer(temp_path: str) -> None:
        torch.jit.save(traced, temp_path)

    return save_via_ascii_temp(_writer, path, ".pt")


def save_numpy_parameters(
    model: nn.Module, scaler: Dict[str, np.ndarray], output_dir: str
) -> str:
    output_dir = ensure_output_dir(output_dir)
    layers = extract_linear_layers(model)
    arrays = {
        "x_mean": np.asarray(scaler["x_mean"], dtype=np.float32),
        "x_std": np.asarray(scaler["x_std"], dtype=np.float32),
        "y_mean": np.asarray(scaler["y_mean"], dtype=np.float32),
        "y_std": np.asarray(scaler["y_std"], dtype=np.float32),
    }
    for i, layer in enumerate(layers, start=1):
        arrays[f"W{i}"] = np.asarray(layer["W"], dtype=np.float32)
        arrays[f"B{i}"] = np.asarray(layer["b"], dtype=np.float32)
    path = os.path.join(output_dir, f"{MODEL_NAME}_parameters.npz")
    np.savez(path, **arrays)
    return path


def export_onnx_models(
    model: nn.Module,
    scaler: Dict[str, np.ndarray],
    output_dir: str,
    raw_x_verify: np.ndarray = None,
) -> Dict[str, object]:
    """导出两个 float32 ONNX：core 与 full；不量化、不使用 NPU。"""
    output_dir = ensure_output_dir(output_dir)
    try:
        import onnx  # noqa: F401
    except Exception as exc:
        raise RuntimeError(
            "ONNX export requires package 'onnx'. Please run: pip install onnx onnxruntime"
        ) from exc

    cpu_model = make_cpu_model_copy(model)
    core_path = os.path.join(output_dir, f"{MODEL_NAME}_core.onnx")
    full_path = os.path.join(output_dir, f"{MODEL_NAME}_full.onnx")

    dummy_feature = torch.zeros((1, FEATURE_DIM), dtype=torch.float32)

    def _write_core_onnx(temp_path: str) -> None:
        torch.onnx.export(
            cpu_model,
            dummy_feature,
            temp_path,
            export_params=True,
            opset_version=ONNX_OPSET_VERSION,
            do_constant_folding=True,
            input_names=["features"],
            output_names=["y_scaled"],
            dynamic_axes={"features": {0: "batch"}, "y_scaled": {0: "batch"}},
        )

    core_path = save_via_ascii_temp(_write_core_onnx, core_path, ".onnx")

    full_wrapper = FullRawInputInferenceWrapper(cpu_model, scaler).cpu().eval()
    dummy_raw = torch.zeros((1, 5), dtype=torch.float32)

    def _write_full_onnx(temp_path: str) -> None:
        torch.onnx.export(
            full_wrapper,
            dummy_raw,
            temp_path,
            export_params=True,
            opset_version=ONNX_OPSET_VERSION,
            do_constant_folding=True,
            input_names=["raw_input"],
            output_names=["prediction"],
            dynamic_axes={"raw_input": {0: "batch"}, "prediction": {0: "batch"}},
        )

    full_path = save_via_ascii_temp(_write_full_onnx, full_path, ".onnx")

    # 固定batch部署版优先合并首尾缩放；先核对所有提供的测试样本。
    # 如果数值误差超限，自动退回未合并版本；不放宽原一致性容差。
    static_wrapper = full_wrapper
    scaler_folded = False
    folding_max_abs_error = None
    if (
        SAVE_STATIC_FULL_ONNX
        and FOLD_SCALER_IN_STATIC_ONNX
        and raw_x_verify is not None
        and len(raw_x_verify) > 0
    ):
        candidate = FoldedRawInputInferenceWrapper(model, scaler).cpu().eval()
        folding_passed = True
        folding_max_abs_error = 0.0
        with torch.no_grad():
            for offset in range(0, len(raw_x_verify), 1024):
                raw_batch = torch.from_numpy(
                    np.asarray(raw_x_verify[offset : offset + 1024], dtype=np.float32)
                )
                reference = full_wrapper(raw_batch)
                folded = candidate(raw_batch)
                error = float(torch.max(torch.abs(folded - reference)).item())
                if not np.isfinite(error):
                    folding_passed = False
                else:
                    folding_max_abs_error = max(folding_max_abs_error, error)
                if not torch.allclose(
                    folded, reference, atol=ONNX_VERIFY_ATOL, rtol=ONNX_VERIFY_RTOL
                ):
                    folding_passed = False
        if folding_passed:
            static_wrapper = candidate
            scaler_folded = True

    static_full_path = None
    if SAVE_STATIC_FULL_ONNX:
        static_full_path = os.path.join(output_dir, f"{MODEL_NAME}_full_static.onnx")

        def _write_static_full_onnx(temp_path: str) -> None:
            torch.onnx.export(
                static_wrapper,
                dummy_raw,
                temp_path,
                export_params=True,
                opset_version=ONNX_OPSET_VERSION,
                do_constant_folding=True,
                input_names=["raw_input"],
                output_names=["prediction"],
            )
            exported = onnx.load(temp_path)
            onnx.checker.check_model(exported)
            actual_input_shape = [
                d.dim_value for d in exported.graph.input[0].type.tensor_type.shape.dim
            ]
            actual_output_shape = [
                d.dim_value for d in exported.graph.output[0].type.tensor_type.shape.dim
            ]
            if actual_input_shape != [1, 5] or actual_output_shape != [1, 2]:
                raise RuntimeError(
                    f"Static ONNX shape mismatch: {actual_input_shape} -> {actual_output_shape}"
                )

        static_full_path = save_via_ascii_temp(
            _write_static_full_onnx, static_full_path, ".onnx"
        )

    verification = {
        "enabled": bool(VERIFY_ONNX_WITH_RUNTIME),
        "performed": False,
        "core_max_abs_error": None,
        "full_max_abs_error": None,
        "passed": None,
    }

    if VERIFY_ONNX_WITH_RUNTIME:
        try:
            import onnxruntime as ort

            if raw_x_verify is None or len(raw_x_verify) == 0:
                raw_np = np.zeros((4, 5), dtype=np.float32)
            else:
                raw_np = np.asarray(raw_x_verify, dtype=np.float32)

            feat_np = make_feature_matrix_from_raw(raw_np, scaler)
            with torch.no_grad():
                torch_core = (
                    cpu_model(torch.from_numpy(feat_np))
                    .cpu()
                    .numpy()
                    .astype(np.float32)
                )
                torch_full = (
                    full_wrapper(torch.from_numpy(raw_np))
                    .cpu()
                    .numpy()
                    .astype(np.float32)
                )

            core_sess = ort.InferenceSession(
                core_path, providers=["CPUExecutionProvider"]
            )
            full_sess = ort.InferenceSession(
                full_path, providers=["CPUExecutionProvider"]
            )
            onnx_core = core_sess.run(None, {"features": feat_np})[0].astype(np.float32)
            onnx_full = full_sess.run(None, {"raw_input": raw_np})[0].astype(np.float32)

            core_err = float(np.max(np.abs(onnx_core - torch_core)))
            full_err = float(np.max(np.abs(onnx_full - torch_full)))
            passed = bool(
                np.allclose(
                    onnx_core, torch_core, atol=ONNX_VERIFY_ATOL, rtol=ONNX_VERIFY_RTOL
                )
                and np.allclose(
                    onnx_full, torch_full, atol=ONNX_VERIFY_ATOL, rtol=ONNX_VERIFY_RTOL
                )
            )
            verification.update(
                {
                    "performed": True,
                    "core_max_abs_error": core_err,
                    "full_max_abs_error": full_err,
                    "passed": passed,
                }
            )
            if not passed:
                raise RuntimeError(
                    f"ONNX verification failed: core max abs error={core_err:.6e}, "
                    f"full max abs error={full_err:.6e}"
                )
            if static_full_path is not None:
                static_sess = ort.InferenceSession(
                    static_full_path, providers=["CPUExecutionProvider"]
                )
                static_prediction = np.concatenate(
                    [
                        static_sess.run(None, {"raw_input": raw_np[i : i + 1]})[0]
                        for i in range(len(raw_np))
                    ],
                    axis=0,
                ).astype(np.float32)
                static_err = float(np.max(np.abs(static_prediction - torch_full)))
                static_passed = bool(
                    np.allclose(
                        static_prediction,
                        torch_full,
                        atol=ONNX_VERIFY_ATOL,
                        rtol=ONNX_VERIFY_RTOL,
                    )
                )
                verification["static_full_max_abs_error"] = static_err
                verification["static_full_passed"] = static_passed
                verification["passed"] = bool(passed and static_passed)
                if not static_passed:
                    raise RuntimeError(
                        f"Static full ONNX verification failed: max abs error={static_err:.6e}"
                    )

        except ImportError:
            verification["note"] = (
                "onnxruntime is not installed; ONNX files were exported but runtime equivalence was not checked. "
                "Install with: pip install onnxruntime"
            )

    return {
        "core_onnx": core_path,
        "full_onnx": full_path,
        "static_full_onnx": static_full_path,
        "static_scaler_folded": scaler_folded,
        "folding_max_abs_error": folding_max_abs_error,
        "verification": verification,
    }


def _c_initializer_rows(arr: np.ndarray, precision: int = 9) -> str:
    arr = np.asarray(arr, dtype=np.float32)
    rows = []
    for row in arr:
        values = ", ".join(c_float(float(v)) for v in np.asarray(row).reshape(-1))
        rows.append("    { " + values + " }")
    return ",\n".join(rows)


def export_dsp_test_vectors(
    raw_x: np.ndarray, y_expected: np.ndarray, output_dir: str
) -> str:
    n = int(min(DSP_TEST_VECTOR_COUNT, len(raw_x), len(y_expected)))
    if n <= 0:
        raise ValueError("No test vectors are available for DSP export.")

    x = np.asarray(raw_x[:n], dtype=np.float32)
    y = np.asarray(y_expected[:n], dtype=np.float32)

    text = f"""#ifndef ANN_TEST_VECTORS_H
#define ANN_TEST_VECTORS_H

#define ANN_TEST_VECTOR_COUNT ({n}U)

/* Columns: P_peak, T_rise_fall, VC1, VG1, direction_state */
static const float ANN_TEST_INPUTS[ANN_TEST_VECTOR_COUNT][5] = {{
{_c_initializer_rows(x)}
}};

/* Columns: DTgiven, DSsample. Values are PyTorch float32 predictions. */
static const float ANN_TEST_EXPECTED[ANN_TEST_VECTOR_COUNT][2] = {{
{_c_initializer_rows(y)}
}};

#endif /* ANN_TEST_VECTORS_H */
"""
    path = os.path.join(output_dir, "ann_test_vectors.h")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def export_dsp_support_files(output_dir: str) -> Dict[str, str]:
    self_h = """#ifndef ANN_DSP_SELF_TEST_H
#define ANN_DSP_SELF_TEST_H

float ANN_runSelfTest(void);

#endif /* ANN_DSP_SELF_TEST_H */
"""
    self_c = """#include "ann_dsp_self_test.h"
#include "ann_inference.h"
#include "ann_test_vectors.h"

static float abs_f(float x)
{
    return (x >= 0.0f) ? x : -x;
}

float ANN_runSelfTest(void)
{
    uint16_t i;
    float y[ANN_OUTPUT_DIM];
    float max_abs_error = 0.0f;

    for (i = 0U; i < ANN_TEST_VECTOR_COUNT; ++i)
    {
        const float *x = ANN_TEST_INPUTS[i];
        ann_predict(x[0], x[1], x[2], x[3], (uint16_t)(x[4] >= 0.5f), y);
        {
            float e0 = abs_f(y[0] - ANN_TEST_EXPECTED[i][0]);
            float e1 = abs_f(y[1] - ANN_TEST_EXPECTED[i][1]);
            if (e0 > max_abs_error) max_abs_error = e0;
            if (e1 > max_abs_error) max_abs_error = e1;
        }
    }
    return max_abs_error;
}
"""
    example_c = """/* F28P550SJ9PZ C28x CPU inference example - NO NPU. */
#include "ann_inference.h"

volatile float g_DTgiven = 0.0f;
volatile float g_DSsample = 0.0f;

void ANN_application_step(float P_peak, float T_rise_fall, float VC1, float VG1, uint16_t direction_state)
{
    float y[ANN_OUTPUT_DIM];
    ann_predict(P_peak, T_rise_fall, VC1, VG1, direction_state, y);
    g_DTgiven = y[0];
    g_DSsample = y[1];
}

void ANN_application_step_up(float P_peak, float T_rise_fall, float VC1_UP, float VG1_UP)
{
    float y[ANN_OUTPUT_DIM];
    ann_predict_up(P_peak, T_rise_fall, VC1_UP, VG1_UP, y);
    g_DTgiven = y[0];
    g_DSsample = y[1];
}

void ANN_application_step_down(float P_peak, float T_rise_fall, float VC1_DOWN, float VG1_DOWN)
{
    float y[ANN_OUTPUT_DIM];
    ann_predict_down(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, y);
    g_DTgiven = y[0];
    g_DSsample = y[1];
}
"""
    readme = """F28P550SJ9PZ CPU/DSP ANN Deployment (NO NPU)
====================================================

Required CCS files:
  ann_inference.c
  ann_inference.h

Recommended first-board verification files:
  ann_test_vectors.h
  ann_dsp_self_test.c
  ann_dsp_self_test.h

Reference integration example:
  ann_dsp_integration_example.c

The DSP does not load .h5/.pth/.pt/.onnx at runtime. All float32 weights,
biases and scaler constants are compiled into ann_inference.c.

Inference math exactly matches the Python model:
  raw input -> training scaler -> 5D feature -> 8/ReLU -> 4/ReLU
  -> 2D scaled output -> inverse scaler -> DTgiven/DSsample.
  Static full ONNX may fold the affine scaler into the first/last Linear.
  This C inference export retains the explicit scaler for consistency.

Recommended first test:
  float max_err = ANN_runSelfTest();

Build notes:
- Use a normal F28P55x C2000Ware/CCS project; no NPU libraries are needed.
- Keep ANN arithmetic in float32.
- After correctness verification, enable normal C28x optimization such as -O2/-O3.
- ann_predict() uses shared static work buffers and is not reentrant.
- Verify final .map placement if strict Flash/RAM placement is required.

Model interchange files:
  *_full.onnx       raw 5-input -> final 2-output model (dynamic batch)
  *_full_static.onnx raw [1,5] -> final [1,2], for TI MCU NNC compilation
  *_core.onnx       normalized 5-feature -> normalized 2-output ANN
  *_checkpoint.pth  complete PyTorch checkpoint
  *_state_dict.pth  state/scaler package
  *_torchscript.pt  full raw-input TorchScript model
  *.h5              portable HDF5 archive
  *_parameters.npz  NumPy weights/biases/scaler

No QAT, PTQ, TI MCU NNC, mod.a, tvmgen_default.h or NPU initialization is used.
"""
    req = "numpy\npandas\nmatplotlib\ntorch\nh5py\nonnx\nonnxruntime\n"

    paths = {
        "self_test_h": os.path.join(output_dir, "ann_dsp_self_test.h"),
        "self_test_c": os.path.join(output_dir, "ann_dsp_self_test.c"),
        "integration_example": os.path.join(
            output_dir, "ann_dsp_integration_example.c"
        ),
        "readme": os.path.join(output_dir, "DSP_DEPLOYMENT_README.txt"),
        "requirements": os.path.join(output_dir, "requirements_deployment.txt"),
    }
    for path, content in [
        (paths["self_test_h"], self_h),
        (paths["self_test_c"], self_c),
        (paths["integration_example"], example_c),
        (paths["readme"], readme),
        (paths["requirements"], req),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return paths


def build_deployment_manifest(
    output_dir: str,
    history: Dict[str, object],
    best_loss: float,
    onnx_info: Dict[str, object],
) -> Dict[str, object]:
    dims = [FEATURE_DIM] + list(HIDDEN_DIMS) + [len(MODEL_OUTPUT_COLUMNS)]
    parameter_count = sum(
        in_dim * out_dim + out_dim for in_dim, out_dim in zip(dims[:-1], dims[1:])
    )
    return {
        "target": {
            "device": "TMS320F28P550SJ9PZ / F28P55x",
            "execution": "C28x_CPU_float32",
            "uses_npu": False,
        },
        "training_compatibility": {
            "training_logic_changed": False,
            "note": "Hidden layers reduced to 8/4 (two hidden layers). Data split, scaler, AdamW, scheduler, early stopping, dropout and multi-seed validation selection are unchanged. Static full ONNX optionally folds affine scaling after equivalence checks. Accuracy and the 100us latency target require validation; neither is guaranteed.",
            "selected_seed": history.get("selected_seed"),
            "best_epoch": history.get("best_epoch"),
            "best_validation_scaled_mse": float(best_loss),
        },
        "model": {
            "raw_inputs": MODEL_INPUT_COLUMNS,
            "outputs": ["DTgiven", "DSsample"],
            "hidden_dims": list(HIDDEN_DIMS),
            "dropout_training": float(DROPOUT),
            "activation": "ReLU",
            "parameter_count": int(parameter_count),
            "parameter_storage_float32_bytes": int(parameter_count * 4),
            "dense_mac_per_inference": int(
                sum(a * b for a, b in zip(dims[:-1], dims[1:]))
            ),
            "latency_target_us": 100.0,
            "latency_measured_us": None,
            "latency_target_verified": False,
        },
        "onnx": onnx_info,
        "dsp_runtime_files": [
            "ann_inference.c",
            "ann_inference.h",
            "ann_test_vectors.h",
            "ann_dsp_self_test.c",
            "ann_dsp_self_test.h",
            "ann_dsp_integration_example.c",
        ],
        "pc_model_files": [
            f"{MODEL_NAME}.h5",
            f"{MODEL_NAME}_checkpoint.pth",
            f"{MODEL_NAME}_state_dict.pth",
            f"{MODEL_NAME}_torchscript.pt",
            f"{MODEL_NAME}_core.onnx",
            f"{MODEL_NAME}_full.onnx",
            *([f"{MODEL_NAME}_full_static.onnx"] if SAVE_STATIC_FULL_ONNX else []),
            f"{MODEL_NAME}_parameters.npz",
        ],
    }


# ============================================================
# 11. 计算 ANN 两个输出的总体训练集/测试集误差
# ============================================================


def compute_overall_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> Dict[str, object]:
    """
    将指定数据集中的全部 UP/DOWN 样本以及两个模型输出 DT、DS 统一纳入统计，
    输出 ANN 模型的总体 MAE、总体 MSE 和总体 R^2。

    总体 MAE：
        对所有样本、所有输出的绝对误差求平均。

    总体 MSE：
        对所有样本、所有输出的平方误差求平均。

    总体 R^2：
        先分别计算 DT 与 DS 的 R^2，再对两个 R^2 做等权平均，
        对应多输出回归中常用的 uniform_average 定义。

    注意：不把 DT 和 DS 直接展平成单个序列计算 R^2，避免两个输出的
    均值与量纲差异使总体 R^2 被错误放大或缩小。
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    if y_true.shape != y_pred.shape:
        raise ValueError(
            f"Metric shape mismatch: y_true={y_true.shape}, y_pred={y_pred.shape}"
        )
    if y_true.ndim != 2 or y_true.shape[1] != 2:
        raise ValueError(f"Expected y_true/y_pred shape (N, 2), got {y_true.shape}")
    if y_true.shape[0] == 0:
        return {
            "samples": 0,
            "output_count": 2,
            "mae": float("nan"),
            "mse": float("nan"),
            "r2": float("nan"),
            "r2_each": [float("nan"), float("nan")],
            "r2_definition": "uniform_average_of_DT_and_DS_R2",
        }

    error = y_pred - y_true
    abs_error = np.abs(error)
    squared_error = error**2

    overall_mae = float(np.mean(abs_error))
    overall_mse = float(np.mean(squared_error))

    r2_each = []
    for output_index in range(y_true.shape[1]):
        actual = y_true[:, output_index]
        predicted = y_pred[:, output_index]

        ss_res = float(np.sum((predicted - actual) ** 2))
        ss_tot = float(np.sum((actual - np.mean(actual)) ** 2))

        variance_scale = max(float(np.max(np.abs(actual))), 1.0)
        ss_tot_tol = max(
            np.finfo(np.float64).eps,
            len(actual) * (1e-10 * variance_scale) ** 2,
        )

        if ss_tot > ss_tot_tol:
            r2_each.append(float(1.0 - ss_res / ss_tot))
        else:
            r2_each.append(float("nan"))

    valid_r2 = np.asarray(r2_each, dtype=np.float64)
    valid_r2 = valid_r2[np.isfinite(valid_r2)]
    overall_r2 = float(np.mean(valid_r2)) if valid_r2.size > 0 else float("nan")

    return {
        "samples": int(y_true.shape[0]),
        "output_count": int(y_true.shape[1]),
        "mae": overall_mae,
        "mse": overall_mse,
        "r2": overall_r2,
        "r2_each": [float(v) for v in r2_each],
        "r2_definition": "uniform_average_of_DT_and_DS_R2",
    }


def print_overall_metrics(
    train_metrics: Dict[str, object],
    test_metrics: Dict[str, object],
) -> None:
    """训练结束后打印训练集和测试集的总体 MAE、MSE 与 R^2。"""
    print("\n" + "=" * 78)
    print("ANN OVERALL TRAIN SET METRICS")
    print("=" * 78)
    print(f"Samples     = {train_metrics['samples']}")
    print(f"Overall MAE = {_format_metric(train_metrics['mae'])}")
    print(f"Overall MSE = {_format_metric(train_metrics['mse'])}")
    print(f"Overall R^2 = {_format_metric(train_metrics['r2'])}")

    print("\n" + "=" * 78)
    print("ANN OVERALL TEST SET METRICS")
    print("=" * 78)
    print(f"Samples     = {test_metrics['samples']}")
    print(f"Overall MAE = {_format_metric(test_metrics['mae'])}")
    print(f"Overall MSE = {_format_metric(test_metrics['mse'])}")
    print(f"Overall R^2 = {_format_metric(test_metrics['r2'])}")


# ============================================================
# 12. DTgiven 与 DSsample 分输出测试指标
# ============================================================


def compute_dtgiven_dssample_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> Dict[str, Dict[str, float]]:
    """
    分别计算两个模型输出的测试集指标：
        DTgiven  -> y[:, 0]，对应 D_T_given
        DSsample -> y[:, 1]，对应 PWM_S_first / D_S

    MAE、MSE 保持原始量纲；R^2 保持无量纲。

    平均、最大和最小预测误差改为绝对百分比误差：
        percentage_error = abs(predicted - actual) / abs(actual) * 100%

    当真实值非常接近 0 时，相对百分比误差没有可靠意义，因此：
        1. 使用相对于该输出真实值最大绝对值的动态阈值判断近零值；
        2. 近零真实值不参与百分比误差的平均、最大和最小统计；
        3. 有效样本数与被排除样本数会写入 metrics_summary.json。
    """
    y_true = np.asarray(y_true, dtype=np.float64)
    y_pred = np.asarray(y_pred, dtype=np.float64)

    if y_true.shape != y_pred.shape:
        raise ValueError(
            f"Metric shape mismatch: y_true={y_true.shape}, y_pred={y_pred.shape}"
        )
    if y_true.ndim != 2 or y_true.shape[1] != 2:
        raise ValueError(f"Expected shape (N, 2), got {y_true.shape}")

    result: Dict[str, Dict[str, float]] = {}
    output_specs = [("DTgiven", 0), ("DSsample", 1)]

    for output_name, output_index in output_specs:
        actual = y_true[:, output_index]
        predicted = y_pred[:, output_index]
        error = predicted - actual
        abs_error = np.abs(error)
        squared_error = error**2

        ss_res = float(np.sum(squared_error))
        ss_tot = float(np.sum((actual - np.mean(actual)) ** 2))
        variance_scale = max(float(np.max(np.abs(actual))), 1.0)
        ss_tot_tol = max(
            np.finfo(np.float64).eps,
            len(actual) * (1e-10 * variance_scale) ** 2,
        )
        r2 = float(1.0 - ss_res / ss_tot) if ss_tot > ss_tot_tol else float("nan")

        # 百分比误差的分母不能为 0。阈值随当前输出的数据尺度变化，
        # 同时保留一个很小的绝对下限，避免浮点除零和无意义的巨大百分比。
        actual_scale = max(float(np.max(np.abs(actual))), 1.0)
        percent_denominator_tol = max(
            np.finfo(np.float64).eps,
            1e-10 * actual_scale,
        )
        valid_percent_mask = np.abs(actual) > percent_denominator_tol
        valid_percent_count = int(np.sum(valid_percent_mask))
        excluded_percent_count = int(actual.size - valid_percent_count)

        if valid_percent_count > 0:
            absolute_percentage_error = (
                abs_error[valid_percent_mask]
                / np.abs(actual[valid_percent_mask])
                * 100.0
            )
            average_percentage_error = float(np.mean(absolute_percentage_error))
            maximum_percentage_error = float(np.max(absolute_percentage_error))
            minimum_percentage_error = float(np.min(absolute_percentage_error))
        else:
            average_percentage_error = float("nan")
            maximum_percentage_error = float("nan")
            minimum_percentage_error = float("nan")

        result[output_name] = {
            "samples": int(actual.size),
            "mae": float(np.mean(abs_error)),
            "mse": float(np.mean(squared_error)),
            "max_absolute_error": float(np.max(abs_error)),
            "r2": r2,
            "average_prediction_error_percent": average_percentage_error,
            "maximum_prediction_error_percent": maximum_percentage_error,
            "minimum_prediction_error_percent": minimum_percentage_error,
            "percentage_error_valid_samples": valid_percent_count,
            "percentage_error_excluded_near_zero_samples": excluded_percent_count,
            "percentage_error_denominator_tolerance": float(percent_denominator_tol),
        }

    return result


def print_dtgiven_dssample_metrics(
    metrics: Dict[str, Dict[str, float]],
) -> None:
    """训练结束后分别打印 DTgiven 与 DSsample 的测试集指标。"""
    print("\n" + "=" * 78)
    print("ANN TEST SET METRICS FOR DTgiven")
    print("=" * 78)

    for output_name in ["DTgiven"]:
        item = metrics[output_name]
        print(f"\n{output_name}")
        print(f"MAE              = {_format_metric(item['mae'])}")
        print(f"MSE              = {_format_metric(item['mse'])}")
        print(f"R^2              = {_format_metric(item['r2'])}")
        print(
            "平均预测误差     = "
            f"{_format_metric(item['average_prediction_error_percent'])} %"
        )
        print(
            "最大预测误差     = "
            f"{_format_metric(item['maximum_prediction_error_percent'])} %"
        )
        print(
            "最小预测误差     = "
            f"{_format_metric(item['minimum_prediction_error_percent'])} %"
        )


def save_test_metrics_txt(metrics, output_dir):
    """只汇总独立测试集的原始量纲误差；不参与训练或模型选择。"""
    output_dir = ensure_output_dir(output_dir)
    dims = [FEATURE_DIM] + list(HIDDEN_DIMS) + [len(MODEL_OUTPUT_COLUMNS)]
    parameter_count = sum(a * b + b for a, b in zip(dims[:-1], dims[1:]))
    suffix = "_".join(str(v) for v in HIDDEN_DIMS)
    path = os.path.join(output_dir, f"test_metrics_{suffix}.txt")
    lines = [
        "ANN 测试集预测误差汇总",
        "网络结构：" + " -> ".join(str(v) for v in dims),
        f"可训练参数量：{parameter_count}",
        "数据范围：全部测试集样本（UP 与 DOWN 合并），两个输出分别统计。",
        "误差在输出反标准化后计算，不是标准化空间的训练损失。",
        "MAE = mean(abs(预测值 - 真实值))",
        "MSE = mean((预测值 - 真实值)^2)",
        "R² = 1 - sum((预测值 - 真实值)^2) / sum((真实值 - 均值)^2)",
        "最大绝对误差 = max(abs(预测值 - 真实值))，不是百分比误差。",
        "R² 为 nan 时，表示目标方差过小，原评估函数未给出有效 R²。",
        "",
    ]
    for output_name in ("DTgiven",):
        item = metrics[output_name]
        lines.extend(
            [
                f"[{output_name}]",
                f"测试样本数：{item['samples']}",
                f"MAE          = {item['mae']:.10g}",
                f"MSE          = {item['mse']:.10g}",
                f"R²           = {item['r2']:.10g}",
                f"最大绝对误差 = {item['max_absolute_error']:.10g}",
                "",
            ]
        )
    with open(path, "w", encoding="utf-8-sig") as file:
        file.write("\n".join(lines))
    return path


# ============================================================
# 13. 主程序
# ============================================================

if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    silent_buffer = io.StringIO()

    with (
        contextlib.nullcontext()
        if os.environ.get("ANN_STREAM_LOG") == "1"
        else contextlib.redirect_stdout(silent_buffer)
    ), (
        contextlib.nullcontext()
        if os.environ.get("ANN_STREAM_LOG") == "1"
        else contextlib.redirect_stderr(silent_buffer)
    ):
        csv_path = get_csv_path()
        output_dir = get_output_dir(csv_path)
        if CLEAN_OUTPUT_DIR_BEFORE_RUN:
            clean_previous_outputs(output_dir)

        raw_df = read_csv_auto_encoding(csv_path)
        clean_df = clean_raw_dataframe(raw_df)
        clean_df = merge_duplicate_raw_cases(clean_df)
        train_raw, val_raw, test_raw, split_info = split_raw_dataframe(clean_df)

        train_df = build_unified_dataframe(train_raw, "train")
        val_df = build_unified_dataframe(val_raw, "val")
        test_df = build_unified_dataframe(test_raw, "test")

        scaler = build_scaler(train_df)
        x_train, y_train, raw_x_train, y_raw_train, _ = transform_data(train_df, scaler)
        x_val, y_val, raw_x_val, y_raw_val, _ = transform_data(val_df, scaler)
        x_test, y_test, raw_x_test, y_raw_test, direction_names_test = transform_data(
            test_df, scaler
        )

    # 训练函数、超参数、数据划分和选模逻辑与原始代码保持一致。
    model, device, best_loss, history = train_model(x_train, y_train, x_val, y_val)

    silent_buffer.seek(0)
    silent_buffer.truncate(0)

    # 训练结束后重新确认输出目录存在。
    # 这一步只修复文件保存路径问题，不会改变模型参数或训练结果。
    output_dir = ensure_output_dir(output_dir)

    exported_files = []
    onnx_info = {"verification": {"performed": False, "passed": None}}

    with (
        contextlib.nullcontext()
        if os.environ.get("ANN_STREAM_LOG") == "1"
        else contextlib.redirect_stdout(silent_buffer)
    ), (
        contextlib.nullcontext()
        if os.environ.get("ANN_STREAM_LOG") == "1"
        else contextlib.redirect_stderr(silent_buffer)
    ):
        test_prediction = predict_all(model, device, x_test, scaler)
        test_metrics = compute_dtgiven_dssample_metrics(
            y_true=y_raw_test, y_pred=test_prediction
        )
        exported_files.append(save_test_metrics_txt(test_metrics, output_dir))

        save_loss_curve(history, output_dir)
        save_actual_predicted_line_figures(
            y_raw_test, test_prediction, output_dir, "test"
        )
        save_prediction_figures(y_raw_test, test_prediction, output_dir, "test")

        if SAVE_H5_MODEL:
            exported_files.append(save_h5_model(model, scaler, output_dir, history))

        if SAVE_PYTORCH_CHECKPOINT:
            save_checkpoint(model, scaler, best_loss, output_dir, split_info, history)
            exported_files.append(
                os.path.join(output_dir, f"{MODEL_NAME}_checkpoint.pth")
            )

        if SAVE_STATE_DICT:
            exported_files.append(save_state_dict_model(model, scaler, output_dir))

        if SAVE_NUMPY_PARAMETERS:
            exported_files.append(save_numpy_parameters(model, scaler, output_dir))

        # 先导出 ONNX，避免可选的 TorchScript 异常阻断 ONNX/DSP 文件生成。
        if SAVE_ONNX_MODEL:
            onnx_info = export_onnx_models(
                model, scaler, output_dir, raw_x_verify=raw_x_test
            )
            exported_files.extend([onnx_info["core_onnx"], onnx_info["full_onnx"]])
            if onnx_info.get("static_full_onnx"):
                exported_files.append(onnx_info["static_full_onnx"])

        if SAVE_TORCHSCRIPT_MODEL:
            try:
                exported_files.append(save_torchscript_model(model, scaler, output_dir))
            except Exception as exc:
                warnings.warn(
                    "TorchScript export failed, but ONNX and DSP exports will continue. "
                    f"Reason: {exc}"
                )

        if SAVE_INFERENCE_CODE:
            export_python_inference(model, scaler, output_dir)
            export_matlab_inference(model, scaler, output_dir)
            export_c_inference(model, scaler, output_dir)
            exported_files.extend(
                [
                    os.path.join(output_dir, "ann_predict_python.py"),
                    os.path.join(output_dir, "ann_predict_matlab.m"),
                    os.path.join(output_dir, "ann_predict_up_matlab.m"),
                    os.path.join(output_dir, "ann_predict_down_matlab.m"),
                    os.path.join(output_dir, "ann_inference.c"),
                    os.path.join(output_dir, "ann_inference.h"),
                ]
            )

        if SAVE_DSP_DEPLOYMENT_FILES:
            exported_files.append(
                export_dsp_test_vectors(raw_x_test, test_prediction, output_dir)
            )
            exported_files.extend(export_dsp_support_files(output_dir).values())

        deployment_manifest = build_deployment_manifest(
            output_dir, history, best_loss, onnx_info
        )
        if SAVE_DEPLOYMENT_MANIFEST:
            manifest_path = os.path.join(output_dir, "deployment_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(deployment_manifest, f, ensure_ascii=False, indent=4)
            exported_files.append(manifest_path)

        if SAVE_JSON_SUMMARY:
            compact_summary = {
                "model": {
                    "name": MODEL_NAME,
                    "format": "pytorch_ann_portable_hdf5_v1",
                    "input_columns": MODEL_INPUT_COLUMNS,
                    "output_columns": ["DTgiven", "DSsample"],
                    "hidden_dims": list(HIDDEN_DIMS),
                    "dropout": DROPOUT,
                    "cpu_dsp_deployment": True,
                    "npu_used": False,
                },
                "data_split": {
                    "train_samples": int(len(train_df)),
                    "validation_samples": int(len(val_df)),
                    "test_samples": int(len(test_df)),
                    "train_ratio": TRAIN_RATIO,
                    "validation_ratio": VAL_RATIO,
                    "test_ratio": TEST_RATIO,
                },
                "training": {
                    "best_seed": history.get("selected_seed"),
                    "best_epoch": history.get("best_epoch"),
                    "actual_epochs": history.get("actual_epochs"),
                    "stopped_early": history.get("stopped_early"),
                    "best_validation_scaled_mse": float(best_loss),
                },
                "onnx_verification": onnx_info.get("verification", {}),
                "test_metrics": test_metrics,
            }
            summary_path = os.path.join(output_dir, "metrics_summary.json")
            with open(summary_path, "w", encoding="utf-8") as file:
                json.dump(compact_summary, file, ensure_ascii=False, indent=4)
            exported_files.append(summary_path)

    print_dtgiven_dssample_metrics(test_metrics)

    print("\n" + "=" * 78)
    print("Model export finished")
    print("=" * 78)
    print(f"Output directory: {output_dir}")
    print(f"Selected seed   : {history.get('selected_seed')}")
    print(f"Best epoch      : {history.get('best_epoch')}")
    if SAVE_ONNX_MODEL:
        print(f"Static scaler folded: {onnx_info.get('static_scaler_folded', False)}")
        verify = onnx_info.get("verification", {})
        print(f"ONNX verified   : {verify.get('performed')}")
        print(f"ONNX passed     : {verify.get('passed')}")
        if verify.get("core_max_abs_error") is not None:
            print(f"Core ONNX maxAE : {verify['core_max_abs_error']:.8e}")
        if verify.get("full_max_abs_error") is not None:
            print(f"Full ONNX maxAE : {verify['full_max_abs_error']:.8e}")
        if verify.get("static_full_max_abs_error") is not None:
            print(f"Static ONNX maxAE: {verify['static_full_max_abs_error']:.8e}")
    print("\nGenerated model/deployment files:")
    for path in exported_files:
        print("  - " + os.path.basename(path))
