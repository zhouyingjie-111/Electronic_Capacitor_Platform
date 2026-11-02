%% 工作区预处理

clc;
clear;
close all;
warning('off');

%% 参数设置

model_path1 = fullfile(fileparts(fileparts(fileparts(mfilename('fullpath')))), 'Simulink/DualBuckPPS22016');
model_path2 = fullfile(fileparts(fileparts(fileparts(mfilename('fullpath')))), 'Simulink/DualBuckPPS22016_define');

save_path = fullfile(fileparts(fileparts(fileparts(mfilename('fullpath')))), 'InputData');

if ~exist(save_path, 'dir')
    mkdir(save_path);
end

sum_num = 100;
batch_num = 20;
num_batches = ceil(sum_num / batch_num);

t_stop_original = 0.5;
t_stop_define = 3.5;

D_change_start_time = 0.5;
D_update_cycle_num = 4;

avg_num = 10;

P_set = 12:1:26;
T_set = (20:10:80) * 1e-6;

T_pulse_set = 1/50;

% 占空比围绕参考值左右波动范围
D_delta_range = 0.08;
D_min = 0;
D_max = 1;
D_step = 0.001;

saw_peak = 1e-5;

cell_delimiter = ';';
num_format = '%.9g';

eps_time = 1e-12;

rng(42);

%% 生成不重复的 P_peak 和 T_rise_fall 组合

[P_grid, T_grid] = ndgrid(P_set, T_set);
param_table_all = [P_grid(:), T_grid(:)];

total_param_num = size(param_table_all, 1);

if sum_num > total_param_num
    error('sum_num 超过 P_set 和 T_set 可组成的不重复组合数量，请减小 sum_num 或扩大 P_set / T_set。');
end

param_rand_idx = randperm(total_param_num, sum_num);
param_table_used = param_table_all(param_rand_idx, :);

%% 输出信号名称

% 原始模型需要输出：
% 1. PWM_T_UP / PWM_T_DOWN 用于计算参考占空比；
% 2. Iin_Pulse_UP / Iin_Pulse_DOWN / Iin_Pulse_UP_ref / Iin_Pulse_DOWN_ref
%    用于把原始模型最后时刻尖峰数据插入CSV第一行。
original_signal_names = { ...
    'PWM_T_UP', ...
    'PWM_T_DOWN', ...
    'Iin_Pulse_UP', ...
    'Iin_Pulse_DOWN', ...
    'Iin_Pulse_UP_ref', ...
    'Iin_Pulse_DOWN_ref' ...
};

define_signal_names = { ...
    'Iin_Pulse_UP', ...
    'Iin_Pulse_DOWN', ...
    'Iin_Pulse_UP_ref', ...
    'Iin_Pulse_DOWN_ref', ...
    'PWM_S_UP', ...
    'PWM_S_DOWN', ...
    'VC1_UP', ...
    'VC1_DOWN', ...
    'VG1_UP', ...
    'VG1_DOWN' ...
};

%% 加载模型

[~, model1, ~] = fileparts(model_path1);
[~, model2, ~] = fileparts(model_path2);

load_system(model_path1);
load_system(model_path2);

set_param(model2, 'FastRestart', 'off');

%% 批量仿真

