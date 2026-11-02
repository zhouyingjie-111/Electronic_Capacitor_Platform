# -*- coding: utf-8 -*-
import numpy as np

PARAMS = {
    "model_input_columns": [
        "P_peak",
        "T_rise_fall",
        "VC1",
        "VG1",
        "direction_state"
    ],
    "model_output_columns": [
        "D_T_given",
        "PWM_S_first"
    ],
    "feature_dim": 5,
    "x_mean": [
        21.256410598754883,
        5.538462573895231e-05,
        60.07332992553711,
        210.072509765625
    ],
    "x_std": [
        1.9705256223678589,
        2.3949321985128336e-05,
        13.766656875610352,
        14.719820976257324
    ],
    "y_mean": [
        0.30152565240859985,
        0.24089741706848145
    ],
    "y_std": [
        0.2194908857345581,
        0.14678771793842316
    ],
    "layers": [
        {
            "W": [
                [
                    -0.02974224090576172,
                    -0.5850991606712341,
                    0.4191196858882904,
                    -0.21377190947532654,
                    -0.7830085754394531
                ],
                [
                    0.0963902696967125,
                    -0.12376085668802261,
                    -0.11387274414300919,
                    0.4367240071296692,
                    0.29646947979927063
                ],
                [
                    0.14159514009952545,
                    -1.4371709823608398,
                    -0.09224680811166763,
                    -0.8030892014503479,
                    0.0014062810223549604
                ],
                [
                    0.4393209218978882,
                    -0.21483857929706573,
                    0.19793671369552612,
                    0.43902942538261414,
                    -0.9612153768539429
                ],
                [
                    -0.2424791157245636,
                    0.5824111700057983,
                    0.3072154223918915,
                    1.1028438806533813,
                    0.3733201324939728
                ],
                [
                    -0.08307575434446335,
                    -0.9632778763771057,
                    -0.486954003572464,
                    0.4531228244304657,
                    -0.2310408651828766
                ]
            ],
            "b": [
                -0.1536468267440796,
                -0.001058558700606227,
                0.11648523807525635,
                -0.2498456835746765,
                0.06377463787794113,
                -0.4690774977207184
            ]
        },
        {
            "W": [
                [
                    -0.7043591141700745,
                    0.6294541358947754,
                    0.023820538073778152,
                    -0.6006456613540649,
                    0.06994639337062836,
                    0.9812217354774475
                ],
                [
                    0.13408398628234863,
                    0.5984945297241211,
                    -0.5545603036880493,
                    0.1899566352367401,
                    0.4419625699520111,
                    0.1517929583787918
                ]
            ],
            "b": [
                -0.38758936524391174,
                -0.20936405658721924
            ]
        }
    ]
}

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
