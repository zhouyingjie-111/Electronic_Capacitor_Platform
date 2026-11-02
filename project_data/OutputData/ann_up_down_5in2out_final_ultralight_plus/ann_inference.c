/* Auto-generated float32 ANN inference for TI F28P550SJ9PZ C28x CPU. */
/* No NPU, no dynamic memory, no filesystem, no external ML runtime. */
#include "ann_inference.h"

static const float X_MEAN[4] = { 21.2564106f, 5.53846257e-05f, 60.0733299f, 210.07251f };
static const float X_STD[4] = { 1.97052562f, 2.3949322e-05f, 13.7666569f, 14.719821f };
static const float Y_MEAN[2] = { 0.301525652f, 0.240897417f };
static const float Y_STD[2] = { 0.219490886f, 0.146787718f };

static const float W1[6][5] = {
    { -0.0297422409f, -0.585099161f, 0.419119686f, -0.213771909f, -0.783008575f },
    { 0.0963902697f, -0.123760857f, -0.113872744f, 0.436724007f, 0.29646948f },
    { 0.14159514f, -1.43717098f, -0.0922468081f, -0.803089201f, 0.00140628102f },
    { 0.439320922f, -0.214838579f, 0.197936714f, 0.439029425f, -0.961215377f },
    { -0.242479116f, 0.58241117f, 0.307215422f, 1.10284388f, 0.373320132f },
    { -0.0830757543f, -0.963277876f, -0.486954004f, 0.453122824f, -0.231040865f }
};
static const float B1[6] = { -0.153646827f, -0.0010585587f, 0.116485238f, -0.249845684f, 0.0637746379f, -0.469077498f };

static const float W2[2][6] = {
    { -0.704359114f, 0.629454136f, 0.0238205381f, -0.600645661f, 0.0699463934f, 0.981221735f },
    { 0.134083986f, 0.59849453f, -0.554560304f, 0.189956635f, 0.44196257f, 0.151792958f }
};
static const float B2[2] = { -0.387589365f, -0.209364057f };

static float g_ann_buf_a[6];
static float g_ann_buf_b[6];

static float ann_relu(float x)
{
    return (x > 0.0f) ? x : 0.0f;
}

static void ann_build_features(float P_peak, float T_rise_fall, float VC1, float VG1, uint16_t direction_state, float x[ANN_FEATURE_DIM])
{
    x[0] = (P_peak      - X_MEAN[0]) / X_STD[0];
    x[1] = (T_rise_fall - X_MEAN[1]) / X_STD[1];
    x[2] = (VC1         - X_MEAN[2]) / X_STD[2];
    x[3] = (VG1         - X_MEAN[3]) / X_STD[3];
    x[4] = (direction_state != 0U) ? 1.0f : 0.0f;
}

void ann_predict_scaled_features(const float x_feature[ANN_FEATURE_DIM], float y_scaled[ANN_OUTPUT_DIM])
{
    uint16_t j;
    uint16_t k;
    for (j = 0U; j < ANN_FEATURE_DIM; ++j)
    {
        g_ann_buf_a[j] = x_feature[j];
    }
    for (j = 0U; j < 6U; ++j)
    {
        float sum = B1[j];
        for (k = 0U; k < 5U; ++k)
        {
            sum += W1[j][k] * g_ann_buf_a[k];
        }
        g_ann_buf_b[j] = ann_relu(sum);
    }
    for (j = 0U; j < 2U; ++j)
    {
        float sum = B2[j];
        for (k = 0U; k < 6U; ++k)
        {
            sum += W2[j][k] * g_ann_buf_b[k];
        }
        g_ann_buf_a[j] = sum;
    }
    for (j = 0U; j < ANN_OUTPUT_DIM; ++j)
    {
        y_scaled[j] = g_ann_buf_a[j];
    }
}

void ann_predict(float P_peak, float T_rise_fall, float VC1, float VG1, uint16_t direction_state, float y_out[ANN_OUTPUT_DIM])
{
    float x_feature[ANN_FEATURE_DIM];
    float y_scaled[ANN_OUTPUT_DIM];
    uint16_t j;
    ann_build_features(P_peak, T_rise_fall, VC1, VG1, direction_state, x_feature);
    ann_predict_scaled_features(x_feature, y_scaled);
    for (j = 0U; j < ANN_OUTPUT_DIM; ++j)
    {
        y_out[j] = y_scaled[j] * Y_STD[j] + Y_MEAN[j];
    }
}

void ann_predict_up(float P_peak, float T_rise_fall, float VC1_UP, float VG1_UP, float y_out[ANN_OUTPUT_DIM])
{
    ann_predict(P_peak, T_rise_fall, VC1_UP, VG1_UP, 0U, y_out);
}

void ann_predict_down(float P_peak, float T_rise_fall, float VC1_DOWN, float VG1_DOWN, float y_out[ANN_OUTPUT_DIM])
{
    ann_predict(P_peak, T_rise_fall, VC1_DOWN, VG1_DOWN, 1U, y_out);
}