for batch = 1:num_batches

    start_idx = (batch - 1) * batch_num + 1;
    end_idx = min(batch * batch_num, sum_num);

    for i = start_idx:end_idx

        %% 输入参数，不会重复

        P_peak = param_table_used(i, 1);
        T_rise_fall = param_table_used(i, 2);

        S_slope = P_peak / T_rise_fall;
        T_pulse = T_pulse_set(randi(length(T_pulse_set)));

        pulse_num = ceil(t_stop_define / T_pulse);

        fprintf(['第 %d 次仿真开始：', ...
                 'P_peak = %.2f A, T_rise_fall = %.6f us, ', ...
                 'S_slope = %.6f A/us, T_pulse = %.6f s\n'], ...
                 i, P_peak, T_rise_fall*1e6, S_slope/1e6, T_pulse);

        %% 1. 原始模型连续仿真

        simIn1 = Simulink.SimulationInput(model1);

        simIn1 = simIn1.setModelParameter( ...
            'StopTime', num2str(t_stop_original), ...
            'ReturnWorkspaceOutputs', 'on' ...
        );

        simIn1 = simIn1.setVariable('P', P_peak);
        simIn1 = simIn1.setVariable('TP', T_rise_fall);
        simIn1 = simIn1.setVariable('T_pulse', T_pulse);

        simOut1 = sim(simIn1);

        original_sig = struct();

        for s = 1:length(original_signal_names)
            sig_name = original_signal_names{s};
            original_sig.(sig_name) = get_signal(simOut1, sig_name);
        end

        D_T_UP_ref = last_mean(original_sig.PWM_T_UP, avg_num) / saw_peak;
        D_T_DOWN_ref = last_mean(original_sig.PWM_T_DOWN, avg_num) / saw_peak;

        D_T_UP_ref = limit_duty(D_T_UP_ref, D_min, D_max, D_step);
        D_T_DOWN_ref = limit_duty(D_T_DOWN_ref, D_min, D_max, D_step);

        D_T_UP_ref_pwm = D_T_UP_ref * saw_peak;
        D_T_DOWN_ref_pwm = D_T_DOWN_ref * saw_peak;

        % 原始模型最后时刻对应的尖峰数据，用于插入最终CSV第一行
        original_Iin_Pulse_UP_final = signal_last_to_string( ...
            original_sig.Iin_Pulse_UP, num_format);

        original_Iin_Pulse_DOWN_final = signal_last_to_string( ...
            original_sig.Iin_Pulse_DOWN, num_format);

        original_Iin_Pulse_UP_ref_final = signal_last_to_string( ...
            original_sig.Iin_Pulse_UP_ref, num_format);

        original_Iin_Pulse_DOWN_ref_final = signal_last_to_string( ...
            original_sig.Iin_Pulse_DOWN_ref, num_format);

        % 原始模型行对应的最后时刻信息
        original_pulse_index = ceil(t_stop_original / T_pulse);
        original_segment_start_time = max(0, (original_pulse_index - 1) * T_pulse);
        original_segment_stop_time = t_stop_original;

        fprintf(['原始模型完成：D_T_UP_ref = %.6f, D_T_DOWN_ref = %.6f，', ...
                 '最后时刻 Iin_UP = %s, Iin_DOWN = %s, ', ...
                 'Iin_UP_ref = %s, Iin_DOWN_ref = %s\n'], ...
                D_T_UP_ref, ...
                D_T_DOWN_ref, ...
                original_Iin_Pulse_UP_final, ...
                original_Iin_Pulse_DOWN_final, ...
                original_Iin_Pulse_UP_ref_final, ...
                original_Iin_Pulse_DOWN_ref_final);

        %% 2. 生成每个脉冲周期对应的给定占空比

        D_T_UP_duty_seq = zeros(pulse_num, 1);
        D_T_DOWN_duty_seq = zeros(pulse_num, 1);
        duty_group_index_seq = zeros(pulse_num, 1);

        current_group = -1;
        current_D_T_UP_duty = D_T_UP_ref;
        current_D_T_DOWN_duty = D_T_DOWN_ref;

        used_duty_keys = {};

        for pulse_idx = 1:pulse_num

            pulse_start_time = (pulse_idx - 1) * T_pulse;

            if pulse_start_time < D_change_start_time - eps_time

                D_T_UP_duty = D_T_UP_ref;
                D_T_DOWN_duty = D_T_DOWN_ref;
                duty_group_index = 0;

            else

                duty_group_index = floor((pulse_start_time - D_change_start_time) / ...
                    (D_update_cycle_num * T_pulse)) + 1;

                if duty_group_index ~= current_group

                    current_group = duty_group_index;

                    [current_D_T_UP_duty, current_D_T_DOWN_duty, used_duty_keys] = ...
                        generate_unique_duty_pair( ...
                            D_T_UP_ref, ...
                            D_T_DOWN_ref, ...
                            D_delta_range, ...
                            D_min, ...
                            D_max, ...
                            D_step, ...
                            used_duty_keys ...
                        );

                end

                D_T_UP_duty = current_D_T_UP_duty;
                D_T_DOWN_duty = current_D_T_DOWN_duty;

            end

            D_T_UP_duty_seq(pulse_idx, 1) = D_T_UP_duty;
            D_T_DOWN_duty_seq(pulse_idx, 1) = D_T_DOWN_duty;
            duty_group_index_seq(pulse_idx, 1) = duty_group_index;

        end

        D_T_UP_pwm_seq = D_T_UP_duty_seq * saw_peak;
        D_T_DOWN_pwm_seq = D_T_DOWN_duty_seq * saw_peak;

        %% 3. 初始化保存变量
        % 注意：
        % 这里直接插入第1行原始模型数据。
        % 表头不变，只是第一行 duty_group_index = -1，
        % 用于标识该行来自原始模型，而不是自定义模型分段仿真。

        index_save = i;
        pulse_index_save = original_pulse_index;
        segment_start_time_save = original_segment_start_time;
        segment_stop_time_save = original_segment_stop_time;
        duty_group_index_save = -1;

        P_peak_save = P_peak;
        T_rise_fall_save = T_rise_fall;
        S_slope_save = S_slope;
        T_pulse_save = T_pulse;

        D_T_UP_ref_save = D_T_UP_ref;
        D_T_DOWN_ref_save = D_T_DOWN_ref;

        D_T_UP_given_save = D_T_UP_ref;
        D_T_DOWN_given_save = D_T_DOWN_ref;

        D_T_UP_Given_pwm_save = D_T_UP_ref_pwm;
        D_T_DOWN_Given_pwm_save = D_T_DOWN_ref_pwm;

        Iin_Pulse_UP_save = {original_Iin_Pulse_UP_final};
        Iin_Pulse_DOWN_save = {original_Iin_Pulse_DOWN_final};
        Iin_Pulse_UP_ref_save = {original_Iin_Pulse_UP_ref_final};
        Iin_Pulse_DOWN_ref_save = {original_Iin_Pulse_DOWN_ref_final};

        PWM_S_UP_save = {''};
        PWM_S_DOWN_save = {''};
        VC1_UP_save = {''};
        VC1_DOWN_save = {''};
        VG1_UP_save = {''};
        VG1_DOWN_save = {''};

        %% 4. 自定义模型按占空比不变区间分段仿真

        x_last = [];
        pulse_idx = 1;

        while pulse_idx <= pulse_num

            current_group = duty_group_index_seq(pulse_idx);

            segment_start_pulse = pulse_idx;
            segment_stop_pulse = pulse_idx;

            while segment_stop_pulse < pulse_num && ...
                  duty_group_index_seq(segment_stop_pulse + 1) == current_group

                segment_stop_pulse = segment_stop_pulse + 1;

            end

            seg_start = (segment_start_pulse - 1) * T_pulse;
            seg_stop = min(segment_stop_pulse * T_pulse, t_stop_define);

            D_T_UP_Given = D_T_UP_pwm_seq(segment_start_pulse);
            D_T_DOWN_Given = D_T_DOWN_pwm_seq(segment_start_pulse);

            simIn2 = Simulink.SimulationInput(model2);

            simIn2 = simIn2.setModelParameter( ...
                'StartTime', num2str(seg_start), ...
                'StopTime', num2str(seg_stop), ...
                'ReturnWorkspaceOutputs', 'on', ...
                'SaveFinalState', 'on', ...
                'FinalStateName', 'xFinal', ...
                'SaveCompleteFinalSimState', 'on' ...
            );

            simIn2 = simIn2.setVariable('P', P_peak);
            simIn2 = simIn2.setVariable('TP', T_rise_fall);
            simIn2 = simIn2.setVariable('T_pulse', T_pulse);

            simIn2 = simIn2.setVariable('D_T_UP_Given', D_T_UP_Given);
            simIn2 = simIn2.setVariable('D_T_DOWN_Given', D_T_DOWN_Given);

            if ~isempty(x_last)
                simIn2 = simIn2.setInitialState(x_last);
            end

            simOut2_seg = sim(simIn2);
            x_last = simOut2_seg.xFinal;

            define_sig = struct();

            for s = 1:length(define_signal_names)
                sig_name = define_signal_names{s};
                define_sig.(sig_name) = get_signal(simOut2_seg, sig_name);
            end

            local_pulse_num = segment_stop_pulse - segment_start_pulse + 1;

            for local_idx = 1:local_pulse_num

                global_pulse_idx = segment_start_pulse + local_idx - 1;

                pulse_start_time = (global_pulse_idx - 1) * T_pulse;
                pulse_stop_time = min(global_pulse_idx * T_pulse, t_stop_define);

                is_boundary_pulse = pulse_start_time < D_change_start_time && ...
                                    pulse_stop_time >= D_change_start_time - eps_time;

                is_after_change_pulse = pulse_start_time >= D_change_start_time - eps_time;

                if ~(is_boundary_pulse || is_after_change_pulse)
                    continue;
                end

                index_save(end+1,1) = i;
                pulse_index_save(end+1,1) = global_pulse_idx;
                segment_start_time_save(end+1,1) = pulse_start_time;
                segment_stop_time_save(end+1,1) = pulse_stop_time;
                duty_group_index_save(end+1,1) = duty_group_index_seq(global_pulse_idx);

                P_peak_save(end+1,1) = P_peak;
                T_rise_fall_save(end+1,1) = T_rise_fall;
                S_slope_save(end+1,1) = S_slope;
                T_pulse_save(end+1,1) = T_pulse;

                D_T_UP_ref_save(end+1,1) = D_T_UP_ref;
                D_T_DOWN_ref_save(end+1,1) = D_T_DOWN_ref;

                D_T_UP_given_save(end+1,1) = D_T_UP_duty_seq(global_pulse_idx);
                D_T_DOWN_given_save(end+1,1) = D_T_DOWN_duty_seq(global_pulse_idx);

                D_T_UP_Given_pwm_save(end+1,1) = D_T_UP_pwm_seq(global_pulse_idx);
                D_T_DOWN_Given_pwm_save(end+1,1) = D_T_DOWN_pwm_seq(global_pulse_idx);

                Iin_Pulse_UP_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.Iin_Pulse_UP, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                Iin_Pulse_DOWN_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.Iin_Pulse_DOWN, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                Iin_Pulse_UP_ref_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.Iin_Pulse_UP_ref, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                Iin_Pulse_DOWN_ref_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.Iin_Pulse_DOWN_ref, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                PWM_S_UP_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.PWM_S_UP, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                PWM_S_DOWN_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.PWM_S_DOWN, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                VC1_UP_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.VC1_UP, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                VC1_DOWN_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.VC1_DOWN, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                VG1_UP_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.VG1_UP, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

                VG1_DOWN_save{end+1,1} = pulse_data_to_string( ...
                    define_sig.VG1_DOWN, local_idx, local_pulse_num, ...
                    cell_delimiter, num_format);

            end

            fprintf(['自定义模型分段完成：%.6f s ~ %.6f s，', ...
                     '脉冲 %d ~ %d，D_UP = %.6f，D_DOWN = %.6f\n'], ...
                     seg_start, seg_stop, ...
                     segment_start_pulse, segment_stop_pulse, ...
                     D_T_UP_duty_seq(segment_start_pulse), ...
                     D_T_DOWN_duty_seq(segment_start_pulse));

            pulse_idx = segment_stop_pulse + 1;

        end

        %% 5. 保存 CSV

        result_table = table( ...
            index_save, ...
            pulse_index_save, ...
            segment_start_time_save, ...
            segment_stop_time_save, ...
            duty_group_index_save, ...
            P_peak_save, ...
            T_rise_fall_save, ...
            S_slope_save, ...
            T_pulse_save, ...
            D_T_UP_ref_save, ...
            D_T_DOWN_ref_save, ...
            D_T_UP_given_save, ...
            D_T_DOWN_given_save, ...
            D_T_UP_Given_pwm_save, ...
            D_T_DOWN_Given_pwm_save, ...
            Iin_Pulse_UP_save, ...
            Iin_Pulse_DOWN_save, ...
            Iin_Pulse_UP_ref_save, ...
            Iin_Pulse_DOWN_ref_save, ...
            PWM_S_UP_save, ...
            PWM_S_DOWN_save, ...
            VC1_UP_save, ...
            VC1_DOWN_save, ...
            VG1_UP_save, ...
            VG1_DOWN_save, ...
            'VariableNames', { ...
                'index', ...
                'pulse_index', ...
                'segment_start_time', ...
                'segment_stop_time', ...
                'duty_group_index', ...
                'P_peak', ...
                'T_rise_fall', ...
                'S_slope', ...
                'T_pulse', ...
                'D_T_UP_ref', ...
                'D_T_DOWN_ref', ...
                'D_T_UP_given', ...
                'D_T_DOWN_given', ...
                'D_T_UP_Given_pwm', ...
                'D_T_DOWN_Given_pwm', ...
                'Iin_Pulse_UP', ...
                'Iin_Pulse_DOWN', ...
                'Iin_Pulse_UP_ref', ...
                'Iin_Pulse_DOWN_ref', ...
                'PWM_S_UP', ...
                'PWM_S_DOWN', ...
                'VC1_UP', ...
                'VC1_DOWN', ...
                'VG1_UP', ...
                'VG1_DOWN' ...
            } ...
        );

        output_file = fullfile(save_path, sprintf('仿真结果_%04d.csv', i));
        writetable(result_table, output_file);

        fprintf('第 %d 次仿真完成，结果已保存：%s\n\n', i, output_file);

    end

    fprintf('第 %d/%d 批数据完成。\n\n', batch, num_batches);

