/* Auto-generated float32 ANN inference for TI F28P550SJ9PZ C28x CPU. */
/* No NPU, no dynamic memory, no filesystem, no external ML runtime. */
#include "ann_inference.h"

static const float X_MEAN[4] = { 21.2564106f, 5.53846257e-05f, 60.0733299f, 210.07251f };
static const float X_STD[4] = { 1.97052562f, 2.3949322e-05f, 13.7666569f, 14.719821f };
static const float Y_MEAN[2] = { 0.301525652f, 0.240897417f };
static const float Y_STD[2] = { 0.219490886f, 0.146787718f };

static const float W1[8][5] = {
    { 0.110185176f, -1.11738658f, 0.970104814f, 0.609255135f, -0.369484425f },
    { 0.18114455f, -0.243085265f, -0.929054379f, -0.460402161f, 0.903174222f },
    { 1.03016162f, 0.697224796f, -0.607870817f, -0.660055697f, -0.832883894f },
    { 0.0647977293f, 0.450075626f, 0.300604016f, -0.3482638f, 0.686369479f },
    { -0.0835221931f, -0.00155039504f, 0.735058606f, 0.430180788f, 0.395579755f },
    { -0.0875349939f, -0.670529068f, 0.710822642f, -0.70648706f, 0.523837745f },
    { -0.611543953f, 0.347292215f, 0.0663134009f, 0.87960279f, -0.915460527f },
    { -0.0729956701f, -0.153824985f, 0.431678504f, 0.757040858f, 1.32555807f }
};
static const float B1[8] = { 0.169670522f, 0.024894055f, 0.439039767f, 0.608344615f, -0.316621631f, -0.25575614f, -0.249773651f, 0.156242743f };

static const float W2[4][8] = {
    { 0.7496593f, -1.20210922f, 0.0243565962f, 0.118772827f, -0.382222742f, 0.751760125f, 0.239136145f, -0.702471018f },
    { -1.25543678f, 0.758232832f, -0.0287327357f, 0.491138488f, 0.0404560082f, 0.153126419f, 0.360870451f, 0.0964003801f },
    { 0.971143901f, 0.547054172f, -0.877940893f, -1.51767814f, 0.226079524f, -0.730834186f, 0.107065916f, 0.0315329209f },
    { -0.287782341f, 0.571350038f, -0.274193376f, -0.542465925f, 0.46200946f, 0.292630941f, -0.485681057f, 0.845392287f }
};
static const float B2[4] = { 0.580338001f, -0.0871899277f, -0.288689435f, -0.106211901f };

static const float W3[2][4] = {
    { -0.369764358f, -0.106168509f, 0.782584786f, 0.282500625f },
    { -0.366376847f, 0.372565478f, -0.658171058f, 0.304983228f }
};
static const float B3[2] = { 0.120610327f, -0.00931941066f };

static float g_ann_buf_a[8];
static float g_ann_buf_b[8];

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
    for (j = 0U; j < 8U; ++j)
    {
        float sum = B1[j];
        for (k = 0U; k < 5U; ++k)
        {
            sum += W1[j][k] * g_ann_buf_a[k];
        }
        g_ann_buf_b[j] = ann_relu(sum);
    }
    for (j = 0U; j < 4U; ++j)
    {
        float sum = B2[j];
        for (k = 0U; k < 8U; ++k)
        {
            sum += W2[j][k] * g_ann_buf_b[k];
        }
        g_ann_buf_a[j] = ann_relu(sum);
    }
    for (j = 0U; j < 2U; ++j)
    {
        float sum = B3[j];
        for (k = 0U; k < 4U; ++k)
        {
            sum += W3[j][k] * g_ann_buf_a[k];
        }
        g_ann_buf_b[j] = sum;
    }
    for (j = 0U; j < ANN_OUTPUT_DIM; ++j)
    {
        y_scaled[j] = g_ann_buf_b[j];
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
