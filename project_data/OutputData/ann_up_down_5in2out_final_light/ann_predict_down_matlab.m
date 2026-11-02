function y = ann_predict_down_matlab(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN)
% Output order: y(1)=D_T_DOWN_given, y(2)=PWM_S_DOWN_first
y = ann_predict_matlab(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1);
end
