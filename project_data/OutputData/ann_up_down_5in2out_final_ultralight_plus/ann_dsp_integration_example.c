/* F28P550SJ9PZ C28x CPU inference example - NO NPU. */
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
