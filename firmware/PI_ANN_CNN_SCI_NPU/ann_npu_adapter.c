#include "ann_npu_adapter.h"
#include "tvmgen_default.h"
#include <math.h>
#ifndef TVMGEN_DEFAULT_TI_NPU
#error A hardware NPU model library is required.
#endif
static float input_buffer[16];
static float output_buffer[2];
static uint16_t pending=0U;
static struct tvmgen_default_inputs inputs={input_buffer};
static struct tvmgen_default_outputs outputs={output_buffer};
static const float mean[5]={2.125641060e+01f, 5.538461846e-05f, 6.007334137e+01f, 2.100725098e+02f, 0.000000000e+00f};
static const float std[5]={1.970525861e+00f, 2.394932199e-05f, 1.376665688e+01f, 1.471982193e+01f, 1.000000000e+00f};
static const float output_mean=3.051896393e-01f;
static const float output_std=2.041616887e-01f;

int16_t ann_npu_start(const float raw_input[5]) {
    uint16_t i;
    if (pending) return 1;
    if (!raw_input) return -1;
    for (i=0U;i<5U;i++) if (!isfinite(raw_input[i])) return -1;
    if (raw_input[0]<=0.0f || raw_input[1]<=0.0f) return -1;
    for (i=0U;i<4U;i++) input_buffer[i]=(raw_input[i]-mean[i])/std[i];
    input_buffer[4]=(raw_input[4]>=0.5f)?1.0f:0.0f;
    for (i=5U;i<16U;i++) input_buffer[i]=0.0f;
    for (i=0U;i<5U;i++) if (!isfinite(input_buffer[i])) return -1;
    pending=1U;
    if (tvmgen_default_run(&inputs,&outputs)!=0) { pending=0U; return -2; }
    return 0;
}
int16_t ann_npu_poll(float *dt_given) {
    float value;
    if (!dt_given) return -1;
    if (!pending || !tvmgen_default_finished) return 0;
    value=output_buffer[0]*output_std+output_mean;
    pending=0U;
    if (!isfinite(value)) return -2;
    *dt_given=value;
    return 1;
}
