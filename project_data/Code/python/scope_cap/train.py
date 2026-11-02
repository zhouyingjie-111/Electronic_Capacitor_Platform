"""Run: python scope_cap/train.py (uses config.json beside this script)."""

import argparse
from pathlib import Path
import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
import csv
import time
import tempfile
import numpy as np
from pipeline import load_config, prepare, json_write, NAMES


def metrics(y, pred):
    result = {}
    for k, name in enumerate(NAMES):
        err = pred[:, k].astype(float) - y[:, k]
        denom = np.sum((y[:, k].astype(float) - np.mean(y[:, k].astype(float))) ** 2)
        result[name] = dict(
            n=len(y),
            mre_percent=float(100 * np.mean(np.abs(err / y[:, k]))),
            rmse=float(np.sqrt(np.mean(err**2))),
            r2=None if denom < 1e-12 else float(1 - np.sum(err**2) / denom),
            maximum_relative_error_percent=float(100 * np.max(np.abs(err / y[:, k]))),
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default=str(Path(__file__).with_name("config.json"))
    )
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--epochs", type=int)
    args = parser.parse_args()
    cfg = load_config(args.config)
    if args.epochs is not None:
        cfg["epochs"] = args.epochs
    out = Path(cfg["output_dir"])
    # Do not silently overwrite a previous trained release.
    if (out / "model.keras").exists():
        out = out.parent / (out.name + "_" + time.strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    # Keras creates temporary asset files even for small models. Keep them
    # in a known writable workspace rather than a restricted system TEMP.
    temp = Path(__file__).resolve().parent / ".tmp"
    temp.mkdir(exist_ok=True)
    tempfile.tempdir = str(temp)
    json_write(out / "resolved_config.json", cfg)
    images, labels, split, norm, records, raw = prepare(cfg, out)
    if args.prepare_only:
        return
    import tensorflow as tf

    tf.keras.utils.set_random_seed(cfg["seed"])
    tf.config.threading.set_intra_op_parallelism_threads(2)
    tf.config.threading.set_inter_op_parallelism_threads(2)
    tr, va, te = [split == i for i in range(3)]
    mean = labels[tr].mean(0)
    std = labels[tr].std(0)
    constant = np.ptp(labels[tr], axis=0) < 1e-6
    std = np.where(constant, 1, std).astype(np.float32)
    # Explicit zero padding gives exact, documented C padding for even shapes.
    inputs = tf.keras.Input((6, 32, 3), name="cap_image")
    x = tf.keras.layers.ZeroPadding2D(((0, 1), (0, 1)))(inputs)
    x = tf.keras.layers.Conv2D(8, 3, strides=2, activation="relu", name="conv1")(x)
    x = tf.keras.layers.ZeroPadding2D(((1, 1), (0, 1)))(x)
    x = tf.keras.layers.Conv2D(16, 3, strides=2, activation="relu", name="conv2")(x)
    x = tf.keras.layers.GlobalAveragePooling2D()(x)
    outputs = tf.keras.layers.Dense(8, name="regression")(x)
    model = tf.keras.Model(inputs, outputs, name="CapArrayCNN1528")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(cfg["learning_rate"]),
        loss=tf.keras.losses.Huber(),
    )
    model.summary()
    z = (labels - mean) / std
    history = model.fit(
        images[tr],
        z[tr],
        validation_data=(images[va], z[va]),
        epochs=cfg["epochs"],
        batch_size=cfg["batch_size"],
        verbose=2,
        callbacks=[
            tf.keras.callbacks.EarlyStopping(
                monitor="val_loss", patience=cfg["patience"], restore_best_weights=True
            ),
            tf.keras.callbacks.ReduceLROnPlateau(
                monitor="val_loss", patience=20, factor=0.5, min_lr=1e-5
            ),
        ],
    )
    # Unchanged C1/C3/ESR1/ESR3 are constants in this dataset. Enforce known
    # constant heads, and explicitly exclude them from claims of learning.
    w, b = model.get_layer("regression").get_weights()
    w[:, constant] = 0
    b[constant] = 0
    model.get_layer("regression").set_weights([w, b])
    mean[constant] = labels[tr][0, constant]
    model.save(out / "model.keras")
    pred = model.predict(images, verbose=0) * std + mean
    baseline = np.broadcast_to(labels[tr].mean(0), labels.shape)
    report = dict(
        evaluation_scope="chronological holdout within the SAME acquisitions; NOT independent hardware validation",
        outputs=NAMES,
        constant_outputs=[NAMES[i] for i in np.flatnonzero(constant)],
        limitation="Only 3 known configurations. C and ESR co-vary; independent ESR identification and unseen states are unvalidated.",
        train=metrics(labels[tr], pred[tr]),
        validation=metrics(labels[va], pred[va]),
        test=metrics(labels[te], pred[te]),
        test_mean_baseline=metrics(labels[te], baseline[te]),
        parameter_count=model.count_params(),
        cnn_mac=28928,
        image_shape=[6, 32, 3],
        window_ms=10.24,
        sample_dt_s=1e-5,
        tensorflow_version=tf.__version__,
        selected_epoch=int(np.argmin(history.history["val_loss"]) + 1),
        epochs_run=len(history.history["loss"]),
    )
    json_write(out / "metrics.json", report)
    lines = [
        "# 训练结果与使用范围",
        "",
        "这是同一次采集内的时间段留出测试，不是独立重新采集的实物测试。",
        f"训练/验证/测试窗口数：{tr.sum()}/{va.sum()}/{te.sum()}。",
        "参数量1,528；每次CNN约28,928 MAC；输入6×32×3；采样100 kHz；窗口10.24 ms。",
        "输出C为μF，ESR为Ω。第一、第三位置无变化，输出为已知常量，其零误差不表示泛化辨识能力。",
        "",
        "|输出|测试MRE/%|测试RMSE|R²|",
        "|---|---:|---:|---:|",
    ]
    for name, m in report["test"].items():
        r2 = "不适用" if m["r2"] is None else f'{m["r2"]:.5f}'
        lines.append(f'|{name}|{m["mre_percent"]:.4f}|{m["rmse"]:.6g}|{r2}|')
    lines += [
        "",
        "只有三种已知组合，且C和ESR同步变化；未验证其他位置变化、未见容值或独立ESR变化。",
        "C源码及权重在dsp目录。执行verify_export.py后可生成C28x静态库；板上链接、运行时间、模拟前端一致性与控制并行仍需实测。",
        "上板前用cap_golden.c的固定输入核对输出；不要直接将model.keras传入现有tvmgen_default_run。",
    ]
    (out / "训练结果说明.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_write(out / "history.json", history.history)
    np.savez(out / "preprocess.npz", **norm, label_mean=mean, label_std=std)
    with (out / "predictions.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["group", "split", "start_s"]
            + [f"true_{s}" for s in NAMES]
            + [f"pred_{s}" for s in NAMES]
        )
        for rec, y, pr in zip(records, labels, pred):
            writer.writerow([rec["group"], rec["split"], rec["start_s"], *y, *pr])
    from export_c import export

    export(model, norm, mean, std, images, raw, pred, out, cfg)
    print(f"\nOUTPUT: {out}", flush=True)
    print(
        "Test MRE (%)",
        {k: round(v["mre_percent"], 3) for k, v in report["test"].items()},
    )


if __name__ == "__main__":
    main()
