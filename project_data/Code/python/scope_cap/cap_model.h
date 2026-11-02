#ifndef SCOPE_CAP_MODEL_H
#define SCOPE_CAP_MODEL_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
#define CAP_WINDOW_SAMPLES 1024
#define CAP_IMAGE_FLOATS 576
#define CAP_OUTPUTS 8
/* Independent from nn_cpu_test and tvmgen_default. Foreground only,
 * synchronous, non-reentrant. Supply calibrated V,A,A at 100 kHz.
 * voltage is the PHYSICAL value; do not add the scope's 200 V export
 * correction to an already calibrated hardware voltage. */
void Cap_Reset(void);
/* 0 collecting; 1 window complete; -1 invalid sample or too many samples.
 * Feed only one valid, contiguous window; reset after loss/saturation. */
int Cap_Push(float voltage, float capacitor_current, float load_current);
/* 0 success; -1 incomplete/invalid. Returns C1..C4 [uF], ESR1..ESR4 [ohm].
 * Full preprocessing and inference are foreground operations. */
int Cap_Finish(float output[CAP_OUTPUTS]);
/* Fixed normalized grayscale HWC input, useful for PC/CCS parity tests. */
int Cap_PredictImage(const float image[CAP_IMAGE_FLOATS], float output[CAP_OUTPUTS]);
/* Optional debug path: MakeImage -> GetImage/copy -> PredictImage.
 * MakeImage consumes the completed window once; do not call Finish after it.
 * PredictImage reuses feature storage, so GetImage is NOT valid afterward. */
int Cap_MakeImage(void);
const float *Cap_GetImage(void);
#ifdef __cplusplus
}
#endif
#endif
