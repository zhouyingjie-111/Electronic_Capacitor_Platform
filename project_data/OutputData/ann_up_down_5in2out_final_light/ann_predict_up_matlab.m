function y = ann_predict_up_matlab(P_peak, T_rise_fall, VC1_UP, VG1_UP)
% Output order: y(1)=D_T_UP_given, y(2)=PWM_S_UP_first
y = ann_predict_matlab(P_peak, T_rise_fall, VC1_UP, VG1_UP, 0);
end
