# AST-only regression reference; not an executable training entry.
UP_TARGET_COLUMNS = ["D_T_UP_given", "PWM_S_UP_first"]

DOWN_TARGET_COLUMNS = ["D_T_DOWN_given", "PWM_S_DOWN_first"]

MODEL_OUTPUT_COLUMNS = ["D_T_given", "PWM_S_first"]

EPOCHS = 15000

LEARNING_RATE = 8e-4

BATCH_SIZE = 64

RANDOM_SEED = 42

TRAINING_SEEDS = (42, 142, 242)

HIDDEN_DIMS = (8, 4)

DROPOUT = 0.05

WEIGHT_DECAY = 1e-4

EARLY_STOPPING_PATIENCE = 700

SCHEDULER_PATIENCE = 200

def split_raw_dataframe(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict[str, List[int]]]:
    if abs(TRAIN_RATIO + VAL_RATIO + TEST_RATIO - 1.0) > 1e-8:
        raise ValueError("TRAIN_RATIO + VAL_RATIO + TEST_RATIO must equal 1.0")

    n = len(df)
    if n < 5:
        raise ValueError("Dataset is too small for train/val/test split. Need at least 5 raw rows.")

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
    val_idx = perm[n_train:n_train + n_val]
    test_idx = perm[n_train + n_val:]

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

def build_scaler(train_df: pd.DataFrame) -> Dict[str, np.ndarray]:
    # 只允许使用训练集计算 scaler，避免验证/测试集信息泄漏
    x_cont = train_df[["P_peak", "T_rise_fall", "VC1", "VG1"]].to_numpy(dtype=np.float32)
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

def make_feature_matrix_from_raw(raw_x: np.ndarray, scaler: Dict[str, np.ndarray]) -> np.ndarray:
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

    x_norm = (
        x_cont - scaler["x_mean"].reshape(1, 4)
    ) / scaler["x_std"].reshape(1, 4)

    features = np.concatenate([x_norm, direction], axis=1).astype(np.float32)

    if features.shape[1] != FEATURE_DIM:
        raise RuntimeError(
            f"Feature dimension error: got {features.shape[1]}, expected {FEATURE_DIM}"
        )

    return features

def train_model(x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray):
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
        final_val_loss = evaluate_scaled_loss(
            model, criterion, x_val, y_val, device
        )

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

        restart_summaries.append({
            "seed": int(seed),
            "best_epoch": int(best_epoch),
            "actual_epochs": int(len(train_loss_history)),
            "train_loss": float(final_train_loss),
            "val_loss": float(final_val_loss),
            "stopped_early": bool(stopped_early),
        })

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
    final_val_loss = evaluate_scaled_loss(
        final_model, criterion, x_val, y_val, device
    )

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