end

set_param(model2, 'FastRestart', 'off');

fprintf('全部仿真完成。\n');

warning('on');

%% 函数区

function sig = get_signal(simOut, sig_name)

    try
        sig = simOut.get(sig_name);
    catch
        sig = simOut.(sig_name);
    end

end

function y = last_mean(x, N)

    y_data = signal_vector(x);

    if isempty(y_data)
        y = NaN;
        return;
    end

    n = min(N, length(y_data));
    y = mean(y_data(end-n+1:end));

end

function y = limit_duty(y, D_min, D_max, D_step)

    y = min(max(y, D_min), D_max);
    y = round(y / D_step) * D_step;
    y = min(max(y, D_min), D_max);

end

function [D_up, D_down, used_duty_keys] = generate_unique_duty_pair( ...
    D_up_ref, D_down_ref, D_delta_range, D_min, D_max, D_step, used_duty_keys)

    max_try = 10000;

    for try_idx = 1:max_try

        D_up = D_up_ref + (2 * rand - 1) * D_delta_range;
        D_down = D_down_ref + (2 * rand - 1) * D_delta_range;

        D_up = limit_duty(D_up, D_min, D_max, D_step);
        D_down = limit_duty(D_down, D_min, D_max, D_step);

        duty_key = sprintf('%.4f_%.4f', D_up, D_down);

        if ~ismember(duty_key, used_duty_keys)
            used_duty_keys{end+1,1} = duty_key;
            return;
        end

    end

    error('当前参考占空比附近可用的不重复随机占空比组合不足，请增大 D_delta_range 或减小 D_step。');

