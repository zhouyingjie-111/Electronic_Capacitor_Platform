# -*- coding: utf-8 -*-

import re
from pathlib import Path

import pandas as pd


# ============================================================
# 1. 路径设置
# ============================================================

# 仿真结果所在文件夹
INPUT_DIR = Path(str(Path(__file__).resolve().parents[2] / "InputData"))

# 汇总结果保存路径
OUTPUT_CSV = Path(
    str(Path(__file__).resolve().parents[2] / "OutputData/仿真结果汇总.csv")
)

# 从哪个仿真编号开始处理
MIN_SIMULATION_INDEX = 1


# ============================================================
# 2. 工具函数
# ============================================================


def get_simulation_index(file_path):
    """
    从文件名中提取仿真编号。

    支持：
    仿真结果_0001.csv
    仿真结果_0002.csv
    仿真结果_0001(5).csv

    不符合格式时返回None。
    """
    match = re.search(r"^仿真结果_(\d+)", file_path.stem)

    if match is None:
        return None

    return int(match.group(1))


def get_first_pwm_value(value):
    """
    提取分号分隔PWM数据中的第一个数据。

    例如：
    0.38;0.42;0.51 -> 0.38
    """
    return float(str(value).split(";")[0])


def select_closest_row(df, actual_column, reference_column, direction):
    """
    使用同一行的实际值和参考值进行比较。

    UP方向优先满足：
        reference < actual

    DOWN方向优先满足：
        reference > actual

    如果没有数据满足大小关系，则从全部数据中选择
    实际值与对应参考值绝对差值最小的一行。

    如果多行误差相同，选择原始数据中最后出现的一行。
    """

    if direction == "up":

        candidates = df[df[reference_column] < df[actual_column]].copy()

    else:

        candidates = df[df[reference_column] > df[actual_column]].copy()

    # 没有满足大小关系的数据时，
    # 从去重后的全部数据中寻找最接近的数据
    if candidates.empty:
        candidates = df.copy()

    # 使用同一行的实际值与参考值计算误差
    candidates["_error"] = (
        candidates[actual_column] - candidates[reference_column]
    ).abs()

    # 误差最小优先
    # 误差相同时选择原始数据中最后出现的一行
    candidates = candidates.sort_values(
        by=["_error", "_row_order"], ascending=[True, False]
    )

    return candidates.iloc[0]


# ============================================================
# 3. 处理单个仿真结果文件
# ============================================================


