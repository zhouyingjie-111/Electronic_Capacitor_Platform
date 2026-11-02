#ifndef ANN_INFERENCE_H
#define ANN_INFERENCE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ANN_FEATURE_DIM          (5U)
#define ANN_OUTPUT_DIM           (2U)
#define ANN_MAX_HIDDEN_DIM       (48U)
#define ANN_PARAMETER_COUNT      (3866UL)

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
}
#endif

#endif /* ANN_INFERENCE_H */