end

function y = signal_vector(x)

    if isa(x, 'Simulink.SimulationData.Signal')
        x = x.Values;
    end

    if isa(x, 'timeseries')
        x = x.Data;
    end

    if isstruct(x)

        if isfield(x, 'signals')
            x = x.signals.values;
        elseif isfield(x, 'Data')
            x = x.Data;
        end

    end

    x = squeeze(x);

    if isempty(x)
        y = [];
        return;
    end

    if ~isvector(x)
        x = x(:);
    end

    y = x(:);
    y = y(isfinite(y));

end

function str_out = signal_last_to_string(x, num_format)

    y = signal_vector(x);

    if isempty(y)
        str_out = '';
        return;
    end

    y_last = y(end);
    str_out = sprintf(num_format, y_last);

end

function str_out = pulse_data_to_string(x, pulse_idx, pulse_num, delimiter, num_format)

    y = signal_vector(x);

    if isempty(y)
        str_out = '';
        return;
    end

    data_len = length(y);

    if data_len == pulse_num

        y_pulse = y(pulse_idx);

    else

        edges = round(linspace(0, data_len, pulse_num + 1));

        idx_start = edges(pulse_idx) + 1;
        idx_end = edges(pulse_idx + 1);

        if idx_start > idx_end
            y_pulse = [];
        else
            y_pulse = y(idx_start:idx_end);
        end

    end

    if isempty(y_pulse)
        str_out = '';
        return;
    end

    str_cell = arrayfun(@(v) sprintf(num_format, v), y_pulse, 'UniformOutput', false);
    str_out = strjoin(str_cell, delimiter);

end