def process_file(file_path):

    # 读取CSV
    df = pd.read_csv(file_path, encoding="utf-8-sig")

    original_row_count = len(df)

    # CSV第一行为表头
    # df.iloc[0]对应表头下面的第一条数据
    # 删除该条数据
    df = df.iloc[1:].copy()

    after_delete_row_count = len(df)

    # 记录每行在原始文件中的顺序
    df["_row_order"] = range(len(df))

    # 需要转换为数值类型的字段
    numeric_columns = [
        "P_peak",
        "T_rise_fall",
        "S_slope",
        "T_pulse",
        "D_T_UP_given",
        "D_T_DOWN_given",
        "Iin_Pulse_UP",
        "Iin_Pulse_DOWN",
        "Iin_Pulse_UP_ref",
        "Iin_Pulse_DOWN_ref",
        "VC1_UP",
        "VC1_DOWN",
        "VG1_UP",
        "VG1_DOWN",
    ]

    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")

    # ========================================================
    # 按D_T_UP_given与D_T_DOWN_given组合去重
    #
    # 当两个占空比同时相同时，
    # 只保留原始数据中最后出现的一行
    # ========================================================
    filtered_df = df.drop_duplicates(
        subset=["D_T_UP_given", "D_T_DOWN_given"], keep="last"
    ).copy()

    filtered_row_count = len(filtered_df)

    # ========================================================
    # UP方向选择
    #
    # 优先满足：
    # Iin_Pulse_UP_ref < Iin_Pulse_UP
    #
    # 然后选择同一行两者差值最小的数据
    #
    # 若没有满足大小关系的数据，
    # 则直接从全部去重数据中选择最相近的数据
    # ========================================================
    up_row = select_closest_row(
        df=filtered_df,
        actual_column="Iin_Pulse_UP",
        reference_column="Iin_Pulse_UP_ref",
        direction="up",
    )

    # ========================================================
    # DOWN方向选择
    #
    # 优先满足：
    # Iin_Pulse_DOWN_ref > Iin_Pulse_DOWN
    #
    # 然后选择同一行两者差值最小的数据
    #
    # 若没有满足大小关系的数据，
    # 则直接从全部去重数据中选择最相近的数据
    # ========================================================
    down_row = select_closest_row(
        df=filtered_df,
        actual_column="Iin_Pulse_DOWN",
        reference_column="Iin_Pulse_DOWN_ref",
        direction="down",
    )

    simulation_index = get_simulation_index(file_path)

    # ========================================================
    # 将单个仿真结果文件汇总为一行
    # 输出CSV的字段名称和顺序保持不变
    # ========================================================
    result = {
        "仿真文件": file_path.name,
        "仿真编号": simulation_index,
        # 仿真输入参数
        "P_peak": filtered_df["P_peak"].iloc[0],
        "T_rise_fall": filtered_df["T_rise_fall"].iloc[0],
        "S_slope": filtered_df["S_slope"].iloc[0],
        "T_pulse": filtered_df["T_pulse"].iloc[0],
        # ====================================================
        # UP方向结果
        # ====================================================
        # 保持原CSV字段名不变
        # 实际保存被选中数据行中的Iin_Pulse_UP_ref
        "Iin_Pulse_UP_ref_min": up_row["Iin_Pulse_UP_ref"],
        "D_T_UP_given": up_row["D_T_UP_given"],
        "Iin_Pulse_UP_selected": up_row["Iin_Pulse_UP"],
        # 被选中行中，实际值与参考值的绝对误差
        "Iin_Pulse_UP_abs_error": abs(
            up_row["Iin_Pulse_UP"] - up_row["Iin_Pulse_UP_ref"]
        ),
        "PWM_S_UP_first": get_first_pwm_value(up_row["PWM_S_UP"]),
        "VC1_UP": up_row["VC1_UP"],
        "VG1_UP": up_row["VG1_UP"],
        # ====================================================
        # DOWN方向结果
        # ====================================================
        # 保持原CSV字段名不变
        # 实际保存被选中数据行中的Iin_Pulse_DOWN_ref
        "Iin_Pulse_DOWN_ref_max": down_row["Iin_Pulse_DOWN_ref"],
        "D_T_DOWN_given": down_row["D_T_DOWN_given"],
        "Iin_Pulse_DOWN_selected": down_row["Iin_Pulse_DOWN"],
        # 被选中行中，实际值与参考值的绝对误差
        "Iin_Pulse_DOWN_abs_error": abs(
            down_row["Iin_Pulse_DOWN"] - down_row["Iin_Pulse_DOWN_ref"]
        ),
        "PWM_S_DOWN_first": get_first_pwm_value(down_row["PWM_S_DOWN"]),
        "VC1_DOWN": down_row["VC1_DOWN"],
        "VG1_DOWN": down_row["VG1_DOWN"],
        # 数据行数统计
        "原始数据行数": original_row_count,
        "忽略第二行后行数": after_delete_row_count,
        "组合去重后行数": filtered_row_count,
    }

    return result


# ============================================================
# 4. 主程序
# ============================================================


def main():

    files = []

    # 获取所有可能的仿真结果文件
    for file_path in INPUT_DIR.glob("仿真结果_*.csv"):

        simulation_index = get_simulation_index(file_path)

        # 跳过不符合“仿真结果_数字”格式的文件
        if simulation_index is None:
            continue

        # 跳过指定编号之前的文件
        if simulation_index < MIN_SIMULATION_INDEX:
            continue

        # 防止输出文件被重新读取
        if file_path.resolve() == OUTPUT_CSV.resolve():
            continue

        files.append(file_path)

    # 按0001、0002、0003……顺序排列
    files.sort(key=get_simulation_index)

    results = []

    # 逐个处理仿真结果文件
    for file_path in files:

        result = process_file(file_path)

        results.append(result)

        print(
            f"{file_path.name}："
            f"D_T_UP_given = "
            f"{result['D_T_UP_given']}，"
            f"D_T_DOWN_given = "
            f"{result['D_T_DOWN_given']}"
        )

    # 生成汇总数据集
    summary_df = pd.DataFrame(results)

    # 按仿真编号排序
    summary_df = summary_df.sort_values(by="仿真编号")

    # 创建输出文件夹
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    # 保存汇总CSV
    summary_df.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print()

    print(f"汇总完成，共处理" f"{len(summary_df)}个仿真文件。")

    print(f"汇总文件保存位置：" f"{OUTPUT_CSV}")


# ============================================================
# 5. 程序入口
# ============================================================

if __name__ == "__main__":
    main()
