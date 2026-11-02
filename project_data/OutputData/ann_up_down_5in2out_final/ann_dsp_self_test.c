#include "ann_dsp_self_test.h"
#include "ann_inference.h"
#include "ann_test_vectors.h"

static float abs_f(float x)
{
    return (x >= 0.0f) ? x : -x;
}

float ANN_runSelfTest(void)
{
    uint16_t i;
    float y[ANN_OUTPUT_DIM];
    float max_abs_error = 0.0f;

    for (i = 0U; i < ANN_TEST_VECTOR_COUNT; ++i)
    {
        const float *x = ANN_TEST_INPUTS[i];
        ann_predict(x[0], x[1], x[2], x[3], (uint16_t)(x[4] >= 0.5f), y);
        {
            float e0 = abs_f(y[0] - ANN_TEST_EXPECTED[i][0]);
            float e1 = abs_f(y[1] - ANN_TEST_EXPECTED[i][1]);
            if (e0 > max_abs_error) max_abs_error = e0;
            if (e1 > max_abs_error) max_abs_error = e1;
        }
    }
    return max_abs_error;
